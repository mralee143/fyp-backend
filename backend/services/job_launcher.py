"""
Queue an analysis job from a file already on disk.

`POST /detection/jobs` accepts an upload and queues it. The agent chat needs the
same pipeline — live progress, frames streamed as they are extracted — but it
has *already* written the video to `media/chat/<user_id>/` so the agent's tools
can seek and sample it. Re-posting the bytes would upload a 200 MB file twice.

This takes the saved path instead, so one upload feeds both: the chat keeps its
local copy for the agent, and the worker gets a queued job to stream from.
"""

import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from services import events, media_store, queue
from services.reference_data import get_model_id

logger = logging.getLogger(__name__)

VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}

_HASH_CHUNK_BYTES = 1024 * 1024


def _checksum(path: Path) -> tuple[str, int]:
    """SHA-256 and byte size of a file, read in chunks (never buffered whole)."""
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


async def queue_job_from_path(
    prisma: Any,
    user_id: int,
    path: Path,
    filename: str,
    model: str = "auto",
    queries: Optional[str] = None,
    num_frames: int = 24,
    threshold: float = 0.2,
) -> Optional[dict]:
    """Store a saved video and queue it for analysis.

    Mirrors what `POST /detection/jobs` does, minus the upload handling: the
    same `videos`/`analysis_jobs` rows, the same queued event, so the existing
    SSE stream and worker serve it without knowing where it came from.

    Args:
        prisma: Connected Prisma client.
        user_id: Owner of the video and job.
        path: The video on local disk.
        filename: Original filename, shown in the UI.
        model: Detector code — auto | yolo | owlv2 | llm | qwen | action.
        queries: OWLv2 only, comma-separated objects to look for.
        num_frames: Frames to sample across the video.
        threshold: Minimum detection confidence.

    Returns:
        ``{"job_id", "queued", "reused_video", "events_url"}``, or None if the
        job could not be created — analysis is a bonus on top of the upload, so
        callers should treat failure as non-fatal.
    """
    try:
        extension = path.suffix.lower()
        mime = VIDEO_MIME.get(extension, "video/mp4")
        checksum, size = _checksum(path)

        # Same bytes, same user -> reuse the stored object rather than a copy.
        video = await prisma.video.find_first(
            where={"userId": user_id, "checksumSha256": checksum}
        )
        reused = video is not None

        if video is None:
            object_key = media_store.video_key(user_id, extension)
            await media_store.put_file(object_key, str(path), mime)
            video = await prisma.video.create(
                data={
                    "userId": user_id,
                    "originalFilename": filename,
                    "objectKey": object_key,
                    "mimeType": mime,
                    "fileSize": size,
                    "checksumSha256": checksum,
                }
            )

        job = await prisma.analysisjob.create(
            data={
                "publicId": str(uuid.uuid4()),
                "videoId": video.id,
                "modelId": await get_model_id(prisma, model),
                "status": "QUEUED",
                "stage": "queued",
                "progress": 0,
                "numFrames": num_frames,
                "scoreThreshold": threshold,
            }
        )

        if model == "owlv2" and queries and queries.strip():
            for ordinal, text in enumerate(
                q.strip() for q in queries.split(",") if q.strip()
            ):
                await prisma.jobquery.create(
                    data={"jobId": job.id, "ordinal": ordinal, "text": text}
                )

        await events.emit(
            prisma,
            job,
            events.JOB_QUEUED,
            stage="queued",
            progress=0,
            message=f"{filename} queued for analysis.",
        )

        arq_job_id = await queue.enqueue_analysis(job.id, job.publicId)
        if arq_job_id:
            await prisma.analysisjob.update(
                where={"id": job.id}, data={"arqJobId": arq_job_id}
            )
        else:
            logger.error(
                "Job %s could not be queued — is the worker's Redis up?", job.publicId
            )

        logger.info(
            "User %s queued chat job %s (%s, model=%s, reused=%s)",
            user_id,
            job.publicId,
            filename,
            model,
            reused,
        )
        return {
            "job_id": job.publicId,
            "queued": arq_job_id is not None,
            "reused_video": reused,
            "events_url": f"/detection/jobs/{job.publicId}/events",
        }
    except Exception as e:
        logger.error("Could not queue job for %s: %s", filename, e, exc_info=True)
        return None
