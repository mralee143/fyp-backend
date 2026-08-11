"""
Read model for scans.

Everything here reads the normalized tables and reassembles the response shape
the frontend already understands (`result.segments`, `result.detections`), so
the 3NF migration is invisible to existing pages while new fields — extracted
frames, per-image captions, job status — are added alongside.

Ownership is always expressed as a traversal rather than a copied column:

    scan -> job -> video -> user

Read-heavy endpoints go through `services.cache`, keyed by user and invalidated
whenever that user's data changes.
"""

import logging
from typing import Any, Optional

from services import cache, media_store

logger = logging.getLogger(__name__)

# Filter fragment selecting scans owned by one user, via the FK chain.
def _owned_by(user_id: int) -> dict:
    return {"job": {"is": {"video": {"is": {"userId": user_id}}}}}


# Relations needed to rebuild a full report in one query.
_SCAN_DETAIL_INCLUDE = {
    "job": {"include": {"video": True, "model": True}},
    "model": True,
    "segments": {"include": {"label": True, "category": True}},
    "detections": {"include": {"label": True}},
    "scanLabels": {"include": {"label": True}},
}


async def _segment_dict(segment: Any) -> dict:
    """One segment in the shape the report timeline expects."""
    return {
        "id": segment.id,
        "ordinal": segment.ordinal,
        "label": segment.label.name if segment.label else "Incident",
        "category": segment.category.code if segment.category else "other",
        "description": segment.description,
        "explanation": segment.explanation,
        "start_time": segment.startTime,
        "end_time": segment.endTime,
        "confidence": segment.confidence,
        "clip_url": await media_store.resolve_media_url(segment.clipObjectKey),
        "annotated_clip_url": await media_store.resolve_media_url(
            segment.annotatedClipObjectKey
        ),
    }


def _detection_dict(detection: Any) -> dict:
    """One object box, back in the original `box_xyxy` form."""
    second = detection.second
    minutes, seconds = divmod(int(second), 60)
    return {
        "second": second,
        "timestamp": f"{minutes:02d}:{seconds:02d}",
        "label": detection.label.name if detection.label else "object",
        "score": detection.score,
        "box_xyxy": [detection.boxX1, detection.boxY1, detection.boxX2, detection.boxY2],
    }


async def _video_url(video: Any) -> Optional[str]:
    """A playable URL, or None when the file was never stored.

    The original blocking `/detection/video*` routes analyse the upload in
    memory and record a placeholder key (see `scan_store.save_scan`), so
    presigning one yields a URL that 404s on click. Returning None instead lets
    the report say the video is unavailable rather than showing a dead player.
    """
    if not video.objectKey or video.objectKey.startswith("inline/"):
        return None
    return await media_store.resolve_media_url(video.objectKey)


async def _image_dict(image: Any) -> dict:
    """One stored frame, with a ready-to-render URL and its caption."""
    return {
        "id": image.id,
        "kind": image.kind,
        "sequence": image.sequence,
        "captured_at_seconds": image.capturedAtSeconds,
        "caption": image.caption,
        "width": image.width,
        "height": image.height,
        "segment_id": image.segmentId,
        "url": await media_store.resolve_media_url(image.objectKey),
    }


async def get_stats(prisma: Any, user_id: int) -> dict:
    """{total, threats, clear} for the dashboard cards."""

    async def load() -> dict:
        total = await prisma.scan.count(where=_owned_by(user_id))
        threats = await prisma.scan.count(
            where={**_owned_by(user_id), "violenceDetected": True}
        )
        return {"total": total, "threats": threats, "clear": total - threats}

    return await cache.cached_json(user_id, "stats", "all", load, ttl=60)


async def get_history(prisma: Any, user_id: int, limit: int = 20) -> list[dict]:
    """The user's most recent scans (metadata only)."""

    async def load() -> list[dict]:
        scans = await prisma.scan.find_many(
            where=_owned_by(user_id),
            include={"job": {"include": {"video": True, "model": True}}, "model": True},
            order={"id": "desc"},
            take=limit,
        )
        return [
            {
                "id": scan.id,
                "filename": scan.job.video.originalFilename,
                "model": scan.model.code if scan.model else scan.job.model.code,
                "violence_detected": scan.violenceDetected,
                "summary": scan.summary,
                "created_at": scan.createdAt.isoformat() if scan.createdAt else None,
                "job_id": scan.job.publicId,
                "video_id": scan.job.videoId,
            }
            for scan in scans
        ]

    return await cache.cached_json(user_id, "history", str(limit), load, ttl=60)


