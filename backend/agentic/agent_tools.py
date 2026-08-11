"""
Tools the chat agent can call.

Each entry pairs an OpenAI-style JSON schema (what Qwen sees) with an async
executor (what actually runs). Two families:

  * **Vision** — ``analyze_video`` hands the session's uploaded video to the
    detection services (YOLO by default, plus the OWLv2/action/Qwen-VL/auto
    backends) and persists the scan.
  * **Data** — ``query_detection_database`` delegates to the DB insights agent
    (agentic/db_agent.py), which writes and runs scoped SQL over past scans
    and their per-frame detections.
  * **Presentation** — ``control_video_playback`` drives the browser's player,
    and ``show_frame_at`` captures the still at one timestamp, so the agent can
    show the exact frame an incident happens on instead of just naming a time.

Executors return plain dicts. The orchestrator serialises them back into the
conversation as ``tool`` messages, so keep them small and self-describing.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi.concurrency import run_in_threadpool

from agentic import chat_store, db_agent
from config import settings
from services.incident_merge import consolidate
from services.frame_extract import (
    annotate_frame,
    describe_location,
    extract_frame,
    extract_frames_for_moments,
    localize_frame,
)
from ml.vid_img import (
    MAX_INCIDENT_COVERAGE,
    focus_on_peak,
    format_seconds,
    video_duration,
)
from services.scan_store import get_history, get_scan, save_scan

logger = logging.getLogger(__name__)

# How many raw detections/segments we echo back to the model. The full result
# is persisted to the scan, so the UI can still show everything.
MAX_ECHOED_ITEMS = 15

# Detection backends the agent may pick, mapped to a human description used in
# the tool schema so Qwen chooses sensibly.
DETECTORS = {
    "yolo": "Fine-tuned YOLOv8 weapon detector. Fast, precise on Gun/Knife/Grenade/Explosive, gives per-frame boxes and timestamps. The default choice.",
    "owlv2": "Zero-shot open-vocabulary detector; needs `queries` (e.g. 'a backpack,a helmet'). Use when asked about objects YOLO wasn't trained on.",
    "action": "Local action-recognition model. Recognises behaviour over time (fighting, robbery, assault) rather than objects.",
    "qwen": "Local Qwen2.5-VL vision model. Describes incidents in natural language with timestamps. Thorough but slow (minutes).",
    "auto": "Cascade of every model, returning the first incident found. Best coverage, slowest.",
}


# Playback commands the agent may issue. These have no server-side effect —
# the executor validates them and the browser's video panel carries them out.
PLAYBACK_ACTIONS = ("play", "pause", "replay", "seek", "play_segment")


@dataclass
class AgentContext:
    """Everything a tool needs about the caller and the current session."""

    prisma: Any
    user_id: int
    session: dict

    @property
    def session_id(self) -> int:
        return int(self.session["id"])

    @property
    def video_path(self) -> Optional[str]:
        return self.session.get("video_path")

    @property
    def video_name(self) -> Optional[str]:
        return self.session.get("video_name")

    @property
    def last_scan_id(self) -> Optional[int]:
        return self.session.get("last_scan_id")


# --------------------------------------------------------------------------
# Tool schemas (what the model sees)
# --------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "analyze_video",
            "description": (
                "Run threat detection on the video the user uploaded to this "
                "chat, and store the scan. Every upload is ALREADY analysed "
                "automatically on arrival, so do not call this to answer "
                "'what is in my video' — those findings are in the conversation "
                "and this tool will just hand them back. Call it only when the "
                "user explicitly asks to re-scan or names a different model, "
                "and set force_rescan=true when they do."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "detector": {
                        "type": "string",
                        "enum": list(DETECTORS),
                        "description": " | ".join(
                            f"{name}: {desc}" for name, desc in DETECTORS.items()
                        ),
                    },
                    "force_rescan": {
                        "type": "boolean",
                        "description": (
                            "True only when the user explicitly asked for the "
                            "video to be analysed again. Otherwise the stored "
                            "findings are returned without re-running anything."
                        ),
                    },
                    "queries": {
                        "type": "string",
                        "description": "owlv2 only: comma-separated objects to look for.",
                    },
                    "num_frames": {
                        "type": "integer",
                        "description": "Frames to sample across the video (1-128). Default 24.",
                    },
                    "threshold": {
                        "type": "number",
                        "description": "Minimum confidence to keep, 0..1. Default 0.25.",
                    },
                },
                "required": ["detector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_detection_database",
            "description": (
                "Ask the database-analyst agent a natural-language question "
                "about the user's stored detections — across all their videos "
                "or about specific frames of one. It writes and runs SQL over "
                "scans, per-frame object detections and incident segments, then "
                "returns rows plus an insight. Use for questions like 'how many "
                "guns did we see this week', 'which second had the highest "
                "confidence', 'compare this video with my earlier ones'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question, in plain English, with any needed context.",
                    },
                    "scope_to_current_video": {
                        "type": "boolean",
                        "description": "True to restrict the query to the video in this chat.",
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_video_playback",
            "description": (
                "Drive the video player in the user's review panel. Use this to "
                "SHOW the user what you are describing instead of only naming a "
                "timestamp: after finding an incident, call this with "
                "action='play_segment' and its start/end so they watch exactly "
                "that moment. Also use it whenever they ask you to play, pause, "
                "replay, rewind or jump to a point in the video."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(PLAYBACK_ACTIONS),
                        "description": (
                            "play: resume | pause: stop | replay: restart from 0 "
                            "| seek: jump to start_time and play | play_segment: "
                            "play start_time..end_time then pause"
                        ),
                    },
                    "start_time": {
                        "type": "number",
                        "description": "Seconds from the start. Required for seek and play_segment.",
                    },
                    "end_time": {
                        "type": "number",
                        "description": "Seconds from the start. Required for play_segment.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short note shown to the user, e.g. 'the gun appears here'.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_frame_at",
            "description": (
                "Capture the exact still image at one moment of the attached "
                "video and show it in the chat. Use it when the user asks to "
                "SEE something — 'show me the frame where the accident happens', "
                "'what does it look like at 00:12' — or to back up a finding "
                "with a picture. `analyze_video` already returns frames for the "
                "moments it flags, so call this for a moment it didn't cover."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "second": {
                        "type": "number",
                        "description": "Offset into the video, in seconds.",
                    },
                    "note": {
                        "type": "string",
                        "description": "Caption shown under the frame, e.g. 'the collision'.",
                    },
                },
                "required": ["second"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_scans",
            "description": (
                "List the user's most recent scans (id, filename, model, "
                "whether a threat was found). Use to orient yourself before "
                "answering questions about past videos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "How many scans to return (1-20). Default 10.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_scan_report",
            "description": (
                "Fetch the full stored report for one scan by id — every frame "
                "detection and incident segment. Use when the user asks for the "
                "details of a specific past scan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scan_id": {
                        "type": "integer",
                        "description": "Scan id, as returned by list_recent_scans.",
                    }
                },
                "required": ["scan_id"],
            },
        },
    },
]


# --------------------------------------------------------------------------
# Executors
# --------------------------------------------------------------------------


def _compact_result(result: dict) -> dict:
    """Shrink a detection result to what's useful in a chat turn."""
    compact: dict[str, Any] = {"model_id": result.get("model_id")}
    detections = result.get("detections") or []
    segments = result.get("segments") or []

    # A stored scan always carries both keys (see scan_repository._build_detail),
    # so presence alone can't pick the branch: an incident scan would be read as
    # an empty object detection. Go by what is actually in there, and let an
    # incident win — it is the headline, boxes are the supporting detail.
    if not segments and ("detections" in result or detections):
        compact.update(
            {
                "kind": "object_detection",
                # Stored scans keep one "threat found" flag for both shapes.
                "weapon_detected": bool(
                    result.get("weapon_detected", result.get("violence_detected"))
                ),
                "frames_scanned": result.get("frames_scanned"),
                "detection_count": result.get("detection_count", len(detections)),
                "label_counts": result.get("label_counts", {}),
                "top_detections": [
                    {
                        "timestamp": d.get("timestamp"),
                        "second": d.get("second"),
                        "label": d.get("label"),
                        "score": d.get("score"),
                        # Carried through so the frame can be boxed at exactly
                        # the spot the detector fired on.
                        "box_xyxy": d.get("box_xyxy"),
                    }
                    for d in detections[:MAX_ECHOED_ITEMS]
                ],
                "truncated": len(detections) > MAX_ECHOED_ITEMS,
            }
        )
    else:
        compact.update(
            {
                "kind": "incident_analysis",
                "violence_detected": bool(result.get("violence_detected")),
                "summary": result.get("summary", ""),
                "segments": [
                    {
                        "label": s.get("label"),
                        "category": s.get("category"),
                        "description": s.get("description"),
                        "start_time": s.get("start_time"),
                        "end_time": s.get("end_time"),
                        "confidence": s.get("confidence"),
                        "clip_url": s.get("clip_url"),
                        # The moment the model was most certain — a merged span
                        # can cover the whole clip, so its midpoint means little.
                        "peak_second": s.get("peak_second"),
                    }
                    for s in segments[:MAX_ECHOED_ITEMS]
                ],
                "truncated": len(segments) > MAX_ECHOED_ITEMS,
            }
        )
    return compact


