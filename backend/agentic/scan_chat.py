"""
Per-analysis chat agent.

Every finished scan gets its own conversation. The agent answers questions about
that specific video — what happened, when, who was involved, why it was flagged
— and it is *grounded*: the prompt carries the stored analysis (summary,
incident segments with timestamps, per-frame captions) plus the actual frame
images, so the model reasons over what the pipeline saw rather than inventing a
scene.

Grounding is enforced in three ways:

  1. The system instruction forbids claims that are not supported by the
     supplied evidence, and requires "the analysis does not show that" when the
     answer is not in the data.
  2. The model returns structured JSON naming the segments and frames it used,
     which is written to `chat_citations` so the UI can show its sources.
  3. Frames are attached as real images, so questions about the *scene* ("what
     is the person on the left carrying?") are answered by looking, not
     guessing.

This is distinct from the general-purpose agent in `agentic/chat_agent.py`,
which drives detection tools over an ad-hoc upload. This one is read-only and
scoped to one stored scan.
"""

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Any, Optional

import httpx

from agentic import prompts
from config import settings
from services import incident_frames, media_store

logger = logging.getLogger(__name__)

GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# Ask for JSON so citations come back machine-readable instead of parsed out of
# prose.
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "cited_segment_ordinals": {"type": "array", "items": {"type": "integer"}},
        "cited_frame_sequences": {"type": "array", "items": {"type": "integer"}},
        "grounded": {"type": "boolean"},
    },
    "required": ["answer", "grounded"],
}

# Grounded Q&A over a whole analysed video — the rules that keep the model from
# inventing a scene. Text lives in agentic/prompts/scan_chat/system.md.
_SYSTEM_INSTRUCTION = prompts.load("scan_chat/system")


class ScanChatError(Exception):
    """Raised when the chat backend is unconfigured or unreachable."""


def _format_time(seconds: Optional[float]) -> str:
    """Seconds -> mm:ss, matching how the report labels the timeline."""
    if seconds is None:
        return "?"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


async def build_context(prisma: Any, scan_id: int) -> Optional[dict]:
    """Collect everything the agent is allowed to reason about for one scan."""
    scan = await prisma.scan.find_unique(
        where={"id": scan_id},
        include={
            "job": {"include": {"video": True, "model": True}},
            "model": True,
            "segments": {"include": {"label": True, "category": True}},
        },
    )
    if scan is None:
        return None

    video = scan.job.video
    images = await prisma.image.find_many(
        where={"videoId": video.id, "kind": {"in": ["FRAME", "THUMBNAIL"]}},
        order=[{"sequence": "asc"}],
    )

    detection_count = await prisma.detection.count(where={"scanId": scan.id})

    return {
        "scan": scan,
        "video": video,
        "segments": sorted(scan.segments or [], key=lambda s: s.ordinal),
        "images": images,
        "detection_count": detection_count,
    }


def _render_evidence(context: dict, attached: list) -> str:
    """Turn the stored analysis into the text block handed to the model.

    `attached` is the exact set of images going into the prompt, listed last so
    every picture the model can see has a timestamp and a frame number beside
    it — incident stills included, which carry no sequence number of their own.
    """
    scan = context["scan"]
    video = context["video"]
    segments = context["segments"]
    images = context["images"]

    lines = [
        "=== VIDEO ===",
        f"File: {video.originalFilename}",
        f"Duration: {_format_time(video.durationSeconds)}"
        + (f" ({video.width}x{video.height})" if video.width else ""),
        "",
        "=== ANALYSIS ===",
        f"Model: {scan.model.displayName if scan.model else 'unknown'}",
        f"Verdict: {'INCIDENT DETECTED' if scan.violenceDetected else 'no incident detected'}",
        f"Summary: {scan.summary or '(none)'}",
    ]

    if context["detection_count"]:
        lines.append(f"Object detections recorded: {context['detection_count']}")

    lines.append("")
    if segments:
        lines.append("=== INCIDENTS ===")
        for segment in segments:
            label = segment.label.name if segment.label else "Incident"
            category = segment.category.code if segment.category else "other"
            lines.append(
                f"[ordinal {segment.ordinal}] {label} ({category}) "
                f"{_format_time(segment.startTime)}–{_format_time(segment.endTime)} "
                f"confidence {segment.confidence:.0%}"
            )
            if segment.description:
                lines.append(f"    what happens: {segment.description}")
            if segment.explanation:
                lines.append(f"    involved: {segment.explanation}")
    else:
        lines.append("=== INCIDENTS ===")
        lines.append("None. The pipeline flagged nothing in this video.")

    lines.append("")
    lines.append("=== SAMPLED FRAMES ===")
    for image in images:
        if image.sequence is None:
            continue
        lines.append(
            f"[frame {image.sequence}] at {_format_time(image.capturedAtSeconds)}: "
            f"{image.caption or '(no caption)'}"
        )

    if attached:
        lines.append("")
        lines.append("=== IMAGES ATTACHED BELOW (in this order) ===")
        for image in attached:
            described = incident_frames.describe_frame(
                image.capturedAtSeconds, video.fps
            )
            marker = (
                f"[frame {image.sequence}]"
                if image.sequence is not None
                else "[incident still]"
            )
            lines.append(f"{marker} {described}")

    return "\n".join(lines)


