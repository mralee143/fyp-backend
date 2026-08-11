"""
ARQ worker — runs every video analysis outside the request cycle.

Start it alongside the API:

    python -m arq worker.WorkerSettings

Pipeline order is chosen for perceived speed, not convenience. Frames are
sampled, uploaded and announced *before* any model is loaded, so the browser
paints real imagery within a second of the upload finishing while the detector
is still warming up. Everything after that streams in as it is produced:

    0-5%    download the video from object storage, probe its metadata
    5-30%   sample N frames -> MinIO -> images row -> emit frame.ready (each)
    30-70%  run the detection backend (or the auto cascade)
    70-90%  per incident: cut a clip, draw boxes, caption the frame,
            emit segment.ready
    90-100% caption the remaining frames, emit job.completed

Every step announces itself through services.events, which fans out to the
browser's SSE stream and to any registered webhook endpoint at the same time.
"""

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from arq.connections import RedisSettings
from prisma import Prisma

from config import settings
from services import cache, events, frame_extract, media_store, scan_writer
from services.annotate import annotate_clip
from services.clip_extract import extract_clip
from services.detection_runner import model_code_from_result, run_detection
from services.explain import explain_segments
from services.incident_frames import incident_frame_seconds, store_incident_frame
from services.queue import QUEUE_NAME, redis_settings
from services.reference_data import seed_reference_data

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s"
)
logger = logging.getLogger("worker")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _set_stage(
    prisma: Prisma, job: Any, stage: str, progress: int, message: str
) -> Any:
    """Move the job to a new stage and announce it."""
    job = await prisma.analysisjob.update(
        where={"id": job.id}, data={"stage": stage, "progress": progress}
    )
    await events.emit(
        prisma, job, events.JOB_PROGRESS, stage=stage, progress=progress, message=message
    )
    return job


def _caption_for(
    timestamp: float,
    segments: list[dict],
    objects: Optional[str] = None,
) -> tuple[Optional[str], Optional[int]]:
    """Write the per-image summary stored alongside each frame.

    Three sources, most specific first:
      1. the incident the frame falls inside (segment backends),
      2. the objects the detector boxed on that frame (yolo/owlv2),
      3. otherwise, an explicit statement that nothing was flagged.

    Returns (caption, segment_id).
    """
    for segment in segments:
        if segment["start"] <= timestamp <= segment["end"]:
            label = segment["label"]
            description = (segment["description"] or "").strip()
            return (f"{label} — {description}" if description else label), segment["id"]

    minutes, seconds = divmod(int(timestamp), 60)
    if objects:
        return f"{objects} at {minutes}:{seconds:02d}", None
    return f"Clear — nothing flagged at {minutes}:{seconds:02d}", None


async def _link_detections_to_frames(
    prisma: Prisma, video_id: int, scan_id: int
) -> dict[int, str]:
    """Attach every object box to the frame it is nearest in time.

    `detections.image_id` is what lets the report draw boxes over a still, and
    it is also how a frame earns an object caption. Both are done in single
    statements — an owlv2 pass can produce several hundred boxes, and a
    round-trip each would cost more than the detection did.

    Returns {image_id: "Gun x3 (91%)"} for captioning.
    """
    try:
        await prisma.execute_raw(
            """
            UPDATE detections d
            SET image_id = nearest.image_id
            FROM (
                SELECT DISTINCT ON (dd.id)
                       dd.id AS detection_id,
                       img.id AS image_id
                FROM detections dd
                JOIN images img
                  ON img.video_id = $1
                 AND img.kind = 'FRAME'::"ImageKind"
                 AND img.captured_at_seconds IS NOT NULL
                WHERE dd.scan_id = $2
                ORDER BY dd.id, abs(img.captured_at_seconds - dd.second)
            ) AS nearest
            WHERE d.id = nearest.detection_id
            """,
            video_id,
            scan_id,
        )

        rows = await prisma.query_raw(
            """
            SELECT d.image_id, l.name, COUNT(*)::int AS hits, MAX(d.score) AS best
            FROM detections d
            JOIN labels l ON l.id = d.label_id
            WHERE d.scan_id = $1 AND d.image_id IS NOT NULL
            GROUP BY d.image_id, l.name
            ORDER BY d.image_id, hits DESC
            """,
            scan_id,
        )
    except Exception as exc:
        logger.info("Could not link detections to frames: %s", exc)
        return {}

    captions: dict[int, list[str]] = {}
    for row in rows:
        name = str(row["name"]).removeprefix("a ")
        count = f" ×{row['hits']}" if row["hits"] > 1 else ""
        captions.setdefault(row["image_id"], []).append(
            f"{name}{count} ({float(row['best']):.0%})"
        )

    return {image_id: ", ".join(parts[:3]) for image_id, parts in captions.items()}


