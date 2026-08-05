"""
Cut the exact incident clip out of an uploaded video using ffmpeg.

Uses the ffmpeg binary bundled with `imageio-ffmpeg` (no system install needed).
Clips are written under MEDIA_DIR and served by FastAPI at /media/clips/<name>.
"""

import logging
import os
import subprocess
import uuid
from pathlib import Path

import imageio_ffmpeg

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "media/clips"))
PAD_SECONDS = 0.75  # a little context before/after the incident


def extract_clip(video_path: str, start: float, end: float) -> str | None:
    """
    Cut [start-pad, end+pad] from the video into an mp4.

    Returns the clip's public path ("/media/clips/<name>") or None on failure.
    """
    try:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

        clip_start = max(0.0, float(start) - PAD_SECONDS)
        duration = max(1.0, (float(end) - float(start)) + 2 * PAD_SECONDS)
        name = f"{uuid.uuid4().hex}.mp4"
        out_path = MEDIA_DIR / name

        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{clip_start:.2f}",
            "-i",
            video_path,
            "-t",
            f"{duration:.2f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-movflags",
            "+faststart",
            "-an",  # drop audio — smaller, browser-safe
            str(out_path),
        ]
        subprocess.run(cmd, capture_output=True, timeout=180, check=True)
        return f"/media/clips/{name}"
    except Exception as e:  # pragma: no cover - best-effort
        logger.error("Clip extraction failed (%s-%s): %s", start, end, e)
        return None


def extract_clips_for_segments(video_path: str, segments: list[dict]) -> None:
    """Add a `clip_url` to each segment in place."""
    for seg in segments:
        try:
            url = extract_clip(video_path, seg.get("start_time", 0), seg.get("end_time", 0))
            if url:
                seg["clip_url"] = url
        except Exception as e:
            logger.error("clip for segment failed: %s", e)