def _by_incident(strips: list[list]) -> list:
    """Order incident stills so every incident is seen before any is seen twice.

    Within one incident the middle still leads — it is the most representative
    of the moment — and the rest follow in time order.
    """
    ordered = []
    for strip in strips:
        middle = len(strip) // 2
        ordered.append(
            [strip[middle]] + [s for index, s in enumerate(strip) if index != middle]
        )

    out = []
    for round_index in range(max((len(strip) for strip in ordered), default=0)):
        for strip in ordered:
            if round_index < len(strip):
                out.append(strip[round_index])
    return out


def _select_frames(images: list, limit: int) -> list:
    """Choose which stills to show the model for a whole-video question.

    Each incident contributes one still before any contributes a second, so
    every flagged moment is seen; the timeline then fills the remaining slots so
    questions about the rest of the video stay answerable; anything still spare
    goes back to the incidents. Without that ordering a single incident's strip
    would eat the whole budget and the model would be blind to the other 55
    seconds of a minute-long video.
    """
    incident = [image for image in images if image.segmentId is not None]
    other = [image for image in images if image.segmentId is None]

    by_segment: dict[int, list] = {}
    for image in incident:
        by_segment.setdefault(image.segmentId, []).append(image)
    priority = _by_incident(list(by_segment.values()))

    in_time_order = lambda picked: sorted(  # noqa: E731 - sort key, not a function
        picked, key=lambda image: image.capturedAtSeconds or 0.0
    )
    if not other:
        return in_time_order(priority[:limit])

    # One per incident, then the timeline, then back to the incident strips.
    chosen = priority[: min(len(by_segment), limit)]
    if len(chosen) < limit:
        step = max(len(other) // max(limit - len(chosen), 1), 1)
        chosen = chosen + other[::step][: limit - len(chosen)]
    if len(chosen) < limit:
        picked = {image.id for image in chosen}
        chosen = chosen + [i for i in priority if i.id not in picked][: limit - len(chosen)]

    return in_time_order(chosen)


async def _frame_parts(chosen: list) -> list[dict]:
    """Inline the chosen frames as image parts for the model."""
    parts: list[dict] = []
    for image in chosen:
        try:
            data = await media_store.get_bytes(image.objectKey)
            parts.append(
                {
                    "inline_data": {
                        "mime_type": image.mimeType or "image/jpeg",
                        "data": base64.b64encode(data).decode("ascii"),
                    }
                }
            )
        except Exception as exc:
            logger.debug("Could not attach frame %s: %s", image.id, exc)

    return parts


def _call_gemini(payload: dict) -> dict:
    """Blocking call to Gemini; run it off the event loop."""
    url = GENERATE_URL.format(model=settings.chat_model)
    response = httpx.post(
        url,
        headers={"x-goog-api-key": settings.gemini_api_key},
        json=payload,
        timeout=180.0,
    )
    if response.status_code != 200:
        raise ScanChatError(
            f"Chat model error {response.status_code}: {response.text[:250]}"
        )
    return response.json()


def _extract_text(body: dict) -> str:
    """Pull the model's text out of a generateContent response."""
    try:
        return body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ScanChatError("The chat model returned an empty response.") from exc


async def get_or_create_session(prisma: Any, scan_id: int) -> Any:
    """The scan's conversation, created on first use."""
    session = await prisma.chatsession.find_first(
        where={"scanId": scan_id}, order={"id": "asc"}
    )
    if session is not None:
        return session

    return await prisma.chatsession.create(
        data={
            "publicId": str(uuid.uuid4()),
            "scanId": scan_id,
            "title": "Analysis discussion",
        }
    )


async def history(prisma: Any, session_id: int, limit: int = 100) -> list[dict]:
    """Every turn in a conversation, oldest first, with its citations."""
    messages = await prisma.chatmessage.find_many(
        where={"sessionId": session_id},
        include={"citations": True},
        order={"ordinal": "asc"},
        take=limit,
    )
    return [
        {
            "id": message.id,
            "ordinal": message.ordinal,
            "role": message.role.lower(),
            "content": message.content,
            "latency_ms": message.latencyMs,
            "created_at": message.createdAt.isoformat() if message.createdAt else None,
            "citations": [
                {"segment_id": c.segmentId, "image_id": c.imageId}
                for c in (message.citations or [])
            ],
        }
        for message in messages
    ]


async def ask(prisma: Any, scan_id: int, question: str) -> dict:
    """Answer one question about a scan and record the exchange.

    Args:
        prisma: Database client.
        scan_id: The analysis being discussed.
        question: The user's message.

    Returns:
        {answer, grounded, citations, latency_ms, source, session_id, message_id}
        where `source` is "gemini", "qwen" or "analysis" — see `_generate`.

    Raises:
        ValueError: If the scan does not exist.
    """
    context = await build_context(prisma, scan_id)
    if context is None:
        raise ValueError("Scan not found")

    session = await get_or_create_session(prisma, scan_id)

    last = await prisma.chatmessage.find_first(
        where={"sessionId": session.id}, order={"ordinal": "desc"}
    )
    next_ordinal = (last.ordinal + 1) if last else 0

    # Replay a bounded slice of history so long conversations stay affordable.
    # The question itself is written only once an answer exists, so a failed
    # turn cannot leave a user message stranded without a reply.
    previous = await prisma.chatmessage.find_many(
        where={"sessionId": session.id, "ordinal": {"lt": next_ordinal}},
        order={"ordinal": "desc"},
        take=settings.chat_history_turns,
    )
    previous = list(reversed(previous))

    attached = _select_frames(
        [image for image in context["images"] if image.objectKey],
        settings.chat_max_frames,
    )
    evidence = _render_evidence(context, attached)
    frame_parts = await _frame_parts(attached)

    contents: list[dict] = [
        {
            "role": "user",
            "parts": [
                {"text": f"Here is the analysis of the video we are discussing.\n\n{evidence}"},
                *frame_parts,
            ],
        },
        {
            "role": "model",
            "parts": [{"text": "Understood. I will answer only from this analysis and these frames."}],
        },
    ]
    for message in previous:
        contents.append(
            {
                "role": "user" if message.role == "USER" else "model",
                "parts": [{"text": message.content}],
            }
        )
    contents.append({"role": "user", "parts": [{"text": question}]})

    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_INSTRUCTION}]},
        "contents": contents,
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": _RESPONSE_SCHEMA,
            "temperature": 0.2,
        },
    }

    started = time.perf_counter()
    parsed, source = await _generate(payload, evidence, previous, question)
    latency_ms = int((time.perf_counter() - started) * 1000)

    answer = str(parsed.get("answer") or "").strip() or "I could not answer that."
    grounded = bool(parsed.get("grounded", True))

    await prisma.chatmessage.create(
        data={
            "sessionId": session.id,
            "ordinal": next_ordinal,
            "role": "USER",
            "content": question,
        }
    )
    assistant = await prisma.chatmessage.create(
        data={
            "sessionId": session.id,
            "ordinal": next_ordinal + 1,
            "role": "ASSISTANT",
            "content": answer,
            "modelId": context["scan"].modelId,
            "latencyMs": latency_ms,
        }
    )

    citations = await _write_citations(prisma, context, assistant.id, parsed)

    await prisma.chatsession.update(
        where={"id": session.id},
        data={"lastMessageAt": assistant.createdAt},
    )

    return {
        "session_id": session.publicId,
        "message_id": assistant.id,
        "answer": answer,
        "grounded": grounded,
        "citations": citations,
        "latency_ms": latency_ms,
        "source": source,
    }


