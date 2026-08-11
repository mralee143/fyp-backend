"""
Persist a raw model result into the normalized tables.

This is where the old `result Json` blob gets taken apart. A backend hands back
one of two shapes:

    segment shape  {violence_detected, summary, segments:[...]}   (llm/qwen/action)
    object shape   {weapon_detected, detections:[...], label_counts:{...}}  (yolo/owlv2)

and both are written as `scans` + `segments` / `detections` + `scan_labels`
rows. `label_counts` is deliberately *not* stored — it is a GROUP BY over
`detections`, and keeping a copy of it would be the derived-data duplication
3NF exists to prevent.
"""

import logging
from typing import Any, Optional

from services.reference_data import get_category_id, get_label_id, get_model_id

logger = logging.getLogger(__name__)


def derive_summary(result: dict) -> str:
    """The whole-video summary, synthesised when a backend does not write one."""
    summary = (result.get("summary") or "").strip()
    if summary:
        return summary

    segments = result.get("segments") or []
    if segments:
        return f"{len(segments)} flagged section(s) found in this video."

    if result.get("detections"):
        counts = result.get("label_counts") or {}
        top = ", ".join(f"{name} ×{n}" for name, n in list(counts.items())[:4])
        return f"{result.get('detection_count', len(result['detections']))} object detection(s): {top}."

    return "No incidents detected in this video."


def is_positive(result: dict) -> bool:
    """True when the backend flagged something, across either result shape."""
    return bool(result.get("violence_detected") or result.get("weapon_detected"))


async def create_scan(prisma: Any, job: Any, result: dict, model_code: str) -> Any:
    """Insert the `scans` row for a finished job."""
    return await prisma.scan.create(
        data={
            "jobId": job.id,
            "modelId": await get_model_id(prisma, model_code),
            "violenceDetected": is_positive(result),
            "summary": derive_summary(result),
            "framesScanned": result.get("frames_scanned"),
            "scoreThreshold": result.get("score_threshold"),
        }
    )


async def write_segments(prisma: Any, scan: Any, result: dict) -> list[Any]:
    """Insert one `segments` row per flagged event, in timeline order."""
    raw_segments = result.get("segments") or []
    created: list[Any] = []

    for ordinal, raw in enumerate(raw_segments):
        segment = await prisma.segment.create(
            data={
                "scanId": scan.id,
                "ordinal": ordinal,
                "labelId": await get_label_id(prisma, str(raw.get("label") or "Incident")),
                "categoryId": await get_category_id(prisma, str(raw.get("category") or "other")),
                "description": str(raw.get("description") or ""),
                "explanation": raw.get("explanation"),
                "startTime": float(raw.get("start_time") or 0.0),
                "endTime": float(raw.get("end_time") or 0.0),
                "confidence": float(raw.get("confidence") or 0.0),
            }
        )
        created.append(segment)

    return created


async def write_detections(prisma: Any, scan: Any, result: dict) -> int:
    """Insert every object box from an object-detector result.

    Uses one multi-row INSERT — an owlv2 pass over a busy street can return
    several hundred boxes, and a round trip each would dominate the job.
    """
    raw_detections = result.get("detections") or []
    if not raw_detections:
        return 0

    rows = []
    for raw in raw_detections:
        box = raw.get("box_xyxy") or [0, 0, 0, 0]
        if len(box) != 4:
            box = [0, 0, 0, 0]
        rows.append(
            {
                "scanId": scan.id,
                "labelId": await get_label_id(prisma, str(raw.get("label") or "object")),
                "second": float(raw.get("second") or 0.0),
                "score": float(raw.get("score") or 0.0),
                "boxX1": float(box[0]),
                "boxY1": float(box[1]),
                "boxX2": float(box[2]),
                "boxY2": float(box[3]),
            }
        )

    await prisma.detection.create_many(data=rows)
    return len(rows)


async def write_scan_labels(prisma: Any, scan: Any, result: dict) -> int:
    """Link the scan to every label it touched (the M:N replacement for labels[])."""
    names: set[str] = set()

    for raw in result.get("segments") or []:
        if raw.get("label"):
            names.add(str(raw["label"]))
        if raw.get("category"):
            names.add(str(raw["category"]))

    for name in (result.get("label_counts") or {}):
        names.add(str(name))

    for name in sorted(names):
        label_id = await get_label_id(prisma, name)
        await prisma.scanlabel.upsert(
            where={"scanId_labelId": {"scanId": scan.id, "labelId": label_id}},
            data={"create": {"scanId": scan.id, "labelId": label_id}, "update": {}},
        )

    return len(names)


async def persist_result(
    prisma: Any, job: Any, result: dict, model_code: str
) -> tuple[Any, list[Any]]:
    """Write a complete result and return (scan, segments)."""
    scan = await create_scan(prisma, job, result, model_code)
    segments = await write_segments(prisma, scan, result)
    await write_detections(prisma, scan, result)
    await write_scan_labels(prisma, scan, result)
    return scan, segments


async def attach_clip(
    prisma: Any,
    segment: Any,
    clip_object_key: Optional[str],
    annotated_object_key: Optional[str] = None,
) -> Any:
    """Record where a segment's cut clip ended up in object storage."""
    return await prisma.segment.update(
        where={"id": segment.id},
        data={
            "clipObjectKey": clip_object_key,
            "annotatedClipObjectKey": annotated_object_key,
        },
    )
