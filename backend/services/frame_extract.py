"""
Grab the exact frame at a given moment of a video.

Where services/clip_extract.py cuts a short mp4 around an incident, this pulls
the single still image at one timestamp — so the chat agent can answer "which
frame shows the accident?" with the picture itself rather than a number.

Uses the ffmpeg binary bundled with `imageio-ffmpeg` (no system install needed).
Frames are written under MEDIA_DIR and served by FastAPI at /media/frames/<name>.
"""

import logging
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import imageio_ffmpeg

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Video probing and frame sampling (used by the analysis worker)
# --------------------------------------------------------------------------


@dataclass
class VideoInfo:
    """What we can learn about a video without decoding all of it."""

    duration_seconds: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    frame_count: int = 0


@dataclass
class Frame:
    """One decoded frame, already JPEG-encoded and ready for object storage."""

    sequence: int
    timestamp_seconds: float
    jpeg: bytes
    width: int
    height: int


def probe(video_path: str) -> VideoInfo:
    """Read a video's dimensions, frame rate and duration.

    Never raises: an unreadable file yields a zeroed VideoInfo, so a probe
    failure degrades the metadata rather than killing the analysis job.
    """
    try:
        import cv2

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            logger.warning("probe: could not open %s", video_path)
            return VideoInfo()
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            return VideoInfo(
                duration_seconds=round(count / fps, 2) if fps > 0 and count > 0 else 0.0,
                width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
                height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
                fps=round(fps, 3),
                frame_count=count,
            )
        finally:
            capture.release()
    except Exception as e:  # pragma: no cover - best-effort
        logger.error("probe failed for %s: %s", video_path, e)
        return VideoInfo()


def _encode(image, sequence: int, seconds: float) -> Optional[Frame]:
    """JPEG-encode a decoded BGR image into a Frame."""
    import cv2

    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        return None
    height, width = image.shape[:2]
    return Frame(
        sequence=sequence,
        timestamp_seconds=round(seconds, 2),
        jpeg=buffer.tobytes(),
        width=int(width),
        height=int(height),
    )


def iter_frames(video_path: str, wanted: int) -> Iterator[Frame]:
    """Yield ``wanted`` frames sampled evenly across the video.

    Yields as it decodes rather than returning a list, so the caller can push
    each preview frame to the browser the moment it exists instead of waiting
    for the whole sample.
    """
    try:
        import cv2

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            logger.warning("iter_frames: could not open %s", video_path)
            return
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if count <= 0 or wanted <= 0:
                return

            step = max(count // max(wanted, 1), 1)
            sequence = 0
            for index in range(0, count, step):
                if sequence >= wanted:
                    break
                capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, image = capture.read()
                if not ok or image is None:
                    continue
                frame = _encode(image, sequence, index / fps if fps > 0 else 0.0)
                if frame is not None:
                    sequence += 1
                    yield frame
        finally:
            capture.release()
    except Exception as e:  # pragma: no cover - best-effort
        logger.error("iter_frames failed for %s: %s", video_path, e)


def frame_at(video_path: str, second: float) -> Optional[Frame]:
    """Decode the single frame at ``second``, JPEG-encoded in memory.

    The in-memory counterpart of ``extract_frame`` below: this one is for the
    worker, which uploads the bytes to object storage rather than writing a
    file the web server serves.
    """
    try:
        import cv2

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            return None
        try:
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(second)) * 1000.0)
            ok, image = capture.read()
            if not ok or image is None:
                return None
            return _encode(image, 0, float(second))
        finally:
            capture.release()
    except Exception as e:  # pragma: no cover - best-effort
        logger.error("frame_at failed for %s at %.2fs: %s", video_path, second, e)
        return None

MEDIA_DIR = Path(os.getenv("FRAME_MEDIA_DIR", "media/frames"))

# Cap on how many frames one request may extract, so a chatty agent can't spend
# minutes shelling out to ffmpeg.
MAX_FRAMES_PER_CALL = 8

# Box colours (BGR, because OpenCV). Red marks the threat itself, orange the
# people/vehicles around it.
_RED = (0, 0, 255)
_ORANGE = (0, 165, 255)

