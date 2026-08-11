"""
Reference (lookup) data for the normalized schema.

`detection_models`, `incident_categories` and `webhook_event_types` are closed
sets that the rest of the code refers to by id. Seeding is idempotent, so it can
run on every application start.

`get_label_id` is the write path for the open-ended `labels` table: labels
arrive from the models as free text ("Fighting", "a gun") and are interned to a
single row each, which is what lets `scan_labels` be a proper M:N join instead
of the old `labels String[]` column.
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


# code, display name, kind, runs locally, description
DETECTION_MODELS: tuple[tuple[str, str, str, bool, str], ...] = (
    ("yolo", "YOLOv8 Weapons", "object", True, "Fine-tuned weapon detector (gun/knife/grenade)."),
    ("owlv2", "OWLv2 Zero-Shot", "object", True, "Open-vocabulary object detection from free text."),
    ("llm", "Gemini Vision", "vlm", False, "Google Gemini video understanding."),
    ("qwen", "Qwen2.5-VL", "vlm", True, "Local vision-language model, offline."),
    ("action", "VideoMAE UCF-Crime", "action", True, "Action recognition over 13 crime classes."),
    ("violence", "Violence Classifier", "action", True, "Dedicated fight/assault classifier."),
    ("auto", "Auto Cascade", "cascade", True, "Runs every model in order and keeps the first hit."),
)

# code, display name, severity (1 low - 5 critical)
INCIDENT_CATEGORIES: tuple[tuple[str, str, int], ...] = (
    ("violence", "Violence", 5),
    ("theft", "Theft / Robbery", 4),
    ("harassment", "Harassment", 4),
    ("accident", "Accident", 3),
    ("other", "Other Hazard", 2),
)

# Every event the pipeline can emit. Webhook endpoints subscribe to a subset.
WEBHOOK_EVENT_TYPES: tuple[str, ...] = (
    "job.queued",
    "job.started",
    "job.progress",
    "frame.ready",
    "segment.ready",
    "job.completed",
    "job.failed",
)

# code -> id, filled on first use. Reference rows never change at runtime, so an
# in-process dict removes a join from every write in the worker's hot loop.
_model_ids: dict[str, int] = {}
_category_ids: dict[str, int] = {}
_label_ids: dict[str, int] = {}
_label_lock = asyncio.Lock()


async def seed_reference_data(prisma: Any) -> None:
    """Create every lookup row that does not exist yet. Safe to run repeatedly."""
    for code, display, kind, is_local, description in DETECTION_MODELS:
        await prisma.detectionmodel.upsert(
            where={"code": code},
            data={
                "create": {
                    "code": code,
                    "displayName": display,
                    "kind": kind,
                    "isLocal": is_local,
                    "description": description,
                },
                "update": {"displayName": display, "kind": kind, "description": description},
            },
        )

    for code, display, severity in INCIDENT_CATEGORIES:
        await prisma.incidentcategory.upsert(
            where={"code": code},
            data={
                "create": {"code": code, "displayName": display, "severity": severity},
                "update": {"displayName": display, "severity": severity},
            },
        )

    for name in WEBHOOK_EVENT_TYPES:
        await prisma.webhookeventtype.upsert(
            where={"name": name},
            data={"create": {"name": name}, "update": {}},
        )

    _model_ids.clear()
    _category_ids.clear()
    logger.info("Reference data seeded")


async def get_model_id(prisma: Any, code: str) -> int:
    """Resolve a detection model code to its id, creating a row if it is new."""
    code = (code or "auto").lower().strip()
    if code in _model_ids:
        return _model_ids[code]

    row = await prisma.detectionmodel.find_unique(where={"code": code})
    if row is None:
        row = await prisma.detectionmodel.create(
            data={"code": code, "displayName": code.title(), "kind": "object"}
        )
    _model_ids[code] = row.id
    return row.id


async def get_category_id(prisma: Any, code: str) -> int:
    """Resolve an incident category code to its id, defaulting to 'other'."""
    code = (code or "other").lower().strip()
    if code in _category_ids:
        return _category_ids[code]

    row = await prisma.incidentcategory.find_unique(where={"code": code})
    if row is None:
        row = await prisma.incidentcategory.find_unique(where={"code": "other"})
        if row is None:  # pragma: no cover - only if seeding never ran
            row = await prisma.incidentcategory.create(
                data={"code": "other", "displayName": "Other Hazard", "severity": 2}
            )
    _category_ids[code] = row.id
    return row.id


async def get_label_id(prisma: Any, name: str) -> int:
    """Intern a free-text label, returning the id of its single `labels` row."""
    clean = (name or "unknown").strip()[:120] or "unknown"
    cached = _label_ids.get(clean)
    if cached is not None:
        return cached

    # Serialised so two frames finishing together cannot race the same insert.
    async with _label_lock:
        cached = _label_ids.get(clean)
        if cached is not None:
            return cached
        row = await prisma.label.find_unique(where={"name": clean})
        if row is None:
            row = await prisma.label.create(data={"name": clean})
        _label_ids[clean] = row.id
        return row.id
