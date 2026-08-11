"""
Agent chatbot routes.

Exposes the Qwen-driven agent: create a conversation, attach a video to it,
then talk. The agent decides on its own when to run detection (YOLO and the
other backends) and when to delegate to the database-insights agent — see
agentic/chat_agent.py.

Videos attached to a chat are kept on disk under ``media/chat/<user_id>/`` for
the life of the conversation, because the agent may re-analyse them with a
different model several turns later.
"""

import logging
import os
import uuid
from pathlib import Path

from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from prisma import Prisma

from middleware.auth import get_current_user
from routes.detection import ALLOWED_VIDEO_EXTENSIONS, MAX_VIDEO_SIZE_BYTES
from agentic.schemas import (
    ChatHealthResponse,
    ChatMessageIn,
    ChatSessionDetail,
    ChatSessionOut,
    ChatTurnResponse,
    ChatVideoResponse,
)
from schemas.user import UserOut
from agentic import chat_agent, chat_store, qwen_chat
from agentic.agent_tools import AgentContext, analyze_and_store, summarize_result
from services.database import get_prisma
from services.job_launcher import queue_job_from_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat Agent"])

# Chat uploads live under the /media mount so the browser can play them back.
CHAT_MEDIA_ROOT = Path("media") / "chat"


async def _require_session(prisma: Prisma, user_id: int, session_id: int) -> dict:
    """Load a session owned by the user, or 404."""
    session = await chat_store.get_session(prisma, user_id, session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
        )
    return session


@router.get("/health", response_model=ChatHealthResponse)
async def chat_health(
    current_user: UserOut = Depends(get_current_user),
) -> ChatHealthResponse:
    """Report whether the Qwen chat model is reachable."""
    return ChatHealthResponse.model_validate(qwen_chat.health())


@router.post("/sessions", response_model=ChatSessionOut, status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> ChatSessionOut:
    """Start a new conversation."""
    session = await chat_store.create_session(prisma, current_user.id)
    return ChatSessionOut.model_validate({**session, "message_count": 0})


@router.get("/sessions", response_model=list[ChatSessionOut])
async def list_chat_sessions(
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> list[ChatSessionOut]:
    """The user's conversations, most recently active first."""
    rows = await chat_store.list_sessions(prisma, current_user.id)
    return [ChatSessionOut.model_validate(row) for row in rows]


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
async def get_chat_session(
    session_id: int,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> ChatSessionDetail:
    """One conversation with its full message history."""
    session = await _require_session(prisma, current_user.id, session_id)
    messages = await chat_store.get_messages(prisma, session_id)
    return ChatSessionDetail.model_validate({**session, "messages": messages})


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    session_id: int,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> None:
    """Delete a conversation, its messages and its uploaded video."""
    session = await _require_session(prisma, current_user.id, session_id)

    video_path = session.get("video_path")
    if video_path and os.path.exists(video_path):
        try:
            os.remove(video_path)
        except OSError as e:
            logger.warning("Could not delete chat video %s: %s", video_path, e)

    await chat_store.delete_session(prisma, current_user.id, session_id)


@router.post("/sessions/{session_id}/video", response_model=ChatVideoResponse)
async def attach_chat_video(
    session_id: int,
    file: UploadFile = File(...),
    model: str = Form(
        "auto", description="Detector for the streamed analysis: auto | yolo | owlv2 | llm | qwen | action"
    ),
    queries: Optional[str] = Form(
        None, description="OWLv2 only: comma-separated objects to look for."
    ),
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> ChatVideoResponse:
    """Attach a video to a conversation so the agent can analyse it.

    Replaces any previously attached video. The file is stored under
    ``media/chat/<user_id>/`` — the agent's tools need a real path to sample
    frames from — and is served back for playback in the chat.

    The same saved file is then queued on the analysis pipeline, so the chat
    gets the live experience of the standalone analyser (progress, frames as
    they are extracted, incidents as they are found) from a single upload.
    Subscribe to the returned ``events_url`` for that stream; it is null when
    the job could not be queued, and the chat still works without it.
    """
    session = await _require_session(prisma, current_user.id, session_id)

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required"
        )
    extension = Path(file.filename).suffix.lower()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported video type '{extension}'. Allowed: "
                f"{', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}"
            ),
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded video is empty"
        )
    if len(content) > MAX_VIDEO_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video exceeds maximum size of {MAX_VIDEO_SIZE_BYTES // (1024 * 1024)} MB",
        )

    user_dir = CHAT_MEDIA_ROOT / str(current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    stored = user_dir / f"{uuid.uuid4().hex}{extension}"
    stored.write_bytes(content)

    # Drop the previous upload now that the new one is safely written.
    previous = session.get("video_path")
    if previous and previous != str(stored) and os.path.exists(previous):
        try:
            os.remove(previous)
        except OSError as e:
            logger.warning("Could not delete previous chat video %s: %s", previous, e)

    video_url = "/" + stored.as_posix()
    await chat_store.set_session_video(
        prisma, session_id, str(stored), file.filename, video_url
    )
    logger.info(
        "User %s attached %s (%s bytes) to chat session %s",
        current_user.id,
        file.filename,
        len(content),
        session_id,
    )

    # Queue the streamed analysis from the copy we just wrote — no second upload.
    job = await queue_job_from_path(
        prisma,
        current_user.id,
        stored,
        file.filename,
        model=model,
        queries=queries,
    )

    return ChatVideoResponse(
        video_name=file.filename,
        video_url=video_url,
        session_id=session_id,
        job_id=job["job_id"] if job else None,
        events_url=job["events_url"] if job else None,
        queued=bool(job and job["queued"]),
    )


@router.post("/sessions/{session_id}/analyze", response_model=ChatTurnResponse)
async def analyze_chat_video(
    session_id: int,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> ChatTurnResponse:
    """Run detection on the attached video immediately and store the findings.

    This is the auto-analyse-on-attach path: the frontend calls it right after a
    successful upload, so the user sees an incident summary without having to ask.
    It runs the detectors directly (no Qwen dependency); the tool card + summary
    are persisted to the conversation, and the chart/DB agent can answer
    follow-up questions about the stored scan.
    """
    session = await _require_session(prisma, current_user.id, session_id)
    if not session.get("video_path"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attach a video before analysing.",
        )

    ctx = AgentContext(prisma=prisma, user_id=current_user.id, session=session)
    result = await analyze_and_store(ctx, "auto")
    reply = summarize_result(result)

    await chat_store.add_message(
        prisma,
        session_id,
        "tool",
        content="analyze_video",
        tool_name="analyze_video",
        tool_payload={"arguments": {"detector": "auto"}, "result": result},
    )
    await chat_store.add_message(prisma, session_id, "assistant", reply)
    await chat_store.touch_session(prisma, session_id)

    return ChatTurnResponse.model_validate(
        {
            "reply": reply,
            "tool_calls": [
                {"name": "analyze_video", "arguments": {"detector": "auto"}, "result": result}
            ],
            "scan_id": result.get("scan_id"),
        }
    )


@router.post("/sessions/{session_id}/messages", response_model=ChatTurnResponse)
async def send_chat_message(
    session_id: int,
    payload: ChatMessageIn,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> ChatTurnResponse:
    """Send a message to the agent and get its reply.

    The response includes the trace of every tool the agent ran (video analysis,
    database queries) so the UI can show its work. A single turn can take a
    while — model inference happens inline.
    """
    session = await _require_session(prisma, current_user.id, session_id)
    result = await chat_agent.safe_run_turn(
        prisma, current_user.id, session, payload.message.strip()
    )
    return ChatTurnResponse.model_validate(result)