# COCO classes worth boxing when we have to localise an incident ourselves —
# the same subset services/annotate.py draws on incident clips.
_COCO_KEEP = {0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck", 43: "knife"}


def describe_location(box: list[float], width: int, height: int) -> str:
    """Say where in the frame a box sits, in words.

    A bounding box answers "where" precisely but unreadably; this turns it into
    the phrase an operator would use ("centre-left, upper third"), which is what
    the agent repeats back in the conversation.
    """
    if not box or len(box) < 4 or width <= 0 or height <= 0:
        return ""

    cx = (float(box[0]) + float(box[2])) / 2 / width
    cy = (float(box[1]) + float(box[3])) / 2 / height

    horizontal = "left" if cx < 0.34 else "right" if cx > 0.66 else "centre"
    vertical = "top" if cy < 0.34 else "bottom" if cy > 0.66 else "middle"

    if horizontal == "centre" and vertical == "middle":
        return "centre of frame"
    if horizontal == "centre":
        return f"{vertical}-centre of frame"
    if vertical == "middle":
        return f"{horizontal} of frame"
    return f"{vertical}-{horizontal} of frame"


def annotate_frame(fs_path: str, boxes: list[dict]) -> tuple[int, int] | None:
    """Draw labelled boxes onto a saved frame, in place.

    Args:
        fs_path: Filesystem path of the JPEG to draw on.
        boxes: Dicts with ``box_xyxy`` and optionally ``label``/``score``/
            ``threat`` (threat boxes are drawn red, the rest orange).

    Returns:
        The image's (width, height), or None if it could not be read.
    """
    try:
        import cv2

        image = cv2.imread(fs_path)
        if image is None:
            return None
        height, width = image.shape[:2]

        for box in boxes:
            xyxy = box.get("box_xyxy")
            if not xyxy or len(xyxy) < 4:
                continue
            x1, y1, x2, y2 = (int(round(float(v))) for v in xyxy[:4])
            colour = _RED if box.get("threat", True) else _ORANGE
            cv2.rectangle(image, (x1, y1), (x2, y2), colour, 2)

            label = str(box.get("label") or "")
            score = box.get("score")
            if score is not None:
                label = f"{label} {float(score) * 100:.0f}%".strip()
            if label:
                # Caption above the box, or just inside it when the box starts
                # at the top edge and there is no room above.
                text_y = y1 - 6 if y1 > 20 else min(y1 + 18, height - 4)
                cv2.putText(
                    image, label, (max(x1, 2), text_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 2, cv2.LINE_AA,
                )

        cv2.imwrite(fs_path, image)
        return width, height
    except Exception as e:  # pragma: no cover - best-effort
        logger.error("Frame annotation failed for %s: %s", fs_path, e)
        return None


def localize_frame(fs_path: str) -> list[dict]:
    """Find people/vehicles/weapons in a still, for results that carry no boxes.

    The action and VLM detectors describe *when* something happens but not
    *where*; running the COCO model on the incident frame recovers the location
    so the agent can point at it.
    """
    try:
        from services.annotate import _get_model

        model = _get_model()
        results = model.predict(fs_path, conf=0.35, verbose=False)
        boxes: list[dict] = []
        for result in results:
            for box in result.boxes or []:
                class_id = int(box.cls[0])
                label = _COCO_KEEP.get(class_id)
                if not label:
                    continue
                boxes.append(
                    {
                        "label": label,
                        "score": round(float(box.conf[0]), 3),
                        "box_xyxy": [round(float(v), 1) for v in box.xyxy[0].tolist()],
                        # People and blades are the subjects of the incident;
                        # vehicles are context.
                        "threat": label in ("person", "knife"),
                    }
                )
        boxes.sort(key=lambda b: b["score"], reverse=True)
        return boxes[:6]
    except Exception as e:  # pragma: no cover - best-effort
        logger.error("Frame localisation failed for %s: %s", fs_path, e)
        return []


def extract_frame(video_path: str, second: float) -> str | None:
    """Write the frame at ``second`` to a JPEG.

    Args:
        video_path: Path to a readable video file.
        second: Offset into the video, in seconds.

    Returns:
        The frame's public path ("/media/frames/<name>.jpg"), or None if
        extraction failed (a bad timestamp or an unreadable file).
    """
    try:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

        name = f"{uuid.uuid4().hex}.jpg"
        out_path = MEDIA_DIR / name

        cmd = [
            ffmpeg,
            "-y",
            # -ss before -i seeks by keyframe: fast, and accurate enough that
            # the still lands inside the incident.
            "-ss",
            f"{max(0.0, float(second)):.2f}",
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-q:v",
            "3",  # visually lossless enough for review, small on the wire
            str(out_path),
        ]
        subprocess.run(cmd, capture_output=True, timeout=60, check=True)

        if not out_path.exists() or out_path.stat().st_size == 0:
            # Seeking past the end produces no frame and no error.
            logger.info("No frame at %.2fs in %s", second, video_path)
            return None
        return f"/media/frames/{name}"
    except Exception as e:  # pragma: no cover - best-effort
        logger.error("Frame extraction failed at %.2fs: %s", second, e)
        return None


def extract_frames_for_moments(
    video_path: str, moments: list[dict], limit: int = MAX_FRAMES_PER_CALL
) -> int:
    """Add a boxed ``frame_url`` and a ``location`` phrase to each moment.

    Handles both result shapes: object detections carry ``second`` plus their
    own ``box_xyxy``, incident segments carry ``start_time``/``end_time`` (we
    take the midpoint, the most representative still) and no box — for those we
    localise the subjects on the frame ourselves.

    Args:
        video_path: The video the moments came from.
        moments: Detection or segment dicts, mutated in place.
        limit: Maximum number of frames to extract.

    Returns:
        How many frames were successfully written.
    """
    extracted = 0
    for moment in moments[:limit]:
        if "second" in moment:
            at = float(moment.get("second") or 0.0)
        elif moment.get("peak_second") is not None:
            # Prefer where the detector actually peaked: a merged incident span
            # can cover the whole video, making its midpoint meaningless.
            at = float(moment["peak_second"])
        else:
            start = float(moment.get("start_time") or 0.0)
            end = float(moment.get("end_time") or start)
            at = start + (end - start) / 2 if end > start else start

        url = extract_frame(video_path, at)
        if not url:
            continue

        moment["frame_url"] = url
        moment["frame_second"] = round(at, 2)
        extracted += 1

        # Mark where in the frame it happened. A detector box is exact; without
        # one, fall back to locating the people/weapons on that still.
        fs_path = url.lstrip("/")
        own_box = moment.get("box_xyxy")
        boxes = (
            [{"box_xyxy": own_box, "label": moment.get("label"), "score": moment.get("score"), "threat": True}]
            if own_box
            else localize_frame(fs_path)
        )
        if not boxes:
            continue

        size = annotate_frame(fs_path, boxes)
        if size:
            width, height = size
            primary = boxes[0]
            moment["location"] = describe_location(primary["box_xyxy"], width, height)
            moment["boxes"] = [
                {"label": b.get("label"), "box_xyxy": b.get("box_xyxy"), "score": b.get("score")}
                for b in boxes
            ]
    return extracted
