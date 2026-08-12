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
    # The verdict, and the box list it was drawn from. These can disagree on
    # purpose: boxes too weak or too isolated to corroborate each other are
    # still listed, but do not make `weapon_detected` true.
    weapon_detected: bool
    summary: str = ""
    detections: list[Detection]


class ViolenceSegment(BaseModel):
    """One violent/threatening event found by the LLM, with its time range."""
    label: str = Field(description="Short event label, e.g. 'Fighting', 'Gun'.")
    category: str = Field(
        default="violence",
        description="Event kind: 'violence', 'theft', or 'harassment'.",
    )
    description: str = Field(description="What happens in this segment.")
    start_time: float = Field(description="Start time in seconds from video start.")
    end_time: float = Field(description="End time in seconds from video start.")
    peak_second: float | None = Field(
        default=None,
        description=(
            "The single second that best shows the event — where the cover "
            "still is captured and the timeline draws its marker."
        ),
    )
    confidence: float = Field(description="Model confidence in [0, 1].")
    clip_url: str | None = Field(
        default=None, description="URL of the extracted incident clip, if available."
    )
    explanation: str | None = Field(
        default=None, description="Plain-language explanation of the incident."
    )


class LlmDetectionResponse(BaseModel):
    """Result of analyzing a video for violence with a vision-LLM (Gemini)."""
    model_id: str
    violence_detected: bool
    summary: str
    segments: list[ViolenceSegment]
