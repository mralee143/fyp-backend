"""
One entry point for every detection backend.

The route layer used to inline the model-dispatch logic (including the `auto`
cascade) in each endpoint. Now that analysis runs in a worker, that logic lives
here so the queue, any future CLI and the tests all drive the models the same
way.

Every backend is blocking and CPU/GPU-bound, so `run_detection` is async only at
its edges — the actual inference is pushed onto a worker thread with
`asyncio.to_thread` to keep the worker's event loop responsive (it still has to
publish progress events while a model runs).
"""

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

from config import settings
from services.action_detection import detect_actions
from services.incident_merge import consolidate
from services.llm_detection import LlmDetectionError, detect_violence_llm
from services.qwen_detection import QwenDetectionError, detect_violence_qwen
from services.video_detection import detect_weapons
from services.violence_detection import detect_violence
from services.yolo_detection import detect_weapons_yolo
from ml.vid_img import (
    DEFAULT_OBJECT_QUERIES,
    MAX_INCIDENT_COVERAGE,
    focus_on_peak,
    video_duration,
)

logger = logging.getLogger(__name__)

SUPPORTED_MODELS = {"auto", "yolo", "owlv2", "llm", "qwen", "action"}

# Extension -> MIME type, for Gemini's inline video payload.
VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}

# The action model over-predicts weak "Fighting/Assault" (~0.65) on ordinary
# clips while genuine incidents score 0.95+, so the cascade holds it to a high
# bar and lets the dedicated violence classifier handle borderline fights.
_AUTO_ACTION_THRESHOLD = 0.85

# Progress callback: (percent, stage, human message) -> awaitable
ProgressFn = Callable[[int, str, str], Awaitable[None]]


async def _noop(_percent: int, _stage: str, _message: str) -> None:
    return None


