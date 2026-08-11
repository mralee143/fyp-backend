"""
Action / violence recognition service.

Classifies *what people are doing* over time, which object detectors (YOLO,
OWLv2) cannot do — they only find things, not behaviour. Runs a VideoMAE video
classifier over sliding windows and reports each window whose predicted class
is an incident, with its time range.

The default weights are ``OPear/videomae-large-finetuned-UCF-Crime`` (UCF-Crime:
Fighting, Assault, Abuse, Robbery, Shooting, Explosion, …), downloaded/cached on
first use. Override with the ``ACTION_MODEL_ID`` env var — any HuggingFace video
classification checkpoint works, since labels are read from the model config.

Note: UCF-Crime has no `harassment` class; `Abuse`/`Assault` are the closest
proxies. Harassment is contextual and is handled by the LLM path
(``services.llm_detection``) instead.
"""

import logging
import os
from pathlib import Path
from typing import Any

# torch must be imported before decord on Windows, otherwise decord's DLLs
# break torch's c10.dll load order (WinError 1114).
import torch
from decord import VideoReader, cpu

from ml.vid_img import (
    HF_TOKEN,
    build_windows,
    focus_on_peak,
    frame_to_seconds,
    load_window_frames,
    prune_to_peak,
    window_resolution,
)

logger = logging.getLogger(__name__)

ACTION_MODEL_ID = os.getenv(
    "ACTION_MODEL_ID", "OPear/videomae-large-finetuned-UCF-Crime"
)

# VideoMAE checkpoints are trained on a fixed clip length; 16 is the value in
# the UCF-Crime config. Overridable for checkpoints trained on other lengths.
ACTION_NUM_FRAMES = int(os.getenv("ACTION_NUM_FRAMES", "16"))

# Classes that mean "nothing happened" — never reported as an incident.
_NORMAL_LABELS = {"normal_videos_event", "normal", "normal_videos"}

# Closest a window may sit to the strongest one and still be discarded — the
# floor under `vid_img.prune_to_peak`'s adaptive cutoff. Raise it to tighten
# segments further, lower it to keep more of the run.
ACTION_PEAK_MARGIN = float(os.getenv("ACTION_PEAK_MARGIN", "0.05"))

# UCF-Crime label -> the category the frontend groups by. Anything unmapped
# falls back to "violence", which is the safe default for a crime checkpoint.
_CATEGORIES = {
    "abuse": "violence",
    "arrest": "other",
    "arson": "violence",
    "assault": "violence",
    "burglary": "theft",
    "explosion": "violence",
    "fighting": "violence",
    "roadaccidents": "accident",
    "robbery": "theft",
    "shooting": "violence",
    "shoplifting": "theft",
    "stealing": "theft",
    "vandalism": "violence",
}

_model: Any = None
_processor: Any = None


def _load_model() -> tuple[Any, Any]:
    """Lazily download/load and cache the action classifier and its processor."""
    global _model, _processor
    if _model is None:
        from transformers import AutoImageProcessor, AutoModelForVideoClassification

        logger.info(f"Loading action model: {ACTION_MODEL_ID}")
        _processor = AutoImageProcessor.from_pretrained(ACTION_MODEL_ID, token=HF_TOKEN)
        _model = AutoModelForVideoClassification.from_pretrained(
            ACTION_MODEL_ID, token=HF_TOKEN
        )
        _model.eval()
        _model.to(_device())
        logger.info(f"Action model loaded on {_device()}")
    return _model, _processor


def _device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def _category_for(label: str) -> str:
    return _CATEGORIES.get(label.lower().replace(" ", ""), "violence")


# The model confuses the physical-violence classes (Abuse/Assault/Fighting) on
# clean/out-of-distribution footage, so present them under one accurate label
# instead of a misleading sub-name. Other classes keep their proper names.
_DISPLAY_LABEL = {
    "abuse": "Fighting / Assault",
    "assault": "Fighting / Assault",
    "fighting": "Fighting / Assault",
    "roadaccidents": "Road Accident",
    "shooting": "Shooting",
    "explosion": "Explosion",
}


def _display_label(label: str) -> str:
    key = label.lower().replace(" ", "").replace("_", "")
    return _DISPLAY_LABEL.get(key, label.replace("_", " "))


