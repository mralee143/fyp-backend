"""
Persistence for detection scans (the `detection_scans` table).

Uses raw SQL via the Prisma client because the generated Python client can't be
regenerated in this environment (engine/version mismatch). The table is created
by `prisma db push`.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _derive(result: dict) -> tuple[bool, str, list[str]]:
    """Pull (violence_detected, summary, labels) from either result shape."""
    violence = bool(result.get("violence_detected") or result.get("weapon_detected"))

    summary = result.get("summary") or ""
    labels: list[str] = []

    segments = result.get("segments")
    if segments:
        # LLM / Qwen / action result -> categories present
        labels = sorted({str(s.get("category", "violence")) for s in segments})
        if not summary:
            summary = f"{len(segments)} flagged section(s)."
    elif result.get("label_counts"):
        # Object detector (yolo/owlv2) -> detected object labels
        labels = list(result["label_counts"].keys())
        if violence and "violence" not in labels:
            labels.append("violence")
        if not summary:
            summary = f"{result.get('detection_count', 0)} object detection(s)."

    return violence, summary, labels


async def save_scan(
    prisma: Any, user_id: int, filename: str, model: str, result: dict
) -> None:
    """Insert one scan. Never raises — a save failure must not break detection."""
    try:
        violence, summary, labels = _derive(result)
        await prisma.execute_raw(
            "INSERT INTO detection_scans "
            "(user_id, filename, model, violence_detected, summary, labels, result) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
            user_id,
            filename,
            model,
            violence,
            summary,
            labels,
            json.dumps(result),
        )
    except Exception as e:  # pragma: no cover - best-effort persistence
        logger.error("Failed to save scan for user %s: %s", user_id, e)


async def get_stats(prisma: Any, user_id: int) -> dict:
    """Return {total, threats, clear} counts for a user's scans."""
    rows = await prisma.query_raw(
        "SELECT COUNT(*)::int AS total, "
        "COUNT(*) FILTER (WHERE violence_detected)::int AS threats, "
        "COUNT(*) FILTER (WHERE NOT violence_detected)::int AS clear "
        "FROM detection_scans WHERE user_id = $1",
        user_id,
    )
    return rows[0] if rows else {"total": 0, "threats": 0, "clear": 0}


async def get_history(prisma: Any, user_id: int, limit: int = 20) -> list[dict]:
    """Return the user's most recent scans (metadata only)."""
    return await prisma.query_raw(
        "SELECT id, filename, model, violence_detected, summary, created_at "
        "FROM detection_scans WHERE user_id = $1 ORDER BY id DESC LIMIT $2",
        user_id,
        limit,
    )


async def get_scan(prisma: Any, user_id: int, scan_id: int) -> dict | None:
    """Return one scan (incl. the full result JSON) for a report view."""
    rows = await prisma.query_raw(
        "SELECT id, filename, model, violence_detected, summary, result, created_at "
        "FROM detection_scans WHERE id = $1 AND user_id = $2",
        scan_id,
        user_id,
    )
    if not rows:
        return None
    row = rows[0]
    # jsonb may come back as a string — parse it so the API returns an object.
    if isinstance(row.get("result"), str):
        try:
            row["result"] = json.loads(row["result"])
        except json.JSONDecodeError:
            row["result"] = {}
    return row