async def _shape(result: dict, video_path: Path) -> dict:
    """Turn a backend's raw segments into the incidents the report shows.

    Two corrections, in this order, because each depends on the other's output:

      1. **Consolidate.** One event described three times is three cards and
         three timeline bands; `incident_merge` folds overlapping and adjacent
         rows back into the single incident they describe, and drops the ones
         too weak to stand behind.
      2. **Focus.** A span that runs the length of the video marks every second
         as worth watching, which is the same as marking none of them. Anything
         past `MAX_INCIDENT_COVERAGE` — including a span that only got that long
         by merging — is pulled back to the moment the model was most certain
         about, and says so in its own description.

    A no-op for the object detectors, which report per-frame hits, not spans.
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
    # Consolidation can drop every row of a result the backend called positive
    # (all of them below the floor is impossible — one always survives), but it
    # can also leave a single hedged incident. Keep the flag honest either way.
    result["violence_detected"] = bool(segments) and bool(
        result.get("violence_detected", True)
    )

    if MAX_INCIDENT_COVERAGE >= 1:
        return result

    duration = await asyncio.to_thread(video_duration, video_path)
    if duration <= 0:
        return result

    focus_on_peak(segments, duration, max(2.0, duration * 0.2))
    for segment in segments:
        if segment.pop("narrowed", False):
            logger.info(
                "narrowed a full-clip segment to its peak at %.1fs",
                segment.get("peak_second") or 0.0,
            )
            note = (
                " The model flagged the entire clip, so the marked section is"
                " where it was most certain."
            )
            segment["description"] = (segment.get("description") or "") + note
    return result


async def run_detection(
    model: str,
    video_path: Path,
    *,
    queries: Optional[tuple[str, ...]] = None,
    num_frames: int = 24,
    threshold: float = 0.2,
    window_seconds: int = 3,
    stride_seconds: int = 2,
    on_progress: Optional[ProgressFn] = None,
) -> dict:
    """Run one detection backend and return its raw result dict.

    Args:
        model: One of SUPPORTED_MODELS.
        video_path: Local path to the video (models need a real file).
        queries: OWLv2 free-text queries; ignored by the other backends.
        num_frames: Frames to sample for the object detectors.
        threshold: Minimum confidence to keep.
        window_seconds/stride_seconds: Action-model sliding window.
        on_progress: Called with (percent, stage, message) as the run advances.

    Returns:
        The backend's result dict — either the object-detector shape
        (`detections`/`label_counts`/`weapon_detected`) or the segment shape
        (`segments`/`violence_detected`/`summary`).

    Raises:
        ValueError: For an unknown model, or a video no backend could read.
    """
    model = (model or "auto").lower().strip()
    if model not in SUPPORTED_MODELS:
        raise ValueError(
            f"Unknown model '{model}'. Allowed: {', '.join(sorted(SUPPORTED_MODELS))}"
        )

    progress = on_progress or _noop

    if model == "yolo":
        await progress(40, "detecting", "Running the YOLO weapon detector…")
        return await asyncio.to_thread(
            detect_weapons_yolo,
            video_path=video_path,
            num_frames=num_frames,
            score_threshold=threshold,
        )

    if model == "owlv2":
        await progress(40, "detecting", "Running zero-shot object detection…")
        return await asyncio.to_thread(
            detect_weapons,
            video_path=video_path,
            queries=queries or DEFAULT_OBJECT_QUERIES,
            num_frames=num_frames,
            score_threshold=threshold,
        )

    if model == "llm":
        await progress(40, "detecting", "Sending the video to Gemini…")
        mime = VIDEO_MIME.get(video_path.suffix.lower(), "video/mp4")
        video_bytes = await asyncio.to_thread(video_path.read_bytes)
        result = await asyncio.to_thread(
            detect_violence_llm, video_bytes=video_bytes, mime_type=mime
        )
        return await _shape(result, video_path)

    if model == "qwen":
        await progress(40, "detecting", "Running the local Qwen2.5-VL model…")
        result = await asyncio.to_thread(detect_violence_qwen, video_path=str(video_path))
        return await _shape(result, video_path)

    if model == "action":
        await progress(40, "detecting", "Recognising actions across the timeline…")
        result = await asyncio.to_thread(
            detect_actions,
            video_path=video_path,
            window_seconds=window_seconds,
            stride_seconds=stride_seconds,
            threshold=threshold,
        )
        return await _shape(result, video_path)

    return await _shape(await _run_auto(video_path, progress), video_path)


async def _run_auto(video_path: Path, progress: ProgressFn) -> dict:
    """Cascade: Gemini -> violence classifier -> action model, first hit wins.

    Each stage is optional — a missing API key, an exhausted quota or an
    unavailable local model just moves the cascade on to the next backend
    instead of failing the job.
    """
    detected: Optional[dict] = None
    clear: Optional[dict] = None

    # 1) Gemini — best coverage (it is the only backend that reads harassment
    #    from context) and fast, when the key has quota left.
    await progress(40, "detecting", "Asking Gemini to watch the video…")
    try:
        mime = VIDEO_MIME.get(video_path.suffix.lower(), "video/mp4")
        video_bytes = await asyncio.to_thread(video_path.read_bytes)
        result = await asyncio.to_thread(
            detect_violence_llm, video_bytes=video_bytes, mime_type=mime
        )
        if result.get("violence_detected"):
            detected = result
        else:
            clear = result
    except LlmDetectionError as exc:
        logger.info("auto: Gemini skipped (%s)", str(exc)[:120])
    except Exception as exc:
        logger.info("auto: Gemini failed (%s)", str(exc)[:120])

    # 2) Dedicated violence classifier — accurate on fights and assault.
    if detected is None:
        await progress(52, "detecting", "Running the violence classifier…")
        try:
            result = await asyncio.to_thread(detect_violence, video_path=video_path)
            if result.get("violence_detected"):
                detected = result
            elif clear is None:
                clear = result
        except Exception as exc:
            logger.info("auto: violence model skipped (%s)", str(exc)[:120])

    # 3) Action model — covers robbery, arson, road accidents and the rest of
    #    the UCF-Crime classes the other two can miss.
    if detected is None:
        await progress(62, "detecting", "Checking for criminal actions…")
        try:
            result = await asyncio.to_thread(
                detect_actions,
                video_path=video_path,
                window_seconds=3,
                stride_seconds=2,
                threshold=_AUTO_ACTION_THRESHOLD,
            )
            if result.get("violence_detected"):
                detected = result
            elif clear is None:
                clear = result
        except Exception as exc:
            logger.info("auto: action model skipped (%s)", str(exc)[:120])

    return detected or clear or {
        "model_id": "auto",
        "violence_detected": False,
        "summary": "No incidents detected by any model.",
        "segments": [],
    }


def model_code_from_result(result: dict, requested: str) -> str:
    """Which backend actually produced a cascade result.

    `auto` delegates, so the scan records the winning model rather than "auto" —
    the requested model is still on the job row.
    """
    raw = str(result.get("model_id") or requested or "auto").lower()
    for code in ("llm", "gemini", "qwen", "action", "yolo", "owlv2", "violence"):
        if code in raw:
            return "llm" if code == "gemini" else code
    return requested if requested in SUPPORTED_MODELS else "auto"


__all__ = [
    "SUPPORTED_MODELS",
    "VIDEO_MIME",
    "LlmDetectionError",
    "QwenDetectionError",
    "model_code_from_result",
    "run_detection",
]