async def _generate(
    payload: dict,
    evidence: str,
    previous: list,
    question: str,
    instruction: str = _SYSTEM_INSTRUCTION,
    fallback: Optional[str] = None,
) -> tuple[dict, str]:
    """Produce an answer, degrading through three backends.

    Gemini is preferred — it is the only one that can look at the frames. When
    it is unconfigured or out of quota the local Qwen server takes over on the
    text evidence alone, and if that is offline too the question is answered
    directly from the stored analysis. The last tier never fails, so the chat
    panel is never a dead end; the returned `source` says which tier answered.

    `instruction` and `fallback` let a caller reasoning about something narrower
    than the whole scan — one incident, say — keep its framing through every
    tier instead of dropping back to a whole-video readout.
    """
    if settings.gemini_api_key:
        try:
            body = await asyncio.to_thread(_call_gemini, payload)
            try:
                return json.loads(_extract_text(body)), "gemini"
            except json.JSONDecodeError:
                return {"answer": _extract_text(body), "grounded": True}, "gemini"
        except Exception as exc:
            logger.info("Gemini chat unavailable, falling back: %s", str(exc)[:160])

    try:
        from agentic import qwen_chat

        if (await asyncio.to_thread(qwen_chat.health)).get("online"):
            messages = [
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": f"Analysis of the video under discussion:\n\n{evidence}",
                },
            ]
            for message in previous:
                messages.append(
                    {
                        "role": "user" if message.role == "USER" else "assistant",
                        "content": message.content,
                    }
                )
            messages.append({"role": "user", "content": question})

            reply = await asyncio.to_thread(qwen_chat.chat, messages, None, 0.2, 700)
            if reply.content.strip():
                return {"answer": reply.content.strip(), "grounded": True}, "qwen"
    except Exception as exc:
        logger.info("Qwen chat unavailable, falling back: %s", str(exc)[:160])

    return {"answer": fallback or _extractive_answer(evidence), "grounded": True}, "analysis"


