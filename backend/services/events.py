"""
Job event bus.

Every meaningful thing that happens to an analysis job becomes one append-only
`job_events` row, and that row is the *single* source of truth for both delivery
channels:

    worker ──► job_events (Postgres)  ──┬──► Redis pub/sub ──► SSE ──► browser
                                        └──► webhook dispatch ──► your server

A browser cannot receive an inbound HTTP callback, so it subscribes to the same
stream over SSE instead of registering a URL. Both channels carry byte-identical
payloads built by `build_payload`, so a webhook consumer and the UI always see
the same event.

Because events are numbered per job (`sequence`), an SSE client that drops can
reconnect with `Last-Event-ID` and replay exactly what it missed — no gaps, no
duplicates.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

from services import media_store
from services.redis_client import get_redis

logger = logging.getLogger(__name__)

# Event names, mirrored in services/reference_data.WEBHOOK_EVENT_TYPES.
JOB_QUEUED = "job.queued"
JOB_STARTED = "job.started"
JOB_PROGRESS = "job.progress"
FRAME_READY = "frame.ready"
SEGMENT_READY = "segment.ready"
JOB_COMPLETED = "job.completed"
JOB_FAILED = "job.failed"

# Terminal events — the SSE stream closes after one of these.
TERMINAL_EVENTS = {JOB_COMPLETED, JOB_FAILED}


def channel_for(job_public_id: str) -> str:
    """Redis pub/sub channel carrying one job's events."""
    return f"job-events:{job_public_id}"


async def build_payload(prisma: Any, event: Any, job: Any) -> dict:
    """Hydrate a `job_events` row into the JSON delivered to clients.

    Frame and segment events are expanded with the referenced row and a ready-to
    use media URL, so the client can render the image the moment the event
    arrives without a follow-up request.
    """
    payload: dict[str, Any] = {
        "id": event.id,
        "sequence": event.sequence,
        "event": event.eventType,
        "job_id": job.publicId,
        "status": job.status,
        "stage": event.stage or job.stage,
        "progress": event.progress if event.progress is not None else job.progress,
        "message": event.message,
        "created_at": event.createdAt.isoformat() if event.createdAt else None,
    }

    if event.imageId:
        image = await prisma.image.find_unique(where={"id": event.imageId})
        if image is not None:
            payload["image"] = {
                "id": image.id,
                "kind": image.kind,
                "sequence": image.sequence,
                "captured_at_seconds": image.capturedAtSeconds,
                "caption": image.caption,
                "width": image.width,
                "height": image.height,
                "url": await media_store.resolve_media_url(image.objectKey),
            }

    if event.segmentId:
        segment = await prisma.segment.find_unique(
            where={"id": event.segmentId},
            include={"label": True, "category": True},
        )
        if segment is not None:
            payload["segment"] = {
                "id": segment.id,
                "ordinal": segment.ordinal,
                "label": segment.label.name if segment.label else "Incident",
                "category": segment.category.code if segment.category else "other",
                "description": segment.description,
                "explanation": segment.explanation,
                "start_time": segment.startTime,
                "end_time": segment.endTime,
                "peak_second": segment.peakSecond,
                "confidence": segment.confidence,
                "clip_url": await media_store.resolve_media_url(segment.clipObjectKey),
                "annotated_clip_url": await media_store.resolve_media_url(
                    segment.annotatedClipObjectKey
                ),
            }

    return payload


async def _next_sequence(prisma: Any, job_id: int) -> int:
    """Next per-job event number. One worker owns a job, so a count is enough."""
    return await prisma.jobevent.count(where={"jobId": job_id}) + 1


async def emit(
    prisma: Any,
    job: Any,
    event_type: str,
    *,
    stage: Optional[str] = None,
    progress: Optional[int] = None,
    message: Optional[str] = None,
    image_id: Optional[int] = None,
    segment_id: Optional[int] = None,
) -> Optional[dict]:
    """Record an event, publish it to subscribers and queue webhook deliveries.

    Never raises: a failure to announce progress must not fail the analysis that
    produced it. Returns the delivered payload, or None if recording failed.
    """
    try:
        sequence = await _next_sequence(prisma, job.id)
        try:
            event = await prisma.jobevent.create(
                data={
                    "jobId": job.id,
                    "sequence": sequence,
                    "eventType": event_type,
                    "stage": stage,
                    "progress": progress,
                    "message": message,
                    "imageId": image_id,
                    "segmentId": segment_id,
                }
            )
        except Exception:
            # Lost a race on [jobId, sequence] — recount once and retry.
            event = await prisma.jobevent.create(
                data={
                    "jobId": job.id,
                    "sequence": await _next_sequence(prisma, job.id),
                    "eventType": event_type,
                    "stage": stage,
                    "progress": progress,
                    "message": message,
                    "imageId": image_id,
                    "segmentId": segment_id,
                }
            )

        payload = await build_payload(prisma, event, job)

        await _publish(job.publicId, payload)

        # Webhook fan-out is fire-and-forget; the dispatcher owns its retries.
        from services.webhooks import dispatch_event

        asyncio.create_task(dispatch_event(prisma, job, event, payload))

        return payload
    except Exception as exc:  # pragma: no cover - best-effort telemetry
        logger.error("Failed to emit %s for job %s: %s", event_type, job.id, exc)
        return None


async def _publish(job_public_id: str, payload: dict) -> None:
    """Push one payload onto the job's Redis channel for SSE subscribers."""
    redis = await get_redis()
    if redis is None:
        return
    try:
        await redis.publish(channel_for(job_public_id), json.dumps(payload, default=str))
    except Exception as exc:
        logger.debug("Publish failed for job %s: %s", job_public_id, exc)


async def replay(prisma: Any, job: Any, after_sequence: int = 0) -> list[dict]:
    """Every event for a job after `after_sequence`, oldest first.

    Used to prime a fresh SSE connection and to honour `Last-Event-ID` on
    reconnect, so a client that joins late still sees frames it missed.
    """
    events = await prisma.jobevent.find_many(
        where={"jobId": job.id, "sequence": {"gt": after_sequence}},
        order={"sequence": "asc"},
    )
    return [await build_payload(prisma, event, job) for event in events]


async def open_subscription(job_public_id: str) -> Optional[Any]:
    """Start listening on a job's channel, returning the pub/sub handle.

    Subscribing is separate from iterating so a caller can subscribe *before*
    replaying history. Doing it the other way round leaves a window in which an
    event published between the replay query and the subscribe is lost — the
    classic missed-notification race.

    Returns None when Redis is unavailable; the caller then falls back to
    polling.
    """
    redis = await get_redis()
    if redis is None:
        return None

    pubsub = redis.pubsub()
    await pubsub.subscribe(channel_for(job_public_id))
    return pubsub


async def iter_subscription(pubsub: Any, job_public_id: str) -> AsyncIterator[dict]:
    """Yield live events from an already-open subscription."""
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=15.0
            )
            if message is None:
                # Timed out with nothing published — hand control back so the
                # caller can send a keep-alive comment.
                yield {}
                continue
            try:
                yield json.loads(message["data"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    finally:
        try:
            await pubsub.unsubscribe(channel_for(job_public_id))
            await pubsub.aclose()
        except Exception:  # pragma: no cover - connection already gone
            pass