def _run_detector(detector: str, path: Path, args: dict[str, Any]) -> dict:
    """Dispatch to the right detection service (blocking — call in a thread)."""
    num_frames = max(1, min(128, int(args.get("num_frames") or 24)))
    threshold = float(args.get("threshold") or 0.25)
    threshold = min(max(threshold, 0.0), 1.0)

    if detector == "yolo":
        from services.yolo_detection import detect_weapons_yolo

        return detect_weapons_yolo(
            video_path=path, num_frames=num_frames, score_threshold=threshold
        )

    if detector == "owlv2":
        from services.video_detection import detect_weapons
        from ml.vid_img import DEFAULT_OBJECT_QUERIES

        raw = (args.get("queries") or "").strip()
        queries = tuple(q.strip() for q in raw.split(",") if q.strip()) or DEFAULT_OBJECT_QUERIES
        return detect_weapons(
            video_path=path,
            queries=queries,
            num_frames=num_frames,
            score_threshold=threshold,
        )

    if detector == "action":
        from services.action_detection import detect_actions

        return detect_actions(
            video_path=path, window_seconds=3, stride_seconds=2, threshold=0.5
        )

    if detector == "qwen":
        from services.qwen_detection import detect_violence_qwen

        return detect_violence_qwen(video_path=str(path))

    raise ValueError(f"Unknown detector '{detector}'")


