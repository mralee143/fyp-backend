"""
Compatibility layer over the normalized schema.

This module used to own a single `detection_scans` table holding a `labels`
array and a `result` JSON blob. That table has been split into `videos`,
`analysis_jobs`, `scans`, `segments`, `detections` and `scan_labels` (see
docs/NORMALIZATION.md), so every function here now delegates to
`services.scan_repository` (reads) or `services.scan_writer` (writes).

The signatures are unchanged on purpose: existing routes keep working and
automatically see normalized data, so the migration needed no call-site churn.
New code should import `scan_repository` / `scan_writer` directly.
"""

import logging
import uuid
from typing import Any, Optional

from services import cache, scan_repository, scan_writer
from services.reference_data import get_model_id

logger = logging.getLogger(__name__)

# Re-exported so existing imports keep resolving.
get_stats = scan_repository.get_stats
get_history = scan_repository.get_history
get_scan = scan_repository.get_scan
get_scan_any = scan_repository.get_scan_any
admin_stats = scan_repository.admin_stats
admin_users = scan_repository.admin_users
admin_scans = scan_repository.admin_scans


async def save_scan(
    prisma: Any, user_id: int, filename: str, model: str, result: dict
) -> Optional[int]:
    """Persist a synchronously-produced result into the normalized tables.

    The queue-based pipeline in `worker.py` writes these rows itself; this path
    exists for the original blocking `/detection/video*` endpoints, which still
    analyse inline and have no `videos` row of their own. A placeholder video is
    created so the FK chain (scan -> job -> video -> user) stays intact.

    Never raises — a persistence failure must not fail a detection that already
    succeeded. Returns the new scan id, or None.
    """
    try:
        placeholder_key = f"inline/{user_id}/{uuid.uuid4().hex}-{filename}"
        video = await prisma.video.create(
            data={
                "userId": user_id,
                "originalFilename": filename,
                "objectKey": placeholder_key,
                "mimeType": "video/mp4",
                "fileSize": 0,
                "checksumSha256": uuid.uuid4().hex,
            }
        )

        job = await prisma.analysisjob.create(
            data={
                "publicId": str(uuid.uuid4()),
                "videoId": video.id,
                "modelId": await get_model_id(prisma, model),
                "status": "SUCCEEDED",
                "stage": "completed",
                "progress": 100,
            }
        )

        scan, _ = await scan_writer.persist_result(prisma, job, result, model)
        await cache.invalidate_user(user_id)
        return scan.id
    except Exception as exc:  # pragma: no cover - best-effort persistence
        logger.error("Failed to save scan for user %s: %s", user_id, exc)
        return None


async def admin_analytics(prisma: Any) -> dict:
    """Aggregates for the admin dashboard charts.

    Returns scans-per-day (last 14 days, gap-filled), threat/clear totals,
    crime-category breakdown, model usage and the busiest users — all computed
    from the normalized tables. The category breakdown now comes from a real
    join through `segments -> incident_categories` instead of `unnest(labels)`,
    which is exactly the query the array column made awkward.
    """
    scans_by_day = await prisma.query_raw(
        "SELECT to_char(d::date, 'YYYY-MM-DD') AS day, "
        "COUNT(s.id)::int AS scans, "
        "COUNT(s.id) FILTER (WHERE s.violence_detected)::int AS threats "
        "FROM generate_series(CURRENT_DATE - INTERVAL '13 days', CURRENT_DATE, "
        "INTERVAL '1 day') d "
        "LEFT JOIN scans s ON s.created_at::date = d::date "
        "GROUP BY d ORDER BY d"
    )

    totals = await prisma.query_raw(
        "SELECT COUNT(*) FILTER (WHERE violence_detected)::int AS threats, "
        "COUNT(*) FILTER (WHERE NOT violence_detected)::int AS clear "
        "FROM scans"
    )

    categories = await prisma.query_raw(
        "SELECT c.code AS category, COUNT(DISTINCT sg.scan_id)::int AS count "
        "FROM segments sg "
        "JOIN incident_categories c ON c.id = sg.category_id "
        "GROUP BY c.code ORDER BY count DESC"
    )

    models = await prisma.query_raw(
        "SELECT m.code AS model, COUNT(s.id)::int AS count "
        "FROM scans s JOIN detection_models m ON m.id = s.model_id "
        "GROUP BY m.code ORDER BY count DESC"
    )

    top_users = await prisma.query_raw(
        "SELECT u.email, COUNT(s.id)::int AS count "
        "FROM users u "
        "JOIN videos v ON v.user_id = u.id "
        "JOIN analysis_jobs j ON j.video_id = v.id "
        "JOIN scans s ON s.job_id = j.id "
        "GROUP BY u.email ORDER BY count DESC LIMIT 6"
    )

    return {
        "scans_by_day": scans_by_day,
        "totals": totals[0] if totals else {"threats": 0, "clear": 0},
        "categories": categories,
        "models": models,
        "top_users": top_users,
    }
