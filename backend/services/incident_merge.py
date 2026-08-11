"""
One event, one incident.

Every segment-shaped backend can report the same thing more than once. Gemini is
the worst offender: asked to watch a 20-second fight it will happily return
"pushing 3.0–7.0", "punching 5.0–9.0" and "assault 4.0–11.0" — three rows for
one event, which the report then renders as three cards and the timeline as
three overlapping bands. The operator reads that as three fights.

This module collapses those back into the single event they describe, and picks
the one second worth showing for it. Two rules do the work:

  * **Overlap or near-touch merges.** Rows that overlap, or sit within
    ``incident_merge_gap_seconds`` of each other, are the same event unless
    their categories genuinely differ *and* they barely touch.
  * **The most confident row wins the story.** The merged incident keeps that
    row's label, category and description; the span is the union, and the peak
    is where the winning row sat. Averaging descriptions would invent a
    sentence no model wrote.

The peak matters beyond the prose: it is the second the cover frame is captured
at. A merged 3.0–11.0 span has a midpoint of 7.0 that may be the lull between
two blows, whereas the winning row's own centre is the moment the model was
actually looking at.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _span(raw: dict) -> tuple[float, float]:
    """Start and end as floats, with the pair ordered and non-negative."""
    start = max(0.0, float(raw.get("start_time") or 0.0))
    end = max(0.0, float(raw.get("end_time") or 0.0))
    return (start, end) if end >= start else (end, start)


def _confidence(raw: dict) -> float:
    try:
        return float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _peak(raw: dict) -> float:
    """Where this row points, preferring an explicit peak over its midpoint."""
    reported = raw.get("peak_second")
    if reported is not None:
        try:
            return float(reported)
        except (TypeError, ValueError):
            pass
    start, end = _span(raw)
    return start + (end - start) / 2


def _same_event(current: dict, nxt: dict, gap: float) -> bool:
    """Whether two rows describe one event rather than two.

    Overlapping rows are one event whatever they are called — a model that
    labels seconds 4–9 "assault" and seconds 5–8 "harassment" is describing the
    same bodies in the same frames. Rows that only *approach* each other need to
    agree on the category before a quiet gap is swallowed, so a theft at 0:10
    and a crash at 0:12 stay two incidents.
    """
    _, current_end = _span(current)
    next_start, _ = _span(nxt)

    if next_start <= current_end:
        return True

    same_category = (current.get("category") or "other") == (nxt.get("category") or "other")
    return same_category and (next_start - current_end) <= gap


def _fuse(group: list[dict]) -> dict:
    """Collapse one group of same-event rows into a single segment."""
    best = max(group, key=_confidence)
    start = min(_span(raw)[0] for raw in group)
    end = max(_span(raw)[1] for raw in group)
    scores = [_confidence(raw) for raw in group]

    merged = dict(best)
    merged["start_time"] = round(start, 2)
    merged["end_time"] = round(max(end, start + 0.1), 2)
    merged["confidence"] = round(max(scores), 4)
    # The peak stays where the winning row sat, clamped into the merged span so
    # a frame is never captured outside the incident it illustrates.
    merged["peak_second"] = round(min(max(_peak(best), start), end), 2)
    # `focus_on_peak` leaves a uniformly-confident span at full width; give it a
    # real spread to judge now that several rows back this one.
    merged["confidence_spread"] = round(max(scores) - min(scores), 4)
    merged["merged_from"] = len(group)
    return merged


def consolidate(
    segments: list[dict],
    *,
    gap_seconds: float,
    min_confidence: float,
    max_incidents: int,
) -> list[dict]:
    """Reduce raw model segments to one row per real event, in timeline order.

    Args:
        segments: Raw segment dicts (``start_time``/``end_time``/``confidence``…).
        gap_seconds: Quiet seconds two same-category rows may be apart and still
            count as one event.
        min_confidence: Rows scoring below this are dropped — unless that would
            empty a positive result, in which case the single best row survives
            so a detection is never silently turned into "nothing found".
        max_incidents: Ceiling on the returned rows; the lowest-confidence
            incidents past it are dropped and logged.

    Returns:
        A new list of merged segments, sorted by start time.
    """
    if not segments:
        return []

    kept = [raw for raw in segments if _confidence(raw) >= min_confidence]
    if not kept:
        # Everything was weak. Better one hedged incident the operator can judge
        # than a clean-looking report that hides what the model actually saw.
        kept = [max(segments, key=_confidence)]

    kept.sort(key=lambda raw: _span(raw))

    groups: list[list[dict]] = [[kept[0]]]
    for raw in kept[1:]:
        # Compare against the group's running span, not just its last row: a
        # long row followed by a short one inside it must not start a new event.
        head = {
            "start_time": min(_span(member)[0] for member in groups[-1]),
            "end_time": max(_span(member)[1] for member in groups[-1]),
            "category": groups[-1][0].get("category"),
        }
        if _same_event(head, raw, gap_seconds):
            groups[-1].append(raw)
        else:
            groups.append([raw])

    merged = [_fuse(group) for group in groups]

    if len(merged) > max_incidents:
        merged.sort(key=_confidence, reverse=True)
        logger.info(
            "consolidate: keeping the %d most confident of %d incidents",
            max_incidents,
            len(merged),
        )
        merged = merged[:max_incidents]

    merged.sort(key=lambda raw: _span(raw))
    if len(merged) < len(segments):
        logger.info(
            "consolidate: %d raw segment(s) -> %d incident(s)", len(segments), len(merged)
        )
    return merged


def peak_of(segment: Any) -> Optional[float]:
    """The stored peak of a segment row, falling back to its midpoint.

    Works on both a raw dict and a Prisma ``Segment``, because the worker holds
    the model's dict while the chat and backfill paths hold the database row.
    """
    if segment is None:
        return None

    if isinstance(segment, dict):
        return _peak(segment)

    reported = getattr(segment, "peakSecond", None)
    if reported is not None:
        return float(reported)

    start = float(getattr(segment, "startTime", 0.0) or 0.0)
    end = float(getattr(segment, "endTime", start) or start)
    return start + (end - start) / 2 if end > start else start


__all__ = ["consolidate", "peak_of"]