def _extractive_answer(evidence: str) -> str:
    """Read the stored analysis back when no language model is reachable.

    Deliberately not an attempt to sound like a model: it states plainly that it
    is a direct readout, so a fallback answer is never mistaken for reasoning.
    """
    incidents = []
    summary = ""
    for line in evidence.splitlines():
        if line.startswith("Summary: "):
            summary = line[len("Summary: ") :]
        elif line.startswith("[ordinal "):
            incidents.append(line.split("] ", 1)[-1])
        elif line.startswith("    what happens: "):
            incidents.append("  " + line.strip())

    parts = [
        "No language model is currently reachable, so here is the stored "
        "analysis for this video verbatim:",
        "",
        summary or "(no summary recorded)",
    ]
    if incidents:
        parts += ["", "Incidents:", *(f"• {line}" for line in incidents)]
    parts += [
        "",
        "Set GEMINI_API_KEY (or start the local Qwen server) to ask follow-up "
        "questions about the scene.",
    ]
    return "\n".join(parts)


async def _write_citations(
    prisma: Any, context: dict, message_id: int, parsed: dict
) -> list[dict]:
    """Record which segments and frames an answer leaned on."""
    citations: list[dict] = []

    by_ordinal = {segment.ordinal: segment for segment in context["segments"]}
    for ordinal in parsed.get("cited_segment_ordinals") or []:
        segment = by_ordinal.get(int(ordinal)) if str(ordinal).lstrip("-").isdigit() else None
        if segment is None:
            continue
        await prisma.chatcitation.create(
            data={"messageId": message_id, "segmentId": segment.id}
        )
        citations.append(
            {
                "segment_id": segment.id,
                "ordinal": segment.ordinal,
                "label": segment.label.name if segment.label else "Incident",
                "start_time": segment.startTime,
                "end_time": segment.endTime,
            }
        )

    by_sequence = {
        image.sequence: image for image in context["images"] if image.sequence is not None
    }
    for sequence in parsed.get("cited_frame_sequences") or []:
        image = by_sequence.get(int(sequence)) if str(sequence).lstrip("-").isdigit() else None
        if image is None:
            continue
        await prisma.chatcitation.create(
            data={"messageId": message_id, "imageId": image.id}
        )
        citations.append(
            {
                "image_id": image.id,
                "sequence": image.sequence,
                "captured_at_seconds": image.capturedAtSeconds,
                "url": await media_store.resolve_media_url(image.objectKey),
            }
        )

    return citations


