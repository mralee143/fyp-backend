"""
Pydantic schemas for the agent chatbot API.

Covers chat sessions, the messages inside them (including the tool-call trace
the UI renders as inspectable cards), and the request/response shapes of
`agentic/routes/chat.py`.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatSessionOut(BaseModel):
    """One conversation in the sidebar."""

    id: int
    title: str
    video_name: Optional[str] = None
    video_url: Optional[str] = None
    last_scan_id: Optional[int] = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    """One stored message. ``tool`` rows carry their payload for inspection."""

    id: int
    role: str = Field(description="'user', 'assistant' or 'tool'.")
    content: str = ""
    tool_name: Optional[str] = None
    tool_payload: Optional[dict[str, Any]] = None
    created_at: datetime


class ChatSessionDetail(BaseModel):
    """A session plus its full message history."""

    id: int
    title: str
    video_name: Optional[str] = None
    video_url: Optional[str] = None
    last_scan_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    messages: list[ChatMessageOut] = []


class ChatMessageIn(BaseModel):
    """A message the user sends to the agent."""

    message: str = Field(min_length=1, max_length=4000)


class ToolCallOut(BaseModel):
    """One tool the agent ran during a turn, with its result."""

    name: str
    arguments: dict[str, Any] = {}
    result: dict[str, Any] = {}


class ChatTurnResponse(BaseModel):
    """What the agent produced for one user message."""

    reply: str
    tool_calls: list[ToolCallOut] = []
    scan_id: Optional[int] = None
    error: bool = False


class ChatVideoResponse(BaseModel):
    """Result of attaching a video to a session.

    The job fields describe the streamed analysis queued from the same upload;
    they are null when no job could be queued (e.g. the worker's Redis is down),
    in which case the chat still works, just without live progress.
    """

    video_name: str
    video_url: str
    session_id: int
    job_id: Optional[str] = None
    events_url: Optional[str] = None
    queued: bool = False


class ChatHealthResponse(BaseModel):
    """Whether the Qwen chat backend is reachable (drives the UI banner)."""

    online: bool
    model: str
    base_url: str
    local_fallback: bool