# --------------------------------------------------------------------------- #
# The job
# --------------------------------------------------------------------------- #


async def analyze_video(ctx: dict, job_id: int) -> dict:
    """Analyse one uploaded video end to end.

    Args:
        ctx: ARQ context; carries the shared Prisma connection.
        job_id: `analysis_jobs.id` — every run parameter is read from that row.

    Returns:
        A small summary dict (ARQ stores it as the job result).
    """
    prisma: Prisma = ctx["prisma"]

    job = await prisma.analysisjob.find_unique(
        where={"id": job_id}, include={"video": True, "model": True, "queries": True}
    )
    if job is None:
        logger.error("Job %s no longer exists", job_id)
        return {"ok": False, "error": "job not found"}

    # Read everything relational once, up front. `prisma.update()` returns a
    # record with its relations unset, so anything reached through `job.video`
    # or `job.model` after the first status write would silently be None.
    video = job.video
    user_id = video.userId
    model_code = job.model.code
    model_display = job.model.displayName
    query_texts = tuple(q.text for q in sorted(job.queries or [], key=lambda q: q.ordinal))
    num_frames = job.numFrames
    score_threshold = job.scoreThreshold
    window_seconds = job.windowSeconds
    stride_seconds = job.strideSeconds
    local_path: Optional[str] = None

    try:
        job = await prisma.analysisjob.update(
            where={"id": job.id},
            data={
                "status": "RUNNING",
                "stage": "downloading",
                "progress": 2,
                "startedAt": datetime.now(timezone.utc),
            },
        )
        await events.emit(
            prisma,
            job,
            events.JOB_STARTED,
            stage="downloading",
            progress=2,
            message=f"Analysing {video.originalFilename} with {model_display}.",
        )

        # ---- 0-5%: pull the video back out of object storage --------------- #
        suffix = Path(video.originalFilename).suffix.lower() or ".mp4"
        handle, local_path = tempfile.mkstemp(suffix=suffix)
        os.close(handle)
        await asyncio.to_thread(media_store.fget_sync, video.objectKey, local_path)

        info = await asyncio.to_thread(frame_extract.probe, local_path)
        await prisma.video.update(
            where={"id": video.id},
            data={
                "durationSeconds": info.duration_seconds or None,
                "width": info.width or None,
                "height": info.height or None,
                "fps": info.fps or None,
            },
        )

        # ---- 5-30%: frames first, so the UI has something to show ---------- #
        job = await _set_stage(
            prisma, job, "extracting_frames", 5, "Extracting preview frames…"
        )

        frame_rows: list[tuple[Any, float]] = []
        wanted = settings.frame_sample_count
        for frame in await asyncio.to_thread(
            lambda: list(frame_extract.iter_frames(local_path, wanted))
        ):
            object_key = media_store.frame_key(video.id, frame.sequence)
            await media_store.put_bytes(object_key, frame.jpeg, "image/jpeg")

            image = await prisma.image.create(
                data={
                    "kind": "FRAME",
                    "videoId": video.id,
                    "objectKey": object_key,
                    "generatedFilename": f"{video.id}_{frame.sequence:04d}.jpg",
                    "mimeType": "image/jpeg",
                    "fileSize": len(frame.jpeg),
                    "width": frame.width,
                    "height": frame.height,
                    "sequence": frame.sequence,
                    "capturedAtSeconds": frame.timestamp_seconds,
                }
            )
            frame_rows.append((image, frame.timestamp_seconds))

            # The moment this lands the browser can render the thumbnail.
            progress = 5 + int(25 * (frame.sequence + 1) / max(wanted, 1))
            await events.emit(
                prisma,
                job,
                events.FRAME_READY,
                stage="extracting_frames",
                progress=progress,
                message=f"Frame {frame.sequence + 1} of {wanted}",
                image_id=image.id,
            )

        # ---- 30-70%: run the detector -------------------------------------- #
        job = await _set_stage(prisma, job, "detecting", 30, "Running detection…")

        async def on_progress(percent: int, stage: str, message: str) -> None:
            await events.emit(
                prisma, job, events.JOB_PROGRESS, stage=stage, progress=percent, message=message
            )

        result = await run_detection(
            model_code,
            Path(local_path),
            queries=query_texts or None,
            num_frames=num_frames,
            threshold=score_threshold,
            window_seconds=window_seconds,
            stride_seconds=stride_seconds,
            on_progress=on_progress,
        )

        # ---- 70%: write the normalized result ------------------------------ #
        job = await _set_stage(prisma, job, "saving", 70, "Saving results…")
        produced_by = model_code_from_result(result, model_code)
        scan, segments = await scan_writer.persist_result(prisma, job, result, produced_by)

        # ---- 70-90%: per incident, cut a clip and illustrate it ------------ #
        raw_segments = result.get("segments") or []
        if raw_segments:
            job = await _set_stage(
                prisma, job, "clipping", 72, f"Preparing {len(segments)} incident clip(s)…"
            )
            # Name the objects involved ("a car and a motorcycle") before cutting.
            try:
                await asyncio.to_thread(explain_segments, Path(local_path), raw_segments)
            except Exception as exc:
                logger.info("Explanation step skipped: %s", exc)

        segment_summaries: list[dict] = []
        for index, segment in enumerate(segments):
            raw = raw_segments[index] if index < len(raw_segments) else {}

            if raw.get("explanation"):
                segment = await prisma.segment.update(
                    where={"id": segment.id}, data={"explanation": raw["explanation"]}
                )

            clip_key = await _build_clip(prisma, scan, segment, local_path)
            await _illustrate_segment(prisma, video, segment, local_path)

            segment_summaries.append(
                {
                    "id": segment.id,
                    "start": segment.startTime,
                    "end": segment.endTime,
                    "label": raw.get("label") or "Incident",
                    "description": segment.description,
                }
            )

            progress = 72 + int(18 * (index + 1) / max(len(segments), 1))
            await prisma.analysisjob.update(
                where={"id": job.id}, data={"progress": progress}
            )
            await events.emit(
                prisma,
                job,
                events.SEGMENT_READY,
                stage="clipping",
                progress=progress,
                message=f"Incident {index + 1} of {len(segments)} ready",
                segment_id=segment.id,
            )
            logger.info("segment %s clip=%s", segment.id, clip_key)

        # ---- 90-100%: caption every frame against the finished analysis ---- #
        job = await _set_stage(prisma, job, "captioning", 92, "Captioning frames…")
        object_captions = await _link_detections_to_frames(prisma, video.id, scan.id)
        for image, timestamp in frame_rows:
            caption, segment_id = _caption_for(
                timestamp, segment_summaries, object_captions.get(image.id)
            )
            await prisma.image.update(
                where={"id": image.id},
                data={"caption": caption, "segmentId": segment_id},
            )

        job = await prisma.analysisjob.update(
            where={"id": job.id},
            data={
                "status": "SUCCEEDED",
                "stage": "completed",
                "progress": 100,
                "finishedAt": datetime.now(timezone.utc),
            },
        )
        await cache.invalidate_user(user_id)
        await events.emit(
            prisma,
            job,
            events.JOB_COMPLETED,
            stage="completed",
            progress=100,
            message=scan.summary,
        )

        logger.info(
            "Job %s finished: scan=%s segments=%d frames=%d",
            job.publicId,
            scan.id,
            len(segments),
            len(frame_rows),
        )
        return {
            "ok": True,
            "scan_id": scan.id,
            "segments": len(segments),
            "frames": len(frame_rows),
        }

    except Exception as exc:
        logger.error("Job %s failed: %s", job_id, exc, exc_info=True)
        try:
            job = await prisma.analysisjob.update(
                where={"id": job_id},
                data={
                    "status": "FAILED",
                    "stage": "failed",
                    "errorMessage": str(exc)[:1000],
                    "finishedAt": datetime.now(timezone.utc),
                },
            )
            await cache.invalidate_user(user_id)
            await events.emit(
                prisma,
                job,
                events.JOB_FAILED,
                stage="failed",
                message=str(exc)[:500],
            )
        except Exception:  # pragma: no cover
            logger.error("Could not record the failure of job %s", job_id)
        raise

    finally:
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except OSError:
                pass


