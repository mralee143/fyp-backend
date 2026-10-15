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

import asyncio
import json
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
from fastapi.responses import StreamingResponse
from prisma import Prisma

from middleware.auth import get_current_user
from routes.detection import ALLOWED_VIDEO_EXTENSIONS, MAX_VIDEO_SIZE_BYTES
from agentic.schemas import (
    ChatAnalyzeIn,
    ChatHealthResponse,
    ChatMessageIn,
    ChatSessionDetail,
    ChatSessionOut,
    ChatTurnResponse,
    ChatVideoResponse,
)
from schemas.user import UserOut
from agentic import chat_agent, chat_store, qwen_chat
from agentic.agent_tools import (
    DETECTORS,
    AgentContext,
    analyze_and_store,
    read_stored_scan,
    summarize_result,
)
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
    """One conversation with its full message history.

    Carries the live state of the session's analysis job when one is recorded,
    so a chat reopened after a reload can tell "still running" apart from
    "finished" — the distinction the browser used to hold on its own, and lose
    the moment you navigated away.
    """
    session = await _require_session(prisma, current_user.id, session_id)
    messages = await chat_store.get_messages(prisma, session_id)

    job_status: Optional[str] = None
    job_progress: Optional[int] = None
    if session.get("job_id"):
        job = await prisma.analysisjob.find_unique(
            where={"publicId": session["job_id"]}
        )
        if job:
            job_status = job.status
            job_progress = job.progress

    return ChatSessionDetail.model_validate(
        {
            **session,
            "messages": messages,
            "job_status": job_status,
            "job_progress": job_progress,
        }
    )


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

    # Persist the job handle. Returning it only in the response would leave the
    # link between chat and run in one browser tab, so navigating away would
    # lose the analysis entirely — the run continues, but nothing can find it.
    await chat_store.set_session_job(prisma, session_id, job["job_id"] if job else None)

    if not job:
        # Nothing was queued, so no worker will ever write a result here. Say so
        # in the conversation rather than leaving the UI on a spinner that can
        # never resolve.
        await chat_store.add_message(
            prisma,
            session_id,
            "assistant",
            "I couldn't queue the analysis for this video. The upload is saved — "
            "ask me to analyse it and I'll run the detectors directly.",
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
    payload: Optional[ChatAnalyzeIn] = None,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> ChatTurnResponse:
    """Write the attached video's findings into the conversation.

    One upload is analysed exactly once. Normally the streamed job queued by
    ``attach_chat_video`` does that work, and the frontend passes back the
    ``scan_id`` it produced — this endpoint then only turns those stored rows
    into the tool card and the written summary.

    Detection runs here only as a fallback: no ``scan_id`` (no worker picked the
    job up, or it failed), in which case the detectors run inline with the model
    the user chose. Either way the tool card + summary are persisted to the
    conversation, so the chat/DB agent can answer follow-ups about the scan.
    """
    session = await _require_session(prisma, current_user.id, session_id)
    if not session.get("video_path"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attach a video before analysing.",
        )

    payload = payload or ChatAnalyzeIn()
    ctx = AgentContext(prisma=prisma, user_id=current_user.id, session=session)

    result = None
    if payload.scan_id:
        result = await read_stored_scan(ctx, payload.scan_id)
        if result is None:
            logger.warning(
                "chat session %s: scan %s not readable, analysing inline instead",
                session_id,
                payload.scan_id,
            )

    if result is None:
        # The chat's own detectors don't cover every code the pipeline offers
        # (Gemini has no inline path), so anything unknown falls back to the
        # auto cascade rather than erroring.
        detector = (payload.detector or "auto").lower().strip()
        if detector not in DETECTORS:
            detector = "auto"
        args = {"queries": payload.queries} if payload.queries else None
        result = await analyze_and_store(ctx, detector, args)

    reply = summarize_result(result)
    arguments = {"detector": result.get("detector") or "auto"}

    await chat_store.add_message(
        prisma,
        session_id,
        "tool",
        content="analyze_video",
        tool_name="analyze_video",
        tool_payload={"arguments": arguments, "result": result},
    )
    await chat_store.add_message(prisma, session_id, "assistant", reply)
    await chat_store.touch_session(prisma, session_id)

    return ChatTurnResponse.model_validate(
        {
            "reply": reply,
            "tool_calls": [
                {"name": "analyze_video", "arguments": arguments, "result": result}
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


# How long the stream may sit silent before a comment frame goes out. Prompt
# evaluation on CPU runs for minutes without producing a token, and an idle
# connection is what proxies and browsers eventually give up on.
SSE_KEEPALIVE_S = 15.0


def _sse(event: dict) -> str:
    """Render one event as a Server-Sent Events frame."""
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@router.post("/sessions/{session_id}/messages/stream")
async def stream_chat_message(
    session_id: int,
    payload: ChatMessageIn,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> StreamingResponse:
    """Send a message and watch the agent's reply as it is written.

    Exactly the turn ``POST /messages`` runs — the same tools, the same text,
    the same rows persisted — delivered as Server-Sent Events instead of one
    response at the end. On local weights a turn takes minutes, nearly all of
    it after the first token, so this is the difference between a reply that
    appears as it is composed and a spinner that sits still until it is done.

    Event frames, each a JSON object on a ``data:`` line:

    - ``{"type": "delta", "text": ...}`` — more of the reply.
    - ``{"type": "reset"}`` — discard the text streamed so far; it turned out to
      be the preamble of a tool call, or the backend died mid-sentence.
    - ``{"type": "tool_started", "name": ...}`` — a tool is running now.
    - ``{"type": "tool", "name", "arguments", "result"}`` — a finished tool call
      and the card for it.
    - ``{"type": "done", "result": {...}}`` — last frame, carrying the
      authoritative reply. Take the transcript's final text from here: deltas
      are a live preview, this is the cleaned, persisted version.
    """
    session = await _require_session(prisma, current_user.id, session_id)
    message = payload.message.strip()

    async def events():
        # The turn runs in its own task feeding a queue, so the response can
        # emit a keepalive while the model is still silent. Racing the
        # generator directly would mean cancelling it on every quiet interval.
        queue: asyncio.Queue = asyncio.Queue()

        async def produce() -> None:
            try:
                async for event in chat_agent.safe_stream_turn(
                    prisma, current_user.id, session, message
                ):
                    await queue.put(event)
            except Exception as e:  # noqa: BLE001 - the stream must still close cleanly
                logger.exception("chat stream failed for session %s", session_id)
                await queue.put({"type": "reset"})
                await queue.put(
                    {
                        "type": "done",
                        "result": {
                            "reply": f"That turn failed: {e}",
                            "tool_calls": [],
                            "scan_id": session.get("last_scan_id"),
                            "error": True,
                        },
                    }
                )
            finally:
                await queue.put(None)

        task = asyncio.create_task(produce())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield _sse(event)
        finally:
            # The client hung up, or we are done — either way the turn should
            # not keep generating into a queue nobody reads.
            task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx buffers proxied responses by default, which would hold every
            # token back until the turn ended — exactly what this endpoint
            # exists to prevent.
            "X-Accel-Buffering": "no",
        },
    )
