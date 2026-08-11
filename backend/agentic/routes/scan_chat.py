"""
Per-analysis chat API.

Each finished scan carries its own conversation, so a user can interrogate a
specific video — "what happens at 0:14?", "who is holding the bag?", "why was
this flagged as theft?" — against the stored analysis and the extracted frames.

Endpoints live under the scan they belong to:

    GET  /detection/scans/{scan_id}/chat            history + suggested openers
    POST /detection/scans/{scan_id}/chat/messages   ask a question
    POST /detection/scans/{scan_id}/chat/incidents/{ordinal}
                                                    explain one flagged clip
    DELETE /detection/scans/{scan_id}/chat          clear the conversation

This is separate from `/chat`, the general tool-using agent that analyses an
ad-hoc upload; this one is read-only and scoped to one stored result.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Prisma
from pydantic import BaseModel, Field

from middleware.auth import get_current_user
from schemas.user import UserOut
from agentic import scan_chat
from services.database import get_prisma

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/detection/scans", tags=["Analysis Chat"])


class AskIn(BaseModel):
    """A question about one analysed video."""

    message: str = Field(min_length=1, max_length=2000)


class AskOut(BaseModel):
    """The agent's grounded answer."""

    session_id: str
    message_id: int
    answer: str
    grounded: bool = Field(
        description="False when the analysis does not cover what was asked."
    )
    citations: list[dict] = Field(
        default_factory=list,
        description="Incidents and frames the answer was drawn from.",
    )
    latency_ms: int
    source: str = Field(
        default="gemini",
        description=(
            "Which backend answered: 'gemini' (sees the frames), 'qwen' (local, "
            "text only) or 'analysis' (direct readout, no model reachable)."
        ),
    )


class ExplainIn(BaseModel):
    """An optional follow-up about one incident; omitted asks for the breakdown."""

    message: str | None = Field(default=None, max_length=2000)


class KeyFrameOut(BaseModel):
    """The single frame the agent judged decisive."""

    image_id: int
    second: float = Field(description="Where the frame sits in the source video.")
    frame_index: int | None = Field(
        default=None, description="Frame number in the source video (second × fps)."
    )
    label: str = Field(description='Human form, e.g. "0:52.30 (frame #1569)".')
    url: str | None = None


class ExplainOut(AskOut):
    """A grounded reading of one incident, with the frame that shows it."""

    question: str = Field(description="What was actually asked of the agent.")
    segment: dict = Field(description="The incident: span, label, confidence, clips.")
    key_frame: KeyFrameOut | None = None
    frames_examined: int = Field(
        default=0, description="Stills from the incident the agent could look at."
    )


async def _require_owned_scan(prisma: Prisma, user_id: int, scan_id: int) -> None:
    """404 unless this user owns the scan (scan -> job -> video -> user)."""
    scan = await prisma.scan.find_first(
        where={
            "id": scan_id,
            "job": {"is": {"video": {"is": {"userId": user_id}}}},
        }
    )
    if scan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found"
        )


@router.get("/{scan_id}/chat")
async def get_conversation(
    scan_id: int,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> dict:
    """The conversation about this scan, plus openers tailored to its findings."""
    await _require_owned_scan(prisma, current_user.id, scan_id)

    context = await scan_chat.build_context(prisma, scan_id)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found"
        )

    session = await scan_chat.get_or_create_session(prisma, scan_id)
    return {
        "session_id": session.publicId,
        "scan_id": scan_id,
        "messages": await scan_chat.history(prisma, session.id),
        "suggestions": scan_chat.suggested_questions(context),
        "frame_count": len(context["images"]),
        "incident_count": len(context["segments"]),
    }


@router.post("/{scan_id}/chat/messages", response_model=AskOut)
async def ask_question(
    scan_id: int,
    payload: AskIn,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> AskOut:
    """Ask the agent about this video.

    The prompt carries the stored analysis and the extracted frame images, so
    answers describe what the pipeline actually saw. When a question falls
    outside the analysis the agent says so and returns `grounded=false` rather
    than speculating.
    """
    await _require_owned_scan(prisma, current_user.id, scan_id)

    try:
        result = await scan_chat.ask(prisma, scan_id, payload.message.strip())
    except scan_chat.ScanChatError as exc:
        message = str(exc)
        code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if "GEMINI_API_KEY" in message
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=code, detail=message)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.error("Chat failed for scan %s: %s", scan_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The chat agent could not answer that.",
        )

    return AskOut.model_validate(result)


@router.post("/{scan_id}/chat/incidents/{ordinal}", response_model=ExplainOut)
async def explain_incident(
    scan_id: int,
    ordinal: int,
    payload: ExplainIn | None = None,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> ExplainOut:
    """Hand one flagged clip to the agent and get it read back.

    The prompt carries only that incident and a strip of stills taken across
    it, so the answer is about the moment the timeline marked red rather than
    the video as a whole. The reply names the exact frame the incident is
    clearest on, and the exchange joins this scan's conversation so it can be
    followed up in the chat panel.

    Stills missing from an older analysis are captured on the first request,
    which can take a few seconds while the video is fetched back.
    """
    await _require_owned_scan(prisma, current_user.id, scan_id)

    try:
        result = await scan_chat.analyze_segment(
            prisma, scan_id, ordinal, payload.message if payload else None
        )
    except scan_chat.ScanChatError as exc:
        message = str(exc)
        code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if "GEMINI_API_KEY" in message
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=code, detail=message)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.error(
            "Incident explain failed for scan %s ordinal %s: %s",
            scan_id, ordinal, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The agent could not read that clip.",
        )

    return ExplainOut.model_validate(result)


@router.delete("/{scan_id}/chat", status_code=status.HTTP_204_NO_CONTENT)
async def clear_conversation(
    scan_id: int,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> None:
    """Delete the conversation about this scan (the analysis itself is kept)."""
    await _require_owned_scan(prisma, current_user.id, scan_id)
    await prisma.chatsession.delete_many(where={"scanId": scan_id})
