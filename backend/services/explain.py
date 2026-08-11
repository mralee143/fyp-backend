"""
Turn a detected incident + the objects seen in the video into a plain-language
explanation, e.g. "Road Accident involving a car and a motorcycle, with people
present." Uses the OWLv2 zero-shot detector (already installed, GPU) to find the
vehicles/weapons/people involved — no external API needed.
"""

import logging
from pathlib import Path

from services.video_detection import detect_weapons

logger = logging.getLogger(__name__)

_VEHICLES = ["a car", "a motorcycle", "a truck", "a bus", "a bicycle"]
_WEAPONS = ["a gun", "a knife"]
# What OWLv2 looks for to describe an incident.
INCIDENT_QUERIES = tuple(_VEHICLES + _WEAPONS + ["a person"])

# An object must appear in at least this many sampled frames to be "involved".
_MIN_COUNT = 3


def _clean(q: str) -> str:
    return q[2:] if q.startswith("a ") else q


def _join_a(items: list[str]) -> str:
    """['car','motorcycle'] -> 'a car and a motorcycle'."""
    return " and ".join(f"a {i}" for i in items)


def detect_objects(video_path: Path, num_frames: int = 12) -> dict[str, int]:
    """Return {query: count} for objects OWLv2 finds in the video."""
    try:
        res = detect_weapons(
            video_path=video_path,
            queries=INCIDENT_QUERIES,
            num_frames=num_frames,
            score_threshold=0.25,
        )
        return res.get("label_counts", {}) or {}
    except Exception as e:
        logger.error("object detection for explanation failed: %s", e)
        return {}


def _dominant(pairs: list[tuple[str, int]]) -> list[str]:
    """Keep only objects that are frequent RELATIVE to the most-seen one.

    OWLv2 hallucinates rare false positives (a 'truck' in a motorbike crash);
    they show up with far lower counts than the real object, so we drop anything
    below half the top count.
    """
    pairs = [p for p in pairs if p[1] >= _MIN_COUNT]
    if not pairs:
        return []
    pairs.sort(key=lambda p: p[1], reverse=True)
    top = pairs[0][1]
    return [_clean(name) for name, c in pairs if c >= 0.5 * top][:2]


def compose_explanation(label: str, category: str, counts: dict[str, int]) -> str:
    """Describe an incident in a sentence from the detected objects."""
    vehicles = _dominant([(v, counts.get(v, 0)) for v in _VEHICLES])
    weapons = _dominant([(w, counts.get(w, 0)) for w in _WEAPONS])
    pcount = counts.get("a person", 0)
    people = pcount >= _MIN_COUNT
    # rough crowd size from how often 'person' is seen across sampled frames
    who = "multiple people" if pcount >= 18 else ("two or more people" if people else "")

    llow = label.lower()
    with_people = f", with {who} present" if who else ""

    if category == "accident":
        v = _join_a(vehicles) if vehicles else ""
        if v and people:
            return f"A road accident involving {v}, with people present."
        if v:
            return f"A road accident involving {v}."
        return "A road accident was detected."

    # Specific crime types (these map to the 'violence'/'other' category but
    # need their own wording, not "physical violence between people").
    if "shoot" in llow:
        wstr = f" with a {weapons[0]}" if weapons else " with a firearm"
        return f"A shooting{wstr}{with_people}."
    if "explos" in llow:
        return "An explosion was detected."
    if "arson" in llow or "fire" in llow:
        return "Arson - a fire was detected."
    if "vandal" in llow:
        return f"Vandalism / property damage{with_people}."
    if "burglar" in llow:
        return f"A burglary - someone breaking in{with_people}."

    if category == "violence":
        wstr = f" armed with a {weapons[0]}" if weapons else ""
        if who:
            return f"Physical violence between {who}{wstr}."
        if weapons:
            return f"Violence involving a {weapons[0]}."
        return "Physical violence was detected."

    if category == "theft":
        wstr = f" with a {weapons[0]}" if weapons else ""
        if who:
            return f"{label} involving {who}{wstr}."
        return f"{label} was detected."

    # harassment / other
    objs = (weapons + vehicles)[:2]
    obj_str = _join_a(objs) if objs else ""
    if obj_str and people:
        return f"{label} involving {obj_str}, with people present."
    if obj_str:
        return f"{label} involving {obj_str}."
    if people:
        return f"{label} involving people."
    return f"{label} detected."


def explain_segments(video_path: Path, segments: list[dict]) -> None:
    """Add an `explanation` to each segment in place, using detected objects."""
    if not segments:
        return
    counts = detect_objects(video_path)
    for seg in segments:
        try:
            seg["explanation"] = compose_explanation(
                seg.get("label", "Incident"), seg.get("category", ""), counts
            )
        except Exception as e:
            logger.error("compose explanation failed: %s", e)