def _focus_segments(result: dict, path: Path) -> dict:
    """Fold repeated rows into one incident, then keep it off the whole clip.

    The chat runs the same models the worker does and inherits the same habit of
    describing one event several times, so it gets the same two corrections (see
    `services.detection_runner._shape`): consolidate first, then pull any span
    still covering more than `MAX_INCIDENT_COVERAGE` of the video back to the
    moment the model was most certain about, saying so in its description.

    Blocking (it reads the video), so call it in a thread. A no-op for the object
    detectors, which report per-frame hits rather than spans.
    """
    segments = result.get("segments")
    if not segments:
        return result

    result["segments"] = segments = consolidate(
        segments,
        gap_seconds=settings.incident_merge_gap_seconds,
        min_confidence=settings.incident_min_confidence,
        max_incidents=settings.max_incidents_per_scan,
    )

    if MAX_INCIDENT_COVERAGE >= 1:
        return result

    duration = video_duration(path)
    if duration <= 0:
        return result

    focus_on_peak(segments, duration, max(2.0, duration * 0.2))
    for segment in segments:
        if segment.pop("narrowed", False):
            segment["description"] = (segment.get("description") or "") + (
                " The model flagged the entire clip, so the marked section is"
                " where it was most certain."
            )
    return result


def _run_auto(path: Path) -> dict:
    """Cascade every model and return the first incident found."""
    from services.action_detection import detect_actions
    from services.violence_detection import detect_violence

    clear: Optional[dict] = None
    for label, run in (
        ("violence", lambda: detect_violence(video_path=path)),
        (
            "action",
            lambda: detect_actions(
                video_path=path, window_seconds=3, stride_seconds=2, threshold=0.85
            ),
        ),
        ("yolo", lambda: _run_detector("yolo", path, {})),
    ):
        try:
            result = run()
        except Exception as e:
            logger.info("agent auto: %s skipped (%s)", label, str(e)[:120])
            continue
        if result.get("violence_detected") or result.get("weapon_detected"):
            return result
        clear = clear or result

    return clear or {
        "model_id": "auto",
        "violence_detected": False,
        "summary": "No incidents detected by any model.",
        "segments": [],
    }


