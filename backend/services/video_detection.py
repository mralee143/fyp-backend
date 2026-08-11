"""
Video weapon/object detection service.

Wraps the OWLv2 zero-shot open-vocabulary detector so it can be reused by
API routes. Unlike the CLI in ``vid_img.py`` (which loads the model on every
run), this service loads the model once and caches it in module globals for
fast repeated inference.
"""

import logging
from pathlib import Path
from typing import Any, Sequence

import torch
from decord import VideoReader, cpu
from PIL import Image

# Reuse the pure helpers and defaults from the CLI so behaviour stays in sync.
from ml.vid_img import (
    DEFAULT_OBJECT_QUERIES,
    HF_TOKEN,
    OBJECT_MODEL_ID,
    format_seconds,
    frame_to_seconds,
    sample_frame_indices,
)

logger = logging.getLogger(__name__)

_processor: Any = None
_model: Any = None
_device = "cuda" if torch.cuda.is_available() else "cpu"


def _load_model(model_id: str) -> tuple[Any, Any]:
    """Lazily load and cache the OWLv2 processor and model."""
    global _processor, _model
    if _model is None:
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        logger.info(f"Loading OWLv2 model on {_device}: {model_id}")
        _processor = Owlv2Processor.from_pretrained(model_id, token=HF_TOKEN)
        _model = Owlv2ForObjectDetection.from_pretrained(model_id, token=HF_TOKEN)
        _model.to(_device)
        _model.eval()
        logger.info("OWLv2 model loaded")
    return _processor, _model


def detect_weapons(
    video_path: Path,
    queries: Sequence[str] = DEFAULT_OBJECT_QUERIES,
    num_frames: int = 24,
    score_threshold: float = 0.2,
    model_id: str = OBJECT_MODEL_ID,
) -> dict[str, Any]:
    """Scan a video for weapons/objects using OWLv2 zero-shot detection.

    Samples ``num_frames`` evenly across the video and runs open-vocabulary
    detection for every text query on each frame.

    Args:
        video_path: Path to a readable video file.
        queries: Text descriptions to look for (e.g. "a gun", "a knife").
        num_frames: Number of frames to sample across the whole video.
        score_threshold: Minimum confidence to keep a detection.
        model_id: Hugging Face OWLv2 checkpoint id.

    Returns:
        A dict with per-detection results, per-label counts and a top-level
        ``weapon_detected`` flag. Matches ``schemas.detection.VideoDetectionResponse``.

    Raises:
        ValueError: If no frames could be sampled from the video.
    """
    processor, model = _load_model(model_id)

    reader = VideoReader(str(video_path), ctx=cpu(0))
    fps = float(reader.get_avg_fps())
    total_frames = len(reader)
    indices = sample_frame_indices(0, total_frames, num_frames)
    if indices.size == 0:
        raise ValueError("No frames could be sampled from the input video.")

    frames = reader.get_batch(indices.tolist()).asnumpy()
    text_labels = [list(queries)]

    detections: list[dict[str, Any]] = []
    for offset, frame_idx in enumerate(indices.tolist()):
        image = Image.fromarray(frames[offset])
        inputs = processor(
            text=text_labels, images=image, return_tensors="pt"
        ).to(_device)
        with torch.inference_mode():
            outputs = model(**inputs)

        target_sizes = torch.tensor(
            [(image.height, image.width)], device=_device
        )
        result = processor.post_process_grounded_object_detection(
            outputs=outputs,
            target_sizes=target_sizes,
            threshold=score_threshold,
            text_labels=text_labels,
        )[0]

        boxes = result["boxes"].tolist()
        scores = result["scores"].tolist()
        raw_labels = result.get("text_labels")
        if raw_labels is None:
            raw_labels = [queries[int(i)] for i in result["labels"].tolist()]

        second = frame_to_seconds(frame_idx, fps)
        for box, score, label in zip(boxes, scores, raw_labels):
            detections.append(
                {
                    "second": round(second, 2),
                    "timestamp": format_seconds(second),
                    "label": label,
                    "score": round(float(score), 4),
                    "box_xyxy": [round(float(coord), 1) for coord in box],
                }
            )

    detections.sort(key=lambda det: det["score"], reverse=True)

    label_counts: dict[str, int] = {}
    for det in detections:
        label_counts[det["label"]] = label_counts.get(det["label"], 0) + 1

    return {
        "model_id": model_id,
        "queries": list(queries),
        "score_threshold": score_threshold,
        "frames_scanned": int(indices.size),
        "detection_count": len(detections),
        "label_counts": label_counts,
        "weapon_detected": len(detections) > 0,
        "detections": detections,
    }