# --------------------------------------------------------------------------- #
# One incident, examined on its own
# --------------------------------------------------------------------------- #

# Same contract as the general answer, plus the still the agent considers
# decisive — that key is what turns "around 0:52" into an exact frame.
_SEGMENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "key_frame": {
            "type": "integer",
            "description": "Key of the attached still that best shows the incident.",
        },
        "grounded": {"type": "boolean"},
    },
    "required": ["answer", "grounded"],
}

# Reading one flagged incident from its strip of stills.
# Text lives in agentic/prompts/scan_chat/segment.md.
_SEGMENT_INSTRUCTION = prompts.load("scan_chat/segment")


async def build_segment_context(prisma: Any, scan_id: int, ordinal: int) -> Optional[dict]:
    """Everything about one flagged incident, with its stills guaranteed.

    Frames missing from an older scan are captured here rather than left out,
    so an incident is always discussed from a strip of the moment itself.
    """
    scan = await prisma.scan.find_unique(
        where={"id": scan_id},
        include={
            "job": {"include": {"video": True, "model": True}},
            "model": True,
            "segments": {"include": {"label": True, "category": True}},
        },
    )
    if scan is None:
        return None

    segment = next((s for s in (scan.segments or []) if s.ordinal == ordinal), None)
    if segment is None:
        return None

    video = scan.job.video
    frames = await incident_frames.ensure_incident_frames(prisma, video, segment)

    return {
        "scan": scan,
        "video": video,
        "segment": segment,
        "frames": [f for f in frames if f.capturedAtSeconds is not None],
        "siblings": sorted(scan.segments or [], key=lambda s: s.ordinal),
    }


def _segment_stills(context: dict, limit: int) -> list[dict]:
    """The stills to attach, numbered so the model can point at one.

    A long incident can hold more stills than the prompt has room for. They are
    thinned evenly — keeping the two ends, which bracket the action — before
    they are numbered, so every key the model is offered corresponds to an image
    it was actually shown.
    """
    frames = context["frames"]
    if limit > 0 and len(frames) > limit:
        step = (len(frames) - 1) / (limit - 1) if limit > 1 else 0
        frames = [frames[round(index * step)] for index in range(limit)]

    fps = context["video"].fps
    return [
        {
            "key": index,
            "image": image,
            "second": round(float(image.capturedAtSeconds), 2),
            "frame_index": incident_frames.frame_index(image.capturedAtSeconds, fps),
            "described": incident_frames.describe_frame(image.capturedAtSeconds, fps),
        }
        for index, image in enumerate(frames)
    ]


def _render_segment_evidence(context: dict, stills: list[dict]) -> str:
    """The incident, its place in the video, and the strip covering it."""
    scan = context["scan"]
    video = context["video"]
    segment = context["segment"]

    label = segment.label.name if segment.label else "Incident"
    category = segment.category.code if segment.category else "other"
    duration = max(segment.endTime - segment.startTime, 0.0)

    lines = [
        "=== INCIDENT UNDER DISCUSSION ===",
        f"Label: {label} ({category})",
        f"Flagged span: {_format_time(segment.startTime)}–{_format_time(segment.endTime)} "
        f"({duration:.2f}s long)",
        f"Confidence: {segment.confidence:.0%}",
        f"Detected by: {scan.model.displayName if scan.model else 'unknown model'}",
    ]
    if segment.description:
        lines.append(f"Pipeline description: {segment.description}")
    if segment.explanation:
        lines.append(f"Objects involved: {segment.explanation}")

    lines += [
        "",
        "=== SOURCE VIDEO ===",
        f"File: {video.originalFilename}",
        f"Duration: {_format_time(video.durationSeconds)}"
        + (f" at {video.fps:g} fps" if video.fps else " (frame rate unknown)"),
    ]
    others = [s for s in context["siblings"] if s.id != segment.id]
    if others:
        lines.append(
            "Other flagged spans in the same video: "
            + ", ".join(
                f"{_format_time(s.startTime)}–{_format_time(s.endTime)}" for s in others
            )
        )
    else:
        lines.append("This is the only flagged span in the video.")

    lines += ["", "=== STILLS ACROSS THIS INCIDENT (attached in this order) ==="]
    if stills:
        for still in stills:
            lines.append(f"[key {still['key']}] {still['described']}")
    else:
        lines.append("None could be extracted — reason from the incident data alone.")

    return "\n".join(lines)


