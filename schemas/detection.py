"""
Video detection Pydantic schemas for request/response validation.

Defines the response shapes for the OWLv2 zero-shot weapon/object
detection endpoint.
"""

from pydantic import BaseModel, Field


class Detection(BaseModel):
    """A single object detected in one sampled video frame."""
    second: float = Field(description="Timestamp of the frame in seconds.")
    timestamp: str = Field(description="Human-readable HH:MM:SS timestamp.")
    label: str = Field(description="Text query that matched (e.g. 'a knife').")
    score: float = Field(description="Detection confidence in [0, 1].")
    box_xyxy: list[float] = Field(
        description="Bounding box as [x_min, y_min, x_max, y_max] pixels."
    )


class VideoDetectionResponse(BaseModel):
    """Full result of scanning a video for weapons/objects."""
    model_id: str
    queries: list[str]
    score_threshold: float
    frames_scanned: int
    detection_count: int
    label_counts: dict[str, int]
    weapon_detected: bool
    detections: list[Detection]
