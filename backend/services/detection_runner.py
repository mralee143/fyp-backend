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

# Qwen runs as a subprocess that loads a multi-billion-parameter model into its
# own address space, so two of them are two full copies of the weights. The
# worker takes `max_jobs` videos at once, and on a 16 GB host the second copy
# does not fit — both processes then page against each other and neither
# finishes inside its budget. One at a time is strictly faster than two.
_QWEN_SLOT = asyncio.Semaphore(1)

# Below this much of the budget left, starting Qwen only burns the remainder on
# loading weights it will be killed before it can use.
_QWEN_MIN_SECONDS = 60

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
        await progress(40, "detecting", "Scanning for weapons…")
        return await asyncio.to_thread(
            detect_weapons_yolo,
            video_path=video_path,
            num_frames=num_frames,
            score_threshold=threshold,
        )

    if model == "owlv2":
        await progress(40, "detecting", "Scanning for objects…")
        return await asyncio.to_thread(
            detect_weapons,
            video_path=video_path,
            queries=queries or DEFAULT_OBJECT_QUERIES,
            num_frames=num_frames,
            score_threshold=threshold,
        )

    if model == "llm":
        await progress(40, "detecting", "Analysing the video…")
        mime = VIDEO_MIME.get(video_path.suffix.lower(), "video/mp4")
        video_bytes = await asyncio.to_thread(video_path.read_bytes)
        result = await asyncio.to_thread(
            detect_violence_llm, video_bytes=video_bytes, mime_type=mime
        )
        return await _shape(result, video_path)

    if model == "qwen":
        await progress(40, "detecting", "Analysing the video…")
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
    """Cascade: Gemini -> Qwen, then violence classifier -> action model.

    The progress messages name no model. Which backend answered is an internal
    detail — the person watching a video upload wants to know the analysis is
    moving, and a message that says a vendor is unavailable reads as a fault in
    the product. The winning model is still recorded on the scan for anyone who
    needs it; it is just not narrated live.

    Each stage is optional — a missing API key, an exhausted quota or an
    unavailable local model just moves the cascade on to the next backend
    instead of failing the job.

    **Gemini decides when it runs, and Qwen stands in for it.** The two
    scene-reading models sit at the top because they are the only backends that
    watch the whole clip and can genuinely report "nothing here". The
    classifiers below them are a fallback for a stage that could not answer at
    all, not a second opinion on one that did. Taking the first *positive*
    instead compounded every model's false-positive rate:
    Gemini would watch a clip of a ball and correctly report nothing, and that
    verdict was thrown away the moment a downstream classifier — which has no
    way to abstain, and no idea what it is looking at — guessed "violence".
    Chaining models that way makes a clear video almost impossible to report,
    because each stage only ever adds ways to say yes and none to say no.
    """
    detected: Optional[dict] = None
    clear: Optional[dict] = None

    # 1) Gemini — best coverage (it is the only backend that reads harassment
    #    from context) and fast, when the key has quota left. It is also the
    #    only one that actually understands the scene, so its "nothing here" is
    #    worth as much as its "here it is": both are returned as final.
    await progress(40, "detecting", "Analysing the video…")
    try:
        mime = VIDEO_MIME.get(video_path.suffix.lower(), "video/mp4")
        video_bytes = await asyncio.to_thread(video_path.read_bytes)
        result = await asyncio.to_thread(
            detect_violence_llm,
            video_bytes=video_bytes,
            mime_type=mime,
            budget_s=settings.auto_gemini_timeout_seconds,
        )
        logger.info(
            "auto: Gemini answered (violence_detected=%s) — taking it as final",
            bool(result.get("violence_detected")),
        )
        return result
    except LlmDetectionError as exc:
        logger.info("auto: Gemini skipped (%s)", str(exc)[:120])
    except Exception as exc:
        logger.info("auto: Gemini failed (%s)", str(exc)[:120])

    # 2) Local Qwen2.5-VL — the stand-in for a Gemini that could not answer,
    #    and the reason an exhausted quota no longer drops straight to the
    #    classifiers. It reads the scene the same way Gemini does, so its
    #    verdict is final on the same grounds: a model that can abstain is
    #    worth as much when it says "nothing here" as when it says otherwise.
    #
    #    It runs in its own venv as a subprocess, and on a CPU-only host that
    #    pass is slow enough to outlast the job itself — hence its own budget
    #    (`auto_qwen_timeout_seconds`). Overrunning it is not a failure; the
    #    cascade simply carries on to the classifiers.
    if settings.auto_qwen_enabled:
        await progress(46, "detecting", "Still analysing — this one is taking longer…")
        # The budget covers the queue for the slot as well as the run itself.
        # A job that spends it all waiting has not failed — it just goes to the
        # classifiers, which is the whole point of the stage being optional.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.auto_qwen_timeout_seconds
        try:
            await asyncio.wait_for(
                _QWEN_SLOT.acquire(), timeout=settings.auto_qwen_timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.info("auto: Qwen skipped (another job held the slot for the whole budget)")
        else:
            try:
                remaining = int(deadline - loop.time())
                if remaining < _QWEN_MIN_SECONDS:
                    logger.info("auto: Qwen skipped (only %ss of its budget left)", remaining)
                else:
                    result = await asyncio.to_thread(
                        detect_violence_qwen,
                        video_path=str(video_path),
                        timeout_s=remaining,
                    )
                    logger.info(
                        "auto: Qwen answered (violence_detected=%s) — taking it as final",
                        bool(result.get("violence_detected")),
                    )
                    return result
            except QwenDetectionError as exc:
                logger.info("auto: Qwen skipped (%s)", str(exc)[:160])
            except Exception as exc:
                logger.info("auto: Qwen failed (%s)", str(exc)[:160])
            finally:
                _QWEN_SLOT.release()

    # 3) Dedicated violence classifier — accurate on fights and assault.
    if detected is None:
        await progress(52, "detecting", "Checking for violence…")
        try:
            result = await asyncio.to_thread(detect_violence, video_path=video_path)
            if result.get("violence_detected"):
                detected = result
            elif clear is None:
                clear = result
        except Exception as exc:
            logger.info("auto: violence model skipped (%s)", str(exc)[:120])

    # 4) Action model — covers robbery, arson, road accidents and the rest of
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
