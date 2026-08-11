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

from ml.vid_img import focus_on_peak, prune_to_peak, window_resolution

logger = logging.getLogger(__name__)

VIOLENCE_MODEL_ID = os.getenv(
    "VIOLENCE_MODEL_ID",
    "cliffer1/videomae-base-finetuned-kinetics-violence-nonviolence-tuned",
)
_NONVIOLENT = {"nonviolence", "normal", "neutral", "noviolence"}

# Floor under the adaptive cutoff that trims a segment to its strongest
# windows — see `vid_img.prune_to_peak`.
VIOLENCE_PEAK_MARGIN = float(os.getenv("VIOLENCE_PEAK_MARGIN", "0.05"))

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
    """Join windows that touch into one span, remembering where each peaked.

    The peak is the moment worth showing — a merged span's midpoint is
    arbitrary, the strongest window is where the violence actually is. Windows
    tying for the best score are averaged, so evenly-scored footage peaks in its
    middle rather than at its first frame.

    Each span also records the gap between its weakest and strongest window, so
    `vid_img.focus_on_peak` can tell a run the model was uniformly sure about
    (report it whole) from one with a clear peak (crop to the peak).
    """
    if not hits:
        return []
    hits.sort(key=lambda h: h["start_time"])
    merged = [
        dict(
            hits[0],
            _peak_from=_midpoint(hits[0]),
            _peak_to=_midpoint(hits[0]),
            _low=hits[0]["confidence"],
        )
    ]
    for h in hits[1:]:
        last = merged[-1]
        if h["start_time"] <= last["end_time"] + 0.5:
            last["end_time"] = max(last["end_time"], h["end_time"])
            last["_low"] = min(last["_low"], h["confidence"])
            if h["confidence"] > last["confidence"]:
                last["confidence"] = h["confidence"]
                last["_peak_from"] = last["_peak_to"] = _midpoint(h)
            elif h["confidence"] == last["confidence"]:
                last["_peak_to"] = _midpoint(h)
        else:
            merged.append(
                dict(
                    h,
                    _peak_from=_midpoint(h),
                    _peak_to=_midpoint(h),
                    _low=h["confidence"],
                )
            )

    for span in merged:
        span["peak_second"] = round(
            (span.pop("_peak_from") + span.pop("_peak_to")) / 2, 2
        )
        span["confidence_spread"] = round(span["confidence"] - span.pop("_low"), 4)
    return merged


def _midpoint(hit: dict) -> float:
    return round((hit["start_time"] + hit["end_time"]) / 2, 2)


def detect_violence(
    video_path: Path,
    window_seconds: float = 3,
    stride_seconds: float = 2,
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

    # Short clips need a finer window than the default 3s/2s, otherwise every
    # window overlaps the incident and the merge below returns the whole video.
    window_seconds, stride_seconds = window_resolution(
        total / fps if fps else 0.0, window_seconds, stride_seconds
    )
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

    segments = focus_on_peak(
        _merge(prune_to_peak(hits, VIOLENCE_PEAK_MARGIN)),
        total / fps if fps else 0.0,
        window_seconds,
    )
    for s in segments:
        s["description"] = f"Violence detected ({s['confidence'] * 100:.0f}% confidence)."
        if s.pop("narrowed", False):
            s["description"] += (
                " The whole clip scored as violent, so the marked section is"
                " the moment the model was most certain about."
            )

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