async def _build_clip(prisma: Prisma, scan: Any, segment: Any, video_path: str) -> Optional[str]:
    """Cut the incident out of the video, box it, and store both in MinIO."""
    try:
        local_clip = await asyncio.to_thread(
            extract_clip, video_path, segment.startTime, segment.endTime
        )
        if not local_clip:
            return None

        # extract_clip returns the public "/media/clips/<name>" path.
        clip_path = Path("media") / "clips" / Path(local_clip).name
        if not clip_path.exists():
            return None

        clip_key = media_store.clip_key(scan.id, segment.ordinal)
        await media_store.put_file(clip_key, str(clip_path), "video/mp4")

        annotated_key: Optional[str] = None
        try:
            local_annotated = await asyncio.to_thread(annotate_clip, str(clip_path))
            if local_annotated:
                annotated_path = Path("media") / "clips" / Path(local_annotated).name
                if annotated_path.exists():
                    annotated_key = media_store.clip_key(
                        scan.id, segment.ordinal, annotated=True
                    )
                    await media_store.put_file(
                        annotated_key, str(annotated_path), "video/mp4"
                    )
        except Exception as exc:
            logger.info("Box annotation skipped for segment %s: %s", segment.id, exc)

        await scan_writer.attach_clip(prisma, segment, clip_key, annotated_key)
        return clip_key
    except Exception as exc:
        logger.warning("Clip extraction failed for segment %s: %s", segment.id, exc)
        return None


