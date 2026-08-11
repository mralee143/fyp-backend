"""
One-shot migration from the old single-table design to the 3NF schema.

The old `detection_scans` table held everything in one row: a `labels String[]`
array (breaks 1NF) and a `result Json` blob containing the segments, the object
detections and the derived label counts (breaks 1NF and stores derived data).
This script fans those rows out into `videos`, `analysis_jobs`, `scans`,
`segments`, `detections` and `scan_labels`, then optionally drops the legacy
table.

Run it once, after `prisma db push`:

    python migrate_normalize.py                # backfill, keep legacy table
    python migrate_normalize.py --drop-legacy  # backfill, then drop it
    python migrate_normalize.py --dry-run      # report only, write nothing

Re-running is safe: a legacy scan is skipped if its video already exists.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from prisma import Prisma

from services.reference_data import (
    DETECTION_MODELS,
    get_category_id,
    get_label_id,
    get_model_id,
    seed_reference_data,
)

KNOWN_MODEL_CODES = {code for code, *_ in DETECTION_MODELS}

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("migrate")


# Ownership must be expressed exactly once on `images`: a direct upload has a
# user and no video, a extracted frame has a video (whose owner is the user) and
# no user. Prisma cannot express a CHECK, so it is applied here.
_IMAGE_OWNER_CHECK = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'images_owner_exactly_one'
    ) THEN
        ALTER TABLE images
            ADD CONSTRAINT images_owner_exactly_one
            CHECK ((user_id IS NULL) <> (video_id IS NULL));
    END IF;
END $$;
"""


async def apply_constraints(db: Prisma) -> None:
    """Add the CHECK constraints Prisma's schema language cannot express."""
    await db.execute_raw(_IMAGE_OWNER_CHECK)
    logger.info("Applied images_owner_exactly_one CHECK constraint")