async def _attach_frames(compact: dict, path: Path) -> None:
    """Grab the still at each flagged moment, boxed where the detector fired.

    What lets an answer show the exact frame an incident happens on rather than
    only naming its timestamp. A bonus on top of the analysis — never fatal.
    """
    moments = compact.get("segments") or compact.get("top_detections") or []
    if not moments:
        return
    try:
        await run_in_threadpool(extract_frames_for_moments, str(path), moments)
    except Exception as e:
        logger.warning("agent: frame extraction failed: %s", e)


async def _stored_analysis(
    ctx: AgentContext, path: Path, detector: str
) -> Optional[dict]:
    """This session's existing scan, dressed as an `analyze_video` result.

    Returns None when there is nothing usable stored, so the caller falls
    through to a real run. The frames are re-attached because they live on disk
    under the request's media directory rather than in the scan row, and the
    answer is only as good as the picture it can point at.
    """
    row = await get_scan(ctx.prisma, ctx.user_id, int(ctx.last_scan_id))
    if not row:
        return None

    compact = _compact_result(row.get("result") or {})
    await _attach_frames(compact, path)
    compact.update(
        {
            "detector": row.get("model") or detector,
            "scan_id": row["id"],
            "video": ctx.video_name,
            "reused": True,
            "note": (
                "These are the findings from the analysis already run on this "
                "video — nothing was re-scanned. Answer from them. If the user "
                "wants a fresh run or a different model, call analyze_video "
                "again with force_rescan=true."
            ),
        }
    )
    logger.info(
        "agent: user %s answered from stored scan %s instead of re-running '%s'",
        ctx.user_id,
        row["id"],
        detector,
    )
    return compact


async def _tool_analyze_video(ctx: AgentContext, args: dict[str, Any]) -> dict:
    """Run a detector over the session's video and persist the scan."""
    path_str = ctx.video_path
    if not path_str:
        return {
            "error": "No video is attached to this chat. Ask the user to upload "
            "one with the attach button before analysing."
        }
    path = Path(path_str)
    if not path.exists():
        return {"error": f"The uploaded video is no longer on disk ({path.name})."}

    detector = str(args.get("detector") or "yolo").lower().strip()
    if detector not in DETECTORS:
        return {"error": f"Unknown detector '{detector}'. Choose one of: {', '.join(DETECTORS)}."}

    # The upload was analysed the moment it arrived. Re-running costs the user
    # minutes and returns the same findings, so a call that isn't an explicit
    # re-scan is answered from the stored scan — the prompt asks the model not
    # to make it, and this makes the mistake harmless when it does anyway.
    if ctx.last_scan_id and not args.get("force_rescan"):
        stored = await _stored_analysis(ctx, path, detector)
        if stored is not None:
            return stored

    logger.info(
        "agent: user %s analysing %s with '%s'", ctx.user_id, path.name, detector
    )
    try:
        if detector == "auto":
            result = await run_in_threadpool(_run_auto, path)
        else:
            result = await run_in_threadpool(_run_detector, detector, path, args)
    except Exception as e:
        logger.error("agent: %s detection failed: %s", detector, e, exc_info=True)
        return {"error": f"The {detector} detector failed: {str(e)[:200]}"}

    result = await run_in_threadpool(_focus_segments, result, path)

    scan_id = await save_scan(
        ctx.prisma, ctx.user_id, ctx.video_name or path.name, detector, result
    )
    if scan_id:
        await chat_store.set_session_scan(ctx.prisma, ctx.session_id, scan_id)
        ctx.session["last_scan_id"] = scan_id

    compact = _compact_result(result)
    await _attach_frames(compact, path)

    compact.update({"detector": detector, "scan_id": scan_id, "video": ctx.video_name})
    return compact


