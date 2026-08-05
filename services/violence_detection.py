"""
Dedicated binary violence detection with a VideoMAE model fine-tuned on
violence datasets (violence / non-violence). Far more accurate at recognising
*fights/assault* than the general UCF-Crime crime classifier.

Runs a sliding window over the video (GPU when available) and returns the
segments predicted as violent, in the shared LlmDetectionResponse shape.
"""

import logging
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

logger = logging.getLogger(__name__)

VIOLENCE_MODEL_ID = os.getenv(
    "VIOLENCE_MODEL_ID",
    "cliffer1/videomae-base-finetuned-kinetics-violence-nonviolence-tuned",
)
_NONVIOLENT = {"nonviolence", "normal", "neutral", "noviolence"}

_model: Any = None
_proc: Any = None


def _load():
    global _model, _proc
    if _model is None:
        from transformers import AutoModelForVideoClassification, AutoImageProcessor

        logger.info("Loading violence model: %s", VIOLENCE_MODEL_ID)
        _proc = AutoImageProcessor.from_pretrained(VIOLENCE_MODEL_ID)
        _model = AutoModelForVideoClassification.from_pretrained(VIOLENCE_MODEL_ID).eval()
        _model.to("cuda" if torch.cuda.is_available() else "cpu")
    return _model, _proc


def _read_window(cap, start: int, end: int, nframes: int) -> list:
    idxs = np.linspace(start, max(start, end - 1), nframes).astype(int)
    frames = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        if ok:
            frames.append(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
    return frames


def _merge(hits: list[dict]) -> list[dict]:
    if not hits:
        return []
    hits.sort(key=lambda h: h["start_time"])
    merged = [hits[0]]
    for h in hits[1:]:
        last = merged[-1]
        if h["start_time"] <= last["end_time"] + 0.5:
            last["end_time"] = max(last["end_time"], h["end_time"])
            last["confidence"] = max(last["confidence"], h["confidence"])
        else:
            merged.append(h)
    return merged


def detect_violence(
    video_path: Path,
    window_seconds: int = 3,
    stride_seconds: int = 2,
    threshold: float = 0.6,
) -> dict[str, Any]:
    """Detect violent segments in a video. Returns the LlmDetectionResponse dict."""
    model, proc = _load()
    device = next(model.parameters()).device
    nframes = int(getattr(model.config, "num_frames", 16))
    id2label = model.config.id2label

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError("Could not open the video.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    win = max(1, int(window_seconds * fps))
    stride = max(1, int(stride_seconds * fps))

    hits: list[dict] = []
    start = 0
    while start < total:
        end = min(start + win, total)
        frames = _read_window(cap, start, end, nframes)
        if len(frames) < max(2, nframes // 2):
            break
        while len(frames) < nframes:
            frames.append(frames[-1])

        inp = proc(frames, return_tensors="pt").to(device)
        with torch.no_grad():
            probs = torch.softmax(model(**inp).logits[0], dim=-1)
        top = int(torch.argmax(probs))
        label = id2label[top].lower().replace(" ", "").replace("-", "").replace("_", "")
        score = float(probs[top])

        if label not in _NONVIOLENT and score >= threshold:
            hits.append(
                {
                    "label": "Violence",
                    "category": "violence",
                    "start_time": round(start / fps, 2),
                    "end_time": round(end / fps, 2),
                    "confidence": round(score, 4),
                }
            )
        start += stride
    cap.release()

    segments = _merge(hits)
    for s in segments:
        s["description"] = f"Violence detected ({s['confidence'] * 100:.0f}% confidence)."

    return {
        "model_id": VIOLENCE_MODEL_ID,
        "violence_detected": bool(segments),
        "summary": (
            f"Detected violence across {len(segments)} segment(s)."
            if segments
            else "No violence detected."
        ),
        "segments": segments,
    }
