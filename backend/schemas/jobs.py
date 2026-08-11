"""
Request/response schemas for the asynchronous analysis pipeline.
"""

from typing import Optional

from pydantic import BaseModel, Field


class JobCreated(BaseModel):
    """202 response from an upload — returned before any analysis has run."""

    job_id: str = Field(description="Public job id, used on every follow-up call.")
    video_id: int = Field(description="Stored video this job analyses.")
    status: str = Field(description="QUEUED, or RUNNING if the worker picked it up.")
    model: str = Field(description="Requested detection backend.")
    filename: str
    queued: bool = Field(
        description="False when the queue was unreachable and the job is parked."
    )
    reused_video: bool = Field(
        default=False,
        description="True when this file had already been uploaded and was reused.",
    )
    events_url: str = Field(description="SSE endpoint streaming this job's progress.")


class JobFrame(BaseModel):
    """One extracted frame, ready to render."""

    id: int
    kind: str
    sequence: Optional[int] = None
    captured_at_seconds: Optional[float] = None
    caption: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    segment_id: Optional[int] = None
    url: Optional[str] = None


class JobStatus(BaseModel):
    """Snapshot of a job — the poll fallback for clients without SSE."""

    job_id: str
    status: str
    stage: Optional[str] = None
    progress: int = 0
    model: str
    filename: str
    error: Optional[str] = None
    queued_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    scan_id: Optional[int] = None
    video_id: int
    frame_count: int = 0
    last_sequence: int = 0
    frames: list[JobFrame] = Field(default_factory=list)


class WebhookEndpointIn(BaseModel):
    """Register a URL to receive job events."""

    url: str = Field(description="HTTPS endpoint that will receive signed POSTs.")
    description: Optional[str] = None
    events: list[str] = Field(
        default_factory=list,
        description="Event names to subscribe to; empty means all of them.",
    )


class WebhookEndpointOut(BaseModel):
    """A registered endpoint. The secret is returned only when it is created."""

    id: int
    url: str
    description: Optional[str] = None
    is_active: bool
    events: list[str]
    created_at: Optional[str] = None
    secret: Optional[str] = Field(
        default=None, description="Signing key — shown once, at creation."
    )


class WebhookDeliveryOut(BaseModel):
    """One delivery attempt, for the debugging view."""

    id: int
    endpoint_id: int
    event: str
    status: str
    attempt: int
    status_code: Optional[int] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: Optional[str] = None
