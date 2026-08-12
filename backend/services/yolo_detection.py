"""
YOLO weapon detection service.

Runs a YOLOv8 model fine-tuned for weapon/threat detection over sampled video
frames. Unlike the zero-shot OWLv2 detector (which accepts free-text queries),
this model only detects the fixed classes it was trained on (Gun, Explosive,
Grenade, Knife) but with much higher precision on real firearms and blades.

The weights default to the Hugging Face repo ``Subh775/Threat-Detection-YOLOv8n``
and are downloaded/cached on first use. Override with the ``YOLO_MODEL_REPO`` /
``YOLO_MODEL_FILE`` env vars, or point ``YOLO_MODEL_PATH`` at a local ``.pt``
file to use your own trained weights.
"""

import logging
import os
from pathlib import Path
from typing import Any

# torch must be imported before decord on Windows, otherwise decord's DLLs
# break torch's c10.dll load order (WinError 1114).
import torch  # noqa: F401
from decord import VideoReader, cpu
from PIL import Image

# Reuse the pure timestamp helpers and defaults from the CLI.
from ml.vid_img import (
    HF_TOKEN,
    WEAPON_REPORT_CONFIDENCE,
    format_seconds,
    frame_to_seconds,
    sample_frame_indices,
    weapon_summary,
    weapon_verdict,
)

logger = logging.getLogger(__name__)

YOLO_MODEL_REPO = os.getenv("YOLO_MODEL_REPO", "Subh775/Threat-Detection-YOLOv8n")
YOLO_MODEL_FILE = os.getenv("YOLO_MODEL_FILE", "weights/best.pt")
# If set, use a local .pt file instead of downloading from the Hub.
YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH")

_model: Any = None
_model_id: str = ""


def _load_model() -> tuple[Any, str]:
    """Lazily download/load and cache the YOLO weapon model."""
    global _model, _model_id
    if _model is None:
        from ultralytics import YOLO

        if YOLO_MODEL_PATH:
            weights_path = YOLO_MODEL_PATH
            _model_id = YOLO_MODEL_PATH
        else:
            from huggingface_hub import hf_hub_download

            logger.info(
                f"Downloading YOLO weapon model: {YOLO_MODEL_REPO}/{YOLO_MODEL_FILE}"
            )
            weights_path = hf_hub_download(
                repo_id=YOLO_MODEL_REPO,
                filename=YOLO_MODEL_FILE,
                token=HF_TOKEN,
            )
            _model_id = f"{YOLO_MODEL_REPO}/{YOLO_MODEL_FILE}"

        logger.info(f"Loading YOLO model from {weights_path}")
        _model = YOLO(weights_path)
        logger.info("YOLO model loaded")
    return _model, _model_id


def detect_weapons_yolo(
    video_path: Path,
    num_frames: int = 24,
    score_threshold: float = WEAPON_REPORT_CONFIDENCE,
) -> dict[str, Any]:
    """Detect weapons in a video with a fine-tuned YOLOv8 model.

    Samples ``num_frames`` evenly across the video and runs YOLO detection on
    each frame, returning every detection above ``score_threshold`` with a
    bounding box, class label, confidence and timestamp.

    ``weapon_detected`` is a separate judgement from the box list: it asks
    whether the boxes corroborate each other (see ``vid_img.weapon_verdict``)
    rather than whether any box exists at all, so one weak hit on a ball no
    longer marks the clip as armed.

    Args:
        video_path: Path to a readable video file.
        num_frames: Number of frames to sample across the whole video.
        score_threshold: Minimum confidence to keep a detection.

    Returns:
        A dict matching ``schemas.detection.VideoDetectionResponse``. The
        ``queries`` field holds the model's trainable class names (the fixed
        vocabulary), since YOLO does not take free-text queries.

    Raises:
        ValueError: If no frames could be sampled from the video.
    """
    model, model_id = _load_model()
    class_names: dict[int, str] = dict(model.names)

    reader = VideoReader(str(video_path), ctx=cpu(0))
    fps = float(reader.get_avg_fps())
    total_frames = len(reader)
    indices = sample_frame_indices(0, total_frames, num_frames)
    if indices.size == 0:
        raise ValueError("No frames could be sampled from the input video.")

    frames = reader.get_batch(indices.tolist()).asnumpy()

    detections: list[dict[str, Any]] = []
    for offset, frame_idx in enumerate(indices.tolist()):
        image = Image.fromarray(frames[offset])
        # verbose=False keeps ultralytics from printing per-frame logs.
        results = model.predict(image, conf=score_threshold, verbose=False)
        second = frame_to_seconds(frame_idx, fps)

        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            for box in boxes:
                class_id = int(box.cls[0])
                label = class_names.get(class_id, str(class_id))
                score = float(box.conf[0])
                xyxy = box.xyxy[0].tolist()
                detections.append(
                    {
                        "second": round(second, 2),
                        "timestamp": format_seconds(second),
                        "label": label,
                        "score": round(score, 4),
                        "box_xyxy": [round(float(coord), 1) for coord in xyxy],
                    }
                )

    detections.sort(key=lambda det: det["score"], reverse=True)

    label_counts: dict[str, int] = {}
    for det in detections:
        label_counts[det["label"]] = label_counts.get(det["label"], 0) + 1

    positive = weapon_verdict(detections)
    if detections and not positive:
        logger.info(
            "yolo: %d box(es) did not corroborate — reporting no weapon (%s)",
            len(detections),
            ", ".join(f"{n} x{c}" for n, c in list(label_counts.items())[:4]),
        )

    return {
        "model_id": model_id,
        "queries": [class_names[i] for i in sorted(class_names)],
        "score_threshold": score_threshold,
        "frames_scanned": int(indices.size),
        "detection_count": len(detections),
        "label_counts": label_counts,
        "weapon_detected": positive,
        "summary": weapon_summary(detections, label_counts, positive),
        "detections": detections,
    }