async def analyze_segment(
    prisma: Any, scan_id: int, ordinal: int, question: Optional[str] = None
) -> dict:
    """Explain one flagged incident and point at the frame that proves it.

    Args:
        prisma: Database client.
        scan_id: The analysis the incident belongs to.
        ordinal: The incident's position within that scan.
        question: An optional follow-up; without one the agent gives its
            standard three-part breakdown of the clip.

    Returns:
        The `ask` payload plus `segment` (label, span, confidence, clip URLs)
        and `key_frame` — the still the agent judged decisive, with its
        timestamp, frame number and image URL.

    Raises:
        ValueError: If the scan or the incident does not exist.
    """
    context = await build_segment_context(prisma, scan_id, ordinal)
    if context is None:
        raise ValueError("Incident not found")

    segment = context["segment"]
    stills = _segment_stills(context, settings.chat_max_frames)
    evidence = _render_segment_evidence(context, stills)

    parts: list[dict] = [{"text": evidence}]
    attached: list[dict] = []
    for still in stills:
        try:
            data = await media_store.get_bytes(still["image"].objectKey)
        except Exception as exc:
            logger.debug("Could not attach still %s: %s", still["image"].id, exc)
            continue
        parts.append(
            {
                "inline_data": {
                    "mime_type": still["image"].mimeType or "image/jpeg",
                    "data": base64.b64encode(data).decode("ascii"),
                }
            }
        )
        attached.append(still)

    label = segment.label.name if segment.label else "Incident"
    span = f"{_format_time(segment.startTime)}–{_format_time(segment.endTime)}"
    asked = (question or "").strip() or (
        f"Explain the {label.lower()} flagged at {span}: what happens, which "
        f"frame shows it, and does the footage support the label?"
    )

    payload = {
        "systemInstruction": {"parts": [{"text": _SEGMENT_INSTRUCTION}]},
        "contents": [
            {"role": "user", "parts": parts},
            {
                "role": "model",
                "parts": [
                    {"text": "Understood. I will describe only what these stills show."}
                ],
            },
            {"role": "user", "parts": [{"text": asked}]},
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": _SEGMENT_RESPONSE_SCHEMA,
            "temperature": 0.2,
        },
    }

    session = await get_or_create_session(prisma, scan_id)
    last = await prisma.chatmessage.find_first(
        where={"sessionId": session.id}, order={"ordinal": "desc"}
    )
    next_ordinal = (last.ordinal + 1) if last else 0

    started = time.perf_counter()
    parsed, source = await _generate(
        payload,
        evidence,
        [],
        asked,
        instruction=_SEGMENT_INSTRUCTION,
        fallback=_segment_fallback(context, stills),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    answer = str(parsed.get("answer") or "").strip() or "I could not read that clip."
    grounded = bool(parsed.get("grounded", True))

    # The agent's pick, the middle still otherwise: an answer about an incident
    # should always come with a picture of it.
    key_still = _resolve_key_frame(parsed.get("key_frame"), attached or stills)

    await prisma.chatmessage.create(
        data={
            "sessionId": session.id,
            "ordinal": next_ordinal,
            "role": "USER",
            "content": asked,
        }
    )
    assistant = await prisma.chatmessage.create(
        data={
            "sessionId": session.id,
            "ordinal": next_ordinal + 1,
            "role": "ASSISTANT",
            "content": answer,
            "modelId": context["scan"].modelId,
            "latencyMs": latency_ms,
        }
    )

    citations = [
        {
            "segment_id": segment.id,
            "ordinal": segment.ordinal,
            "label": label,
            "start_time": segment.startTime,
            "end_time": segment.endTime,
        }
    ]
    await prisma.chatcitation.create(
        data={"messageId": assistant.id, "segmentId": segment.id}
    )
    if key_still is not None:
        await prisma.chatcitation.create(
            data={"messageId": assistant.id, "imageId": key_still["image"].id}
        )
        citations.append(
            {
                "image_id": key_still["image"].id,
                "captured_at_seconds": key_still["second"],
                "url": await media_store.resolve_media_url(key_still["image"].objectKey),
            }
        )

    await prisma.chatsession.update(
        where={"id": session.id}, data={"lastMessageAt": assistant.createdAt}
    )

    return {
        "session_id": session.publicId,
        "message_id": assistant.id,
        "question": asked,
        "answer": answer,
        "grounded": grounded,
        "citations": citations,
        "latency_ms": latency_ms,
        "source": source,
        "segment": {
            "id": segment.id,
            "ordinal": segment.ordinal,
            "label": label,
            "category": segment.category.code if segment.category else "other",
            "start_time": segment.startTime,
            "end_time": segment.endTime,
            "confidence": segment.confidence,
            "clip_url": await media_store.resolve_media_url(segment.clipObjectKey),
            "annotated_clip_url": await media_store.resolve_media_url(
                segment.annotatedClipObjectKey
            ),
        },
        "key_frame": (
            {
                "image_id": key_still["image"].id,
                "second": key_still["second"],
                "frame_index": key_still["frame_index"],
                "label": key_still["described"],
                "url": await media_store.resolve_media_url(key_still["image"].objectKey),
            }
            if key_still is not None
            else None
        ),
        "frames_examined": len(attached),
    }


def _segment_fallback(context: dict, stills: list[dict]) -> str:
    """Read the incident back when no language model is reachable.

    The frames and timings are ours, not the model's, so this still answers
    "when exactly?" — only the description of what is visible is missing, and
    it says so rather than pretending otherwise.
    """
    segment = context["segment"]
    label = segment.label.name if segment.label else "Incident"
    middle = stills[len(stills) // 2] if stills else None

    lines = [
        "No language model is currently reachable, so here is what the pipeline "
        "recorded for this incident:",
        "",
        f"What was flagged: {label} "
        f"({segment.category.code if segment.category else 'other'}), "
        f"{_format_time(segment.startTime)}–{_format_time(segment.endTime)}, "
        f"{segment.confidence:.0%} confidence.",
    ]
    if segment.description:
        lines.append(f"Description: {segment.description}")
    if segment.explanation:
        lines.append(f"Objects involved: {segment.explanation}")
    if middle is not None:
        lines.append(f"Middle of the incident: {middle['described']}.")
        lines.append(f"Stills captured across it: {len(stills)}.")
    lines += [
        "",
        "Set GEMINI_API_KEY (or start the local Qwen server) for a description "
        "of what these frames actually show.",
    ]
    return "\n".join(lines)


def _resolve_key_frame(chosen: Any, stills: list[dict]) -> Optional[dict]:
    """The still the agent named, or the middle of the strip as a fallback."""
    if not stills:
        return None
    try:
        key = int(chosen)
    except (TypeError, ValueError):
        key = None
    if key is not None:
        for still in stills:
            if still["key"] == key:
                return still
    return stills[len(stills) // 2]


def suggested_questions(context: dict) -> list[str]:
    """Opening prompts tailored to what the analysis actually found."""
    segments = context["segments"]

    if not segments:
        # Object-detector runs produce boxes rather than timed incidents, so
        # ask about the objects instead of about "the incident".
        if context["detection_count"]:
            return [
                "What objects were detected, and how confident is the model?",
                "When does the weapon first appear?",
                "Describe what is happening in these frames.",
                "Could any of these detections be a false positive?",
            ]
        return [
            "Why was this video marked as clear?",
            "What is happening in this video?",
            "Were there any people or vehicles in the scene?",
        ]

    first = segments[0]
    label = first.label.name if first.label else "the incident"
    return [
        f"What exactly happens during {label}?",
        f"Who is involved at {_format_time(first.startTime)}?",
        "How confident is the model, and why?",
        "Summarise the whole video in three sentences.",
    ]
