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

from vid_img import (
    HF_TOKEN,
    build_windows,
    frame_to_seconds,
    load_window_frames,
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
    "roadaccidents": "other",
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
    window_seconds: int = 3,
    stride_seconds: int = 2,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Detect violent/criminal *actions* in a video and return their time ranges.

    Slides a window across the video and classifies each one, keeping windows
    whose top prediction is an incident class scoring at least ``threshold``.
    Overlapping windows of the same label are merged into one segment, so a
    10-second fight reads as a single event rather than five.

    Args:
        video_path: Path to a readable video file.
        window_seconds: Length of each classified window.
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
    windows = build_windows(total_frames, fps, window_seconds, stride_seconds)
    if not windows:
        raise ValueError("No frames could be read from the input video.")

    hits: list[dict[str, Any]] = []
    for start_frame, end_frame in windows:
        frames = load_window_frames(reader, start_frame, end_frame, ACTION_NUM_FRAMES)
        if frames.size == 0:
            continue

        inputs = processor(list(frames), return_tensors="pt").to(device)
        with torch.inference_mode():
            probs = torch.softmax(model(**inputs).logits[0], dim=-1)

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

    segments = _merge_adjacent(hits)
    for seg in segments:
        seg["description"] = (
            f"{seg['label']} recognised by the action model "
            f"({seg['confidence'] * 100:.0f}% confidence)."
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
    """Merge consecutive same-label windows that touch or overlap into one span."""
    merged: list[dict[str, Any]] = []
    for hit in hits:
        prev = merged[-1] if merged else None
        if (
            prev
            and prev["label"] == hit["label"]
            and hit["start_time"] <= prev["end_time"]
        ):
            prev["end_time"] = max(prev["end_time"], hit["end_time"])
            # Keep the strongest window's confidence for the merged span.
            prev["confidence"] = max(prev["confidence"], hit["confidence"])
        else:
            merged.append(dict(hit))
    return merged