def _parse_result(raw: Any) -> dict:
    """The legacy `result` column comes back as dict or str depending on driver."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


async def _backfill_one(db: Prisma, legacy: Any) -> bool:
    """Move one legacy scan into the normalized tables. Returns True if written."""
    result = _parse_result(legacy.result)

    # The original video file is long gone — synthesise a stable placeholder so
    # the FK chain (scan -> job -> video -> user) is intact and the report page
    # can still render. `legacy/` keys are recognised as "no object stored".
    checksum = hashlib.sha256(f"legacy-scan-{legacy.id}".encode()).hexdigest()
    object_key = f"legacy/{legacy.id}/{legacy.filename}"

    existing = await db.video.find_unique(where={"objectKey": object_key})
    if existing is not None:
        return False

    created_at: datetime = legacy.createdAt or datetime.now(timezone.utc)

    video = await db.video.create(
        data={
            "userId": legacy.userId,
            "originalFilename": legacy.filename,
            "objectKey": object_key,
            "mimeType": "video/mp4",
            "fileSize": 0,
            "checksumSha256": checksum,
            "uploadedAt": created_at,
        }
    )

    model_id = await get_model_id(db, legacy.model)
    job = await db.analysisjob.create(
        data={
            "publicId": str(uuid.uuid4()),
            "videoId": video.id,
            "modelId": model_id,
            "status": "SUCCEEDED",
            "stage": "completed",
            "progress": 100,
            "queuedAt": created_at,
            "startedAt": created_at,
            "finishedAt": created_at,
        }
    )

    # `auto` runs a cascade; the winning model is recorded inside the payload.
    produced_by = str(result.get("model_id") or legacy.model or "auto")
    produced_code = produced_by.split("/")[-1].split(":")[0].lower()
    if produced_code not in KNOWN_MODEL_CODES:
        produced_code = legacy.model
    produced_model_id = await get_model_id(db, produced_code)

    scan = await db.scan.create(
        data={
            "jobId": job.id,
            "modelId": produced_model_id,
            "violenceDetected": bool(legacy.violenceDetected),
            "summary": legacy.summary or "",
            "framesScanned": result.get("frames_scanned"),
            "scoreThreshold": result.get("score_threshold"),
            "createdAt": created_at,
        }
    )

    # ----- segments (LLM / qwen / action / auto results) -----
    for ordinal, seg in enumerate(result.get("segments") or []):
        label_id = await get_label_id(db, str(seg.get("label") or "Incident"))
        category_id = await get_category_id(db, str(seg.get("category") or "other"))
        await db.segment.create(
            data={
                "scanId": scan.id,
                "ordinal": ordinal,
                "labelId": label_id,
                "categoryId": category_id,
                "description": str(seg.get("description") or ""),
                "explanation": seg.get("explanation"),
                "startTime": float(seg.get("start_time") or 0.0),
                "endTime": float(seg.get("end_time") or 0.0),
                "confidence": float(seg.get("confidence") or 0.0),
                # Legacy clips are files under media/clips, not MinIO objects.
                # The URL resolver keys off the leading slash.
                "clipObjectKey": seg.get("clip_url"),
                "annotatedClipObjectKey": seg.get("annotated_clip_url"),
            }
        )

    # ----- object detections (yolo / owlv2 results) -----
    detections = result.get("detections") or []
    if detections:
        # A single owlv2 scan can hold 350 boxes — insert them in one statement.
        rows = []
        for det in detections:
            box = det.get("box_xyxy") or [0, 0, 0, 0]
            if len(box) != 4:
                box = [0, 0, 0, 0]
            rows.append(
                {
                    "scanId": scan.id,
                    "labelId": await get_label_id(db, str(det.get("label") or "object")),
                    "second": float(det.get("second") or 0.0),
                    "score": float(det.get("score") or 0.0),
                    "boxX1": float(box[0]),
                    "boxY1": float(box[1]),
                    "boxX2": float(box[2]),
                    "boxY2": float(box[3]),
                }
            )
        await db.detection.create_many(data=rows)

    # ----- scan <-> label join (replaces the labels[] array) -----
    label_names = set(legacy.labels or [])
    for seg in result.get("segments") or []:
        if seg.get("label"):
            label_names.add(str(seg["label"]))
    for name in result.get("label_counts") or {}:
        label_names.add(str(name))

    for name in sorted(label_names):
        label_id = await get_label_id(db, name)
        await db.scanlabel.upsert(
            where={"scanId_labelId": {"scanId": scan.id, "labelId": label_id}},
            data={"create": {"scanId": scan.id, "labelId": label_id}, "update": {}},
        )

    return True


async def backfill(db: Prisma, dry_run: bool) -> tuple[int, int]:
    """Copy every legacy scan into the normalized tables."""
    legacy_rows = await db.detectionscan.find_many(order={"id": "asc"})
    logger.info("Found %d legacy scan(s)", len(legacy_rows))

    if dry_run:
        for row in legacy_rows:
            result = _parse_result(row.result)
            logger.info(
                "  scan %-4s model=%-7s segments=%-3d detections=%-4d labels=%s",
                row.id,
                row.model,
                len(result.get("segments") or []),
                len(result.get("detections") or []),
                list(row.labels or []),
            )
        return len(legacy_rows), 0

    migrated = 0
    for row in legacy_rows:
        try:
            if await _backfill_one(db, row):
                migrated += 1
        except Exception as exc:
            logger.error("  scan %s failed: %s", row.id, exc)

    return len(legacy_rows), migrated


async def drop_legacy(db: Prisma) -> None:
    """Drop the legacy table once its rows live in the normalized schema."""
    await db.execute_raw("DROP TABLE IF EXISTS detection_scans CASCADE")
    logger.info(
        "Dropped detection_scans. Remove the DetectionScan model from "
        "prisma/schema.prisma so `db push` does not recreate it."
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    parser.add_argument(
        "--drop-legacy", action="store_true", help="drop detection_scans after backfilling"
    )
    args = parser.parse_args()

    db = Prisma()
    await db.connect()
    try:
        await apply_constraints(db)
        await seed_reference_data(db)

        total, migrated = await backfill(db, args.dry_run)
        logger.info("Backfill: %d legacy row(s), %d migrated, %d skipped",
                    total, migrated, total - migrated)

        if args.drop_legacy and not args.dry_run:
            await drop_legacy(db)

        # Summary of the new tables so the result is visible at a glance.
        if not args.dry_run:
            for name, count in (
                ("videos", await db.video.count()),
                ("analysis_jobs", await db.analysisjob.count()),
                ("scans", await db.scan.count()),
                ("segments", await db.segment.count()),
                ("detections", await db.detection.count()),
                ("labels", await db.label.count()),
                ("scan_labels", await db.scanlabel.count()),
            ):
                logger.info("  %-16s %d", name, count)
    finally:
        await db.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
