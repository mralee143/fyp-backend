"""
Draw bounding boxes on an incident clip.

Runs YOLOv8 (COCO) over the extracted clip to localise the people / vehicles /
weapons involved, draws labelled boxes on each frame, and re-encodes a
browser-friendly (h264) annotated clip. Uses the RTX 4060 when available.
"""

import logging
import os
import subprocess
import uuid
from pathlib import Path

import cv2
import imageio_ffmpeg

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "media/clips"))

# COCO class id -> label we keep (people, vehicles, knife).
_KEEP = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    43: "knife",
}
# Red for people/weapons (the "violence"), orange for vehicles.
_RED = (0, 0, 255)
_ORANGE = (0, 165, 255)

_model = None


def _get_model():
    global _model
    if _model is None:
        from ultralytics import YOLO

        logger.info("Loading YOLOv8 (COCO) for box annotation")
        _model = YOLO("yolov8n.pt")  # auto-downloads ~6 MB on first use
    return _model


def annotate_clip(clip_fs_path: str) -> str | None:
    """Draw boxes on a clip file; return the boxed clip's public /media path."""
    try:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        model = _get_model()

        cap = cv2.VideoCapture(clip_fs_path)
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        tmp_path = MEDIA_DIR / f"{uuid.uuid4().hex}_tmp.mp4"
        writer = cv2.VideoWriter(
            str(tmp_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h)
        )

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            res = model.predict(frame, verbose=False, conf=0.35)[0]
            for box in res.boxes:
                cid = int(box.cls[0])
                if cid not in _KEEP:
                    continue
                label = _KEEP[cid]
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                color = _RED if label in ("person", "knife") else _ORANGE
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    frame,
                    label,
                    (x1, max(14, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    2,
                )
            writer.write(frame)

        cap.release()
        writer.release()

        # Re-encode to h264 so browsers can play it.
        out_name = f"{uuid.uuid4().hex}_boxed.mp4"
        out_path = MEDIA_DIR / out_name
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run(
            [
                ffmpeg, "-y", "-i", str(tmp_path),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(out_path),
            ],
            capture_output=True,
            timeout=180,
        )
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return f"/media/clips/{out_name}"
    except Exception as e:  # pragma: no cover - best-effort
        logger.error("annotate_clip failed: %s", e)
        return None


def annotate_segments(segments: list[dict]) -> None:
    """Replace each segment's clip with a box-annotated version, in place."""
    for seg in segments:
        url = seg.get("clip_url")
        if not url:
            continue
        boxed = annotate_clip(url.lstrip("/"))  # "/media/clips/x" -> "media/clips/x"
        if boxed:
            seg["clip_url"] = boxed