async def _illustrate_segment(
    prisma: Prisma, video: Any, segment: Any, video_path: str
) -> None:
    """Store stills spanning an incident, the peak moment first.

    One cover image says an incident happened; a strip across the window says
    *when inside it* — which is what the chat agent needs to answer "which frame
    shows the punch?" with a picture instead of a guess. The peak is stored
    first so anything showing a single cover still picks the moment the model
    pointed at rather than whatever happened to be halfway through the span.
    """
    for second in incident_frame_seconds(
        segment.startTime,
        segment.endTime,
        settings.incident_frame_count,
        segment.peakSecond,
    ):
        try:
            await store_incident_frame(prisma, video, segment, video_path, second)
        except Exception as exc:
            logger.info(
                "Could not illustrate segment %s at %.2fs: %s", segment.id, second, exc
            )


# --------------------------------------------------------------------------- #
# ARQ wiring
# --------------------------------------------------------------------------- #


async def startup(ctx: dict) -> None:
    """Open the worker's own database connection and warm the lookup caches."""
    prisma = Prisma()
    await prisma.connect()
    await seed_reference_data(prisma)
    ctx["prisma"] = prisma
    logger.info("Worker ready — listening on %s", QUEUE_NAME)


async def shutdown(ctx: dict) -> None:
    """Close the database connection cleanly."""
    prisma: Optional[Prisma] = ctx.get("prisma")
    if prisma is not None:
        await prisma.disconnect()


class WorkerSettings:
    """`python -m arq worker.WorkerSettings`."""

    functions = [analyze_video]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings: RedisSettings = redis_settings()
    queue_name = QUEUE_NAME

    # Vision models are memory-hungry; two concurrent jobs is the ceiling on a
    # single GPU. Raise only if the models are moved to a dedicated host.
    max_jobs = 2
    job_timeout = settings.job_timeout_seconds
    max_tries = settings.job_max_tries
    keep_result = 3600