async def _tool_query_database(ctx: AgentContext, args: dict[str, Any]) -> dict:
    """Delegate to the DB insights agent."""
    question = str(args.get("question") or "").strip()
    if not question:
        return {"error": "No question was provided."}

    scoped = bool(args.get("scope_to_current_video"))
    try:
        return await db_agent.answer_question(
            ctx.prisma,
            ctx.user_id,
            question,
            scan_id=ctx.last_scan_id if scoped else None,
            video_name=ctx.video_name if scoped else None,
        )
    except db_agent.DbAgentError as e:
        return {"error": str(e)}


async def _tool_control_playback(ctx: AgentContext, args: dict[str, Any]) -> dict:
    """Emit a playback command for the browser's video panel to carry out.

    Nothing happens server-side: the result is a directive the frontend applies
    to the ``<video>`` element, which is why it only validates and echoes.
    """
    if not ctx.video_path:
        return {
            "error": "No video is attached to this chat, so there is nothing to play."
        }

    action = str(args.get("action") or "").lower().strip()
    if action not in PLAYBACK_ACTIONS:
        return {
            "error": f"Unknown action '{action}'. Choose one of: {', '.join(PLAYBACK_ACTIONS)}."
        }

    def _seconds(key: str) -> Optional[float]:
        raw = args.get(key)
        if raw is None:
            return None
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return None

    start = _seconds("start_time")
    end = _seconds("end_time")

    if action in ("seek", "play_segment") and start is None:
        return {"error": f"action '{action}' needs a numeric start_time."}
    if action == "play_segment":
        if end is None:
            return {"error": "action 'play_segment' needs a numeric end_time."}
        if end <= (start or 0.0):
            # A zero/negative window would pause instantly — give it a moment.
            end = (start or 0.0) + 3.0

    return {
        "kind": "playback",
        "action": action,
        "start_time": start,
        "end_time": end,
        "reason": str(args.get("reason") or ""),
        "video": ctx.video_name,
    }


async def _tool_show_frame(ctx: AgentContext, args: dict[str, Any]) -> dict:
    """Capture the still at one timestamp so the chat can display it."""
    path_str = ctx.video_path
    if not path_str:
        return {"error": "No video is attached to this chat."}
    if not Path(path_str).exists():
        return {"error": "The uploaded video is no longer on disk."}

    try:
        second = max(0.0, float(args.get("second")))
    except (TypeError, ValueError):
        return {"error": "second must be a number (seconds into the video)."}

    url = await run_in_threadpool(extract_frame, path_str, second)
    if not url:
        return {
            "error": f"No frame at {second:.1f}s — the video may be shorter than that."
        }

    # Box the people/weapons on the still so the answer shows not just when but
    # where. Never let this sink the frame itself.
    location = ""
    boxes: list[dict] = []
    try:
        fs_path = url.lstrip("/")
        boxes = await run_in_threadpool(localize_frame, fs_path)
        if boxes:
            size = await run_in_threadpool(annotate_frame, fs_path, boxes)
            if size:
                location = describe_location(boxes[0]["box_xyxy"], size[0], size[1])
    except Exception as e:
        logger.warning("agent: frame localisation failed: %s", e)

    return {
        "kind": "frame",
        "frame_url": url,
        "second": round(second, 2),
        "timestamp": format_seconds(second),
        "note": str(args.get("note") or ""),
        "location": location,
        "boxes": boxes,
    }