def detect_actions(
    video_path: Path,
    window_seconds: float = 3,
    stride_seconds: float = 2,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Detect violent/criminal *actions* in a video and return their time ranges.

    Slides a window across the video and classifies each one, keeping windows
    whose top prediction is an incident class scoring at least ``threshold``.
    Overlapping windows of the same label are merged into one segment, so a
    10-second fight reads as a single event rather than five.

    Two steps keep that merge from swallowing the whole clip, which is what
    turns a timeline into a solid bar: short videos are sampled at a finer
    window/stride than the caller asked for (``window_resolution``), and windows
    scoring well below the best one are dropped before merging
    (``prune_to_peak``). What survives is the stretch the model is actually
    confident about.

    Args:
        video_path: Path to a readable video file.
        window_seconds: Length of each classified window. Reduced automatically
            on short clips.
        stride_seconds: Step between windows; less than ``window_seconds``
            overlaps them, which avoids missing events that straddle a boundary.
        threshold: Minimum confidence for the top class to count as an incident.

    Returns:
        A dict matching ``schemas.detection.LlmDetectionResponse``, so the same
        frontend timeline renders both this and the LLM path.

    Raises:
        ValueError: If no windows could be read from the video.
    """
    model, processor = _load_model()
    device = _device()
    id_to_label: dict[int, str] = model.config.id2label

    reader = VideoReader(str(video_path), ctx=cpu(0))
    fps = float(reader.get_avg_fps())
    total_frames = len(reader)
    duration = frame_to_seconds(total_frames, fps)

    # An 8-second clip stepped 2 seconds at a time gives four windows, all of
    # them overlapping any incident in it — hence a full-width bar. Sample it
    # finely enough to tell the seconds apart.
    window_seconds, stride_seconds = window_resolution(
        duration, window_seconds, stride_seconds
    )
    windows = build_windows(
        total_frames, fps, window_seconds, stride_seconds, min_frames=ACTION_NUM_FRAMES
    )
    if not windows:
        raise ValueError("No frames could be read from the input video.")
    logger.info(
        "action: %.1fs video -> %d window(s) of %.1fs every %.1fs",
        duration,
        len(windows),
        window_seconds,
        stride_seconds,
    )

    hits: list[dict[str, Any]] = []
    for start_frame, end_frame in windows:
        frames = load_window_frames(reader, start_frame, end_frame, ACTION_NUM_FRAMES)
        if frames.size == 0:
            continue

        try:
            inputs = processor(list(frames), return_tensors="pt").to(device)
            with torch.inference_mode():
                probs = torch.softmax(model(**inputs).logits[0], dim=-1)
        except Exception as e:
            # One unclassifiable window must not lose the whole video: callers
            # treat a raise here as "the action model found nothing".
            logger.warning(
                "action: window %.2fs-%.2fs skipped (%s)",
                frame_to_seconds(start_frame, fps),
                frame_to_seconds(end_frame, fps),
                str(e)[:120],
            )
            continue

        top_idx = int(torch.argmax(probs))
        label = id_to_label[top_idx]
        score = float(probs[top_idx])

        if label.lower() in _NORMAL_LABELS or score < threshold:
            continue

        hits.append(
            {
                "label": _display_label(label),
                "category": _category_for(label),
                "start_time": round(frame_to_seconds(start_frame, fps), 2),
                "end_time": round(frame_to_seconds(end_frame, fps), 2),
                "confidence": round(score, 4),
            }
        )

    segments = focus_on_peak(
        _merge_adjacent(prune_to_peak(hits, ACTION_PEAK_MARGIN)),
        duration,
        window_seconds,
    )
    for seg in segments:
        seg["description"] = (
            f"{seg['label']} recognised by the action model "
            f"({seg['confidence'] * 100:.0f}% confidence)."
        )
        if seg.pop("narrowed", False):
            # Say it plainly: the span shown is the model's peak, not a measured
            # boundary, because every window of the clip came back as this class.
            seg["description"] += (
                " Every window of the clip scored as this class, so the marked"
                " section is the moment the model was most certain about."
            )

    if segments:
        labels = sorted({seg["label"] for seg in segments})
        summary = f"Detected {', '.join(labels)} across {len(segments)} segment(s)."
    else:
        summary = "No violent or criminal actions were recognised."

    return {
        "model_id": ACTION_MODEL_ID,
        "violence_detected": bool(segments),
        "summary": summary,
        "segments": segments,
    }


def _merge_adjacent(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive same-label windows that touch or overlap into one span.

    Each span keeps the strongest window's confidence and remembers *where* it
    peaked — a merged span can cover far more than the incident, so its midpoint
    is arbitrary while the peak is the moment the model was most certain, the
    frame worth showing. When several windows tie for the best score the centre
    of that run is the peak: taking the first of them would point at the opening
    seconds of footage the model scores evenly throughout.

    Each span also records how far its weakest window sat below its strongest.
    A narrow spread means the model called the whole run the same incident, and
    ``vid_img.focus_on_peak`` uses that to leave the span at full width instead
    of cropping a genuinely long event down to one window.
    """
    merged: list[dict[str, Any]] = []
    for hit in hits:
        midpoint = (hit["start_time"] + hit["end_time"]) / 2
        prev = merged[-1] if merged else None
        if (
            prev
            and prev["label"] == hit["label"]
            and hit["start_time"] <= prev["end_time"]
        ):
            prev["end_time"] = max(prev["end_time"], hit["end_time"])
            prev["_low"] = min(prev["_low"], hit["confidence"])
            if hit["confidence"] > prev["confidence"]:
                prev["confidence"] = hit["confidence"]
                prev["_peak_from"] = prev["_peak_to"] = midpoint
            elif hit["confidence"] == prev["confidence"]:
                prev["_peak_to"] = midpoint
        else:
            merged.append(
                dict(
                    hit,
                    _peak_from=midpoint,
                    _peak_to=midpoint,
                    _low=hit["confidence"],
                )
            )

    for span in merged:
        span["peak_second"] = round(
            (span.pop("_peak_from") + span.pop("_peak_to")) / 2, 2
        )
        span["confidence_spread"] = round(span["confidence"] - span.pop("_low"), 4)
    return merged