async def _build_detail(prisma: Any, scan: Any) -> dict:
    """Assemble a full report from the normalized rows."""
    video = scan.job.video

    images = await prisma.image.find_many(
        where={"videoId": video.id},
        order=[{"kind": "asc"}, {"sequence": "asc"}],
    )

    segments = [await _segment_dict(s) for s in sorted(scan.segments or [], key=lambda s: s.ordinal)]
    detections = [_detection_dict(d) for d in (scan.detections or [])]

    # Derived on read, never stored — the 3NF replacement for `label_counts`.
    label_counts: dict[str, int] = {}
    for detection in scan.detections or []:
        name = detection.label.name if detection.label else "object"
        label_counts[name] = label_counts.get(name, 0) + 1

    return {
        "id": scan.id,
        "filename": video.originalFilename,
        "model": scan.model.code if scan.model else scan.job.model.code,
        "model_name": scan.model.displayName if scan.model else None,
        "requested_model": scan.job.model.code,
        "violence_detected": scan.violenceDetected,
        "summary": scan.summary,
        "created_at": scan.createdAt.isoformat() if scan.createdAt else None,
        "job_id": scan.job.publicId,
        "job_status": scan.job.status,
        "video": {
            "id": video.id,
            "filename": video.originalFilename,
            "duration_seconds": video.durationSeconds,
            "width": video.width,
            "height": video.height,
            "url": await _video_url(video),
        },
        "labels": sorted(
            link.label.name for link in (scan.scanLabels or []) if link.label
        ),
        "images": [await _image_dict(image) for image in images],
        # Kept for the existing report page, now rebuilt from the child tables.
        "result": {
            "model_id": scan.model.code if scan.model else None,
            "summary": scan.summary,
            "violence_detected": scan.violenceDetected,
            "frames_scanned": scan.framesScanned,
            "score_threshold": scan.scoreThreshold,
            "label_counts": label_counts,
            "detection_count": len(detections),
            "segments": segments,
            "detections": detections,
        },
    }


async def get_scan(prisma: Any, user_id: int, scan_id: int) -> Optional[dict]:
    """One scan owned by `user_id`, or None."""

    async def load() -> Optional[dict]:
        scan = await prisma.scan.find_first(
            where={"id": scan_id, **_owned_by(user_id)}, include=_SCAN_DETAIL_INCLUDE
        )
        return await _build_detail(prisma, scan) if scan else None

    # Presigned URLs live inside the payload, so the cache must expire before
    # they do.
    return await cache.cached_json(user_id, "scan", str(scan_id), load, ttl=120)


async def get_scan_any(prisma: Any, scan_id: int) -> Optional[dict]:
    """One scan regardless of owner (admin), with the owner's email attached."""
    scan = await prisma.scan.find_unique(
        where={"id": scan_id}, include=_SCAN_DETAIL_INCLUDE
    )
    if scan is None:
        return None

    detail = await _build_detail(prisma, scan)
    owner = await prisma.user.find_unique(where={"id": scan.job.video.userId})
    detail["email"] = owner.email if owner else None
    return detail


async def get_video_images(
    prisma: Any, user_id: int, video_id: int, kinds: Optional[list[str]] = None
) -> Optional[list[dict]]:
    """Every stored frame of a video, newest analysis first. None if not owned."""
    video = await prisma.video.find_first(where={"id": video_id, "userId": user_id})
    if video is None:
        return None

    where: dict = {"videoId": video_id}
    if kinds:
        where["kind"] = {"in": kinds}

    images = await prisma.image.find_many(
        where=where, order=[{"kind": "asc"}, {"sequence": "asc"}]
    )
    return [await _image_dict(image) for image in images]


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #


async def admin_stats(prisma: Any) -> dict:
    """Site-wide counters for the admin dashboard."""
    return {
        "users": await prisma.user.count(),
        "scans": await prisma.scan.count(),
        "threats": await prisma.scan.count(where={"violenceDetected": True}),
        "videos": await prisma.video.count(),
        "jobs_running": await prisma.analysisjob.count(where={"status": "RUNNING"}),
        "jobs_queued": await prisma.analysisjob.count(where={"status": "QUEUED"}),
        "jobs_failed": await prisma.analysisjob.count(where={"status": "FAILED"}),
        "images": await prisma.image.count(),
    }


async def admin_users(prisma: Any) -> list[dict]:
    """Every user with their video and scan counts."""
    users = await prisma.user.find_many(order={"id": "desc"})
    rows = []
    for user in users:
        video_count = await prisma.video.count(where={"userId": user.id})
        scan_count = await prisma.scan.count(where=_owned_by(user.id))
        rows.append(
            {
                "id": user.id,
                "email": user.email,
                "is_active": user.isActive,
                "created_at": user.createdAt.isoformat() if user.createdAt else None,
                "scan_count": scan_count,
                "video_count": video_count,
            }
        )
    return rows


async def admin_scans(prisma: Any, limit: int = 100) -> list[dict]:
    """Recent scans across every user."""
    scans = await prisma.scan.find_many(
        include={"job": {"include": {"video": {"include": {"user": True}}, "model": True}}, "model": True},
        order={"id": "desc"},
        take=limit,
    )
    return [
        {
            "id": scan.id,
            "filename": scan.job.video.originalFilename,
            "model": scan.model.code if scan.model else scan.job.model.code,
            "violence_detected": scan.violenceDetected,
            "summary": scan.summary,
            "created_at": scan.createdAt.isoformat() if scan.createdAt else None,
            "email": scan.job.video.user.email if scan.job.video.user else None,
        }
        for scan in scans
    ]