async def _tool_list_recent_scans(ctx: AgentContext, args: dict[str, Any]) -> dict:
    """Recent scan metadata for the current user."""
    limit = max(1, min(20, int(args.get("limit") or 10)))
    rows = await get_history(ctx.prisma, ctx.user_id, limit)
    return {"scans": [db_agent.jsonable(row) for row in rows], "count": len(rows)}


async def _tool_get_scan_report(ctx: AgentContext, args: dict[str, Any]) -> dict:
    """Full stored report for one of the user's scans."""
    try:
        scan_id = int(args.get("scan_id"))
    except (TypeError, ValueError):
        return {"error": "scan_id must be an integer."}

    row = await get_scan(ctx.prisma, ctx.user_id, scan_id)
    if not row:
        return {"error": f"No scan {scan_id} found for this user."}

    compact = _compact_result(row.get("result") or {})
    compact.update(
        {
            "scan_id": row["id"],
            "filename": row["filename"],
            "model": row["model"],
            "created_at": str(row["created_at"]),
        }
    )
    return compact


TOOL_EXECUTORS: dict[str, Callable[[AgentContext, dict], Awaitable[dict]]] = {
    "analyze_video": _tool_analyze_video,
    "query_detection_database": _tool_query_database,
    "control_video_playback": _tool_control_playback,
    "show_frame_at": _tool_show_frame,
    "list_recent_scans": _tool_list_recent_scans,
    "get_scan_report": _tool_get_scan_report,
}


async def analyze_and_store(
    ctx: AgentContext, detector: str = "auto", args: Optional[dict] = None
) -> dict:
    """Run a detector on the session's video and persist the scan.

    The auto-analyze-on-attach path: same work ``analyze_video`` does when the
    orchestrator calls it, but invoked directly (no LLM needed) the moment a
    video is uploaded.

    Only used when the streamed pipeline could not do the job — see
    ``read_stored_scan``, which is the normal path.
    """
    return await _tool_analyze_video(ctx, {"detector": detector, **(args or {})})


async def read_stored_scan(ctx: AgentContext, scan_id: int) -> Optional[dict]:
    """Present a scan the worker already produced, without detecting again.

    The chat upload and the streamed pipeline are the *same* file: once the
    worker's job has finished there is nothing left to detect, so the chat
    readout is built from the stored rows. Running the detectors a second time
    would only burn a minute of GPU to (re)state what is already known — and
    write a second scan for one video.

    Returns None when the scan isn't this user's, which leaves the caller free
    to fall back to analysing inline.
    """
    row = await get_scan(ctx.prisma, ctx.user_id, scan_id)
    if not row:
        return None

    compact = _compact_result(row.get("result") or {})

    # The worker stores its own frames; these are cut from the session's local
    # copy so the chat card boxes exactly the moments echoed above.
    path = Path(ctx.video_path) if ctx.video_path else None
    if path and path.exists():
        await _attach_frames(compact, path)

    await chat_store.set_session_scan(ctx.prisma, ctx.session_id, scan_id)
    ctx.session["last_scan_id"] = scan_id

    compact.update(
        {
            "detector": row.get("model") or compact.get("model_id"),
            "scan_id": scan_id,
            "video": row.get("filename") or ctx.video_name,
        }
    )
    return compact


