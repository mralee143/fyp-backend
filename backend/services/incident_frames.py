"""
The stills that make an incident inspectable.

A flagged span like 0:50–0:55 is only a pair of numbers until someone can see
what happened inside it. This module captures a strip of frames across that
window and stores each one against its segment, so the report can show the
incident and the chat agent can answer "which frame shows it?" with the picture
and its exact video frame number.

Two entry points, one shared strip:

  * the worker calls `store_incident_frame` while the uploaded file is still on
    disk — the cheap path, taken for every new analysis;
  * `ensure_incident_frames` backfills the same strip on demand for a scan that
    predates this (or whose extraction failed), pulling the video back out of
    object storage once and reusing it for every frame.

Frame numbers are derived from `capturedAtSeconds * fps` rather than stored:
the timestamp and the frame rate are both already recorded, so a column holding
their product would be a value the database has to keep true twice.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from config import settings
from services import frame_extract, media_store

logger = logging.getLogger(__name__)

# Below this many stills an incident cannot be discussed frame by frame, so a
# chat request backfills the strip rather than reasoning from one picture. Two
# is not enough: a five-second incident sampled at the video's rate can easily
# land two frames outside the action.
MIN_USABLE_FRAMES = 3


def incident_frame_seconds(
    start: float, end: float, count: int, peak: Optional[float] = None
) -> list[float]:
    """Offsets to sample across one incident, the representative second first.

    The lead frame is the cover: it is what the report card, the chat and the
    dashboard all show when they show one picture. That should be the moment the
    event actually reads — the punch, the impact — which is what `peak` carries
    when the backend reported one. Without a peak the midpoint is the best
    available guess, and on a span that only got long by merging it can easily
    be the lull between two blows, which is why the peak is worth threading
    through from the model.

    The rest spread evenly and exclude the exact edges, where a seek can land on
    the neighbouring shot.
    """
    start, end = float(start), max(float(end), float(start))
    midpoint = round(start + (end - start) / 2, 2)
    cover = midpoint if peak is None else round(min(max(float(peak), start), end), 2)
    if count <= 1 or end <= start:
        return [cover]

    span = end - start
    seconds = [cover]
    for index in range(count - 1):
        # count-1 points at the centres of equal slices, so nothing sits on an
        # edge frame.
        at = round(start + span * (index + 0.5) / (count - 1), 2)
        if all(abs(at - existing) > 0.15 for existing in seconds):
            seconds.append(at)
    return seconds


def frame_index(second: Optional[float], fps: Optional[float]) -> Optional[int]:
    """Which frame of the video a timestamp lands on, or None without an fps."""
    if second is None or not fps or fps <= 0:
        return None
    return int(round(float(second) * float(fps)))


def describe_frame(second: Optional[float], fps: Optional[float]) -> str:
    """"0:52.30 (frame #1569)" — how a still is referred to in prose."""
    if second is None:
        return "unknown time"
    minutes, remainder = divmod(float(second), 60)
    text = f"{int(minutes)}:{remainder:05.2f}"
    index = frame_index(second, fps)
    return f"{text} (frame #{index})" if index is not None else text


async def store_incident_frame(
    prisma: Any, video: Any, segment: Any, video_path: str, second: float
) -> Optional[Any]:
    """Capture one still inside an incident and record it against the segment.

    Returns the created image row, or None when the frame could not be decoded
    (a seek past the end produces no frame and no error).
    """
    frame = await asyncio.to_thread(frame_extract.frame_at, video_path, second)
    if frame is None:
        return None

    # Timestamp-keyed, so a backfill of a partially-captured incident cannot
    # collide with the frames already stored for it.
    object_key = f"frames/{video.id}/incidents/{segment.id}_{int(second * 100):07d}.jpg"
    await media_store.put_bytes(object_key, frame.jpeg, "image/jpeg")
    return await prisma.image.create(
        data={
            "kind": "THUMBNAIL",
            "videoId": video.id,
            "segmentId": segment.id,
            "objectKey": object_key,
            "generatedFilename": f"incident_{segment.id}_{int(second * 100):07d}.jpg",
            "mimeType": "image/jpeg",
            "fileSize": len(frame.jpeg),
            "width": frame.width,
            "height": frame.height,
            "capturedAtSeconds": round(float(second), 2),
            "caption": segment.description or None,
        }
    )


async def stored_frames(prisma: Any, segment_id: int) -> list[Any]:
    """Every still already held for one incident, in time order."""
    return await prisma.image.find_many(
        where={"segmentId": segment_id},
        order=[{"capturedAtSeconds": "asc"}],
    )


async def ensure_incident_frames(prisma: Any, video: Any, segment: Any) -> list[Any]:
    """The incident's stills, capturing them from object storage if missing.

    Scans analysed before this strip existed carry a single cover image; asking
    one of those about an exact frame would otherwise be answered from one
    picture. Downloading the video costs seconds and happens once — the frames
    are stored, so the next question is served from the database.
    """
    existing = await stored_frames(prisma, segment.id)
    if len(existing) >= MIN_USABLE_FRAMES or not getattr(video, "objectKey", None):
        return existing

    captured = {
        round(float(image.capturedAtSeconds), 2)
        for image in existing
        if image.capturedAtSeconds is not None
    }
    wanted = [
        second
        for second in incident_frame_seconds(
            segment.startTime,
            segment.endTime,
            settings.incident_frame_count,
            getattr(segment, "peakSecond", None),
        )
        if all(abs(second - already) > 0.15 for already in captured)
    ]
    if not wanted:
        return existing

    suffix = Path(video.objectKey).suffix or ".mp4"
    handle, local_path = tempfile.mkstemp(suffix=suffix)
    os.close(handle)
    try:
        await asyncio.to_thread(media_store.fget_sync, video.objectKey, local_path)
        for second in wanted:
            try:
                await store_incident_frame(prisma, video, segment, local_path, second)
            except Exception as exc:
                logger.info(
                    "Backfill of segment %s at %.2fs failed: %s", segment.id, second, exc
                )
    except Exception as exc:
        logger.info("Could not fetch video for segment %s: %s", segment.id, exc)
        return existing
    finally:
        try:
            os.remove(local_path)
        except OSError:
            pass

    return await stored_frames(prisma, segment.id)