def summarize_result(result: dict) -> str:
    """Write the analysis up for the chat: what happened, when, and where.

    Deliberately LLM-free, so the readout an operator gets the moment a video
    finishes uploading never depends on a chat model being reachable. Every line
    is taken from the detector's own output — nothing is inferred.
    """
    if result.get("error"):
        return result["error"]

    detector = result.get("detector") or result.get("model_id") or "the detector"

    if result.get("kind") == "object_detection":
        detections = result.get("top_detections") or []
        counts = result.get("label_counts") or {}
        frames = result.get("frames_scanned", 0)

        if not result.get("weapon_detected"):
            return (
                f"✅ Nothing dangerous found. I sampled {frames} frames with "
                f"{detector} and no weapon appeared in any of them.\n\n"
                "If you expected an incident, ask me to re-run it with the "
                "'auto' models — those recognise behaviour (fighting, robbery, "
                "accidents) rather than objects."
            )

        lines = [
            f"⚠️ Threat found — {result.get('detection_count', len(detections))} "
            f"detection(s) across {frames} sampled frames.",
            "",
            "What is in the video:",
        ]
        for label, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            best = max(
                (d for d in detections if d.get("label") == label),
                key=lambda d: d.get("score") or 0,
                default=None,
            )
            line = f"- {label} — seen {count} time(s)"
            if best:
                line += f", clearest at {best.get('timestamp')} ({(best.get('score') or 0) * 100:.0f}% confident"
                if best.get("location"):
                    line += f", {best['location']}"
                line += ")"
            lines.append(line)

        # A timeline reads chronologically, and a detector can fire several
        # boxes on one sampled frame — keep the strongest per moment.
        by_moment: dict[tuple, dict] = {}
        for d in detections:
            # Key on the displayed timestamp, not the raw second: 0.04s and
            # 0.0s both read "00:00:00", and listing both looks like a bug.
            key = (d.get("timestamp"), d.get("label"))
            best = by_moment.get(key)
            if best is None or (d.get("score") or 0) > (best.get("score") or 0):
                by_moment[key] = d
        timeline = sorted(by_moment.values(), key=lambda d: d.get("second") or 0)

        if timeline:
            lines += ["", "When it happens:"]
            for d in timeline[:8]:
                where = f" — {d['location']}" if d.get("location") else ""
                lines.append(
                    f"- {d.get('timestamp')} · {d.get('label')} "
                    f"({(d.get('score') or 0) * 100:.0f}%){where}"
                )
            if len(timeline) > 8:
                lines.append(f"- …and {len(timeline) - 8} more moment(s)")

        lines += [
            "",
            "The frames above are those exact moments, boxed where the detector "
            "fired. Click any of them — or ask me to play one — and the video "
            "jumps there.",
        ]
        return "\n".join(lines)

    # Incident analysis (action / VLM / auto cascade).
    segments = result.get("segments") or []
    if not (result.get("violence_detected") and segments):
        return (
            f"✅ No incident detected. {detector} watched the whole clip and "
            "found nothing violent, criminal or dangerous.\n\n"
            "Ask me to re-check with a different model if that looks wrong."
        )

    categories = sorted({s.get("category") or "incident" for s in segments})
    lines = [
        f"⚠️ Incident detected ({', '.join(categories)}) — "
        f"{len(segments)} flagged section(s).",
    ]
    if result.get("summary"):
        lines += ["", result["summary"]]

    lines += ["", "What happens, and when:"]
    for segment in segments:
        start = format_seconds(float(segment.get("start_time") or 0))
        end = format_seconds(float(segment.get("end_time") or 0))
        span = start if start == end else f"{start}–{end}"
        head = (
            f"- {span} · {segment.get('label')} "
            f"({segment.get('category')}, {(segment.get('confidence') or 0) * 100:.0f}%)"
        )
        if segment.get("location"):
            head += f" — {segment['location']}"
        lines.append(head)
        if segment.get("description"):
            lines.append(f"    {segment['description']}")

    lines += [
        "",
        "Each one has its frame above. Click it to jump the video straight to "
        "that moment.",
    ]
    return "\n".join(lines)


async def execute_tool(ctx: AgentContext, name: str, args: dict[str, Any]) -> dict:
    """Run one tool by name, converting any failure into a readable payload."""
    executor = TOOL_EXECUTORS.get(name)
    if executor is None:
        return {
            "error": f"Unknown tool '{name}'. Available: {', '.join(TOOL_EXECUTORS)}."
        }
    try:
        return await executor(ctx, args or {})
    except Exception as e:  # a tool must never break the conversation
        logger.error("agent: tool %s failed: %s", name, e, exc_info=True)
        return {"error": f"Tool '{name}' failed: {str(e)[:200]}"}
