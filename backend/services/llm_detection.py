"""
LLM / Vision-LLM video violence detection via the Google Gemini REST API.

Uploads the video with the Gemini **Files API** (supports large videos up to
~2 GB), waits for it to be processed, then asks Gemini for structured violence
segments with timestamps. Uses httpx directly (no SDK) to avoid dependency
conflicts with the pinned FastAPI/pydantic stack.
"""

import json
import logging
import time

import httpx

from config import settings

logger = logging.getLogger(__name__)

GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
UPLOAD_START_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"
FILE_API_BASE = "https://generativelanguage.googleapis.com/v1beta/"

# Matches the backend's upload cap; Files API can handle well beyond this.
MAX_FILE_BYTES = 200 * 1024 * 1024  # 200 MB

# Max seconds to wait for Gemini to finish processing an uploaded video.
_PROCESS_TIMEOUT_S = 300

_PROMPT = (
    "You are a video safety analyst. Watch this video and detect any of the "
    "following:\n"
    "1. VIOLENCE — physical fighting, assault, beating, shooting, explosion, "
    "killing, or weapons (gun/knife/etc.).\n"
    "2. THEFT/ROBBERY — snatching, mugging, burglary, shoplifting, or stealing.\n"
    "3. HARASSMENT — unwanted physical contact (groping, grabbing, blocking "
    "someone's path), stalking or following, aggressive intimidation, "
    "threatening gestures, bullying, or someone visibly distressed while "
    "another person persists despite their attempts to withdraw.\n"
    "4. ACCIDENT — road/car accidents, crashes, collisions, a person falling "
    "or being struck.\n"
    "5. OTHER — fire, arson, vandalism, or any other dangerous emergency.\n\n"
    "For each event, report when it happens using the video's own timeline "
    "(seconds from the start), and set `category` to \"violence\", \"theft\", "
    "\"harassment\", \"accident\", or \"other\".\n\n"
    "REPORT EACH EVENT EXACTLY ONCE. One continuous incident is one segment, "
    "however long it runs and however many stages it passes through: an "
    "argument that becomes a shove and then a punch is a single segment "
    "spanning the whole thing, not three. Do not emit overlapping segments, do "
    "not repeat the same event under a second label, and do not split a "
    "continuous incident at a camera cut or a brief pause. Start a new segment "
    "only when the action genuinely stops and a separate event begins later.\n\n"
    "Set `peak_time` to the single second that best shows the event — the "
    "moment of the punch, the impact, the grab — not the midpoint of the span. "
    "It must fall between `start_time` and `end_time`; this is the second we "
    "capture the still image from.\n\n"
    "Harassment is judged from behaviour and context, not objects: look for "
    "one person's unwanted persistence and the other's discomfort or retreat. "
    "Only report it when the visual evidence supports it — if intent is "
    "ambiguous, either lower the confidence or leave it out. Do not infer "
    "events you cannot see.\n\n"
    "Confidence must reflect what is actually visible: use above 0.8 only when "
    "the event is unmistakable, and below 0.4 when you are guessing from "
    "partial or obstructed footage. Ordinary activity — people walking, "
    "talking, gesturing, playing, embracing, or working — is not an incident. "
    "When in doubt, report nothing rather than something.\n\n"
    "If nothing above occurs, return an empty segments list and "
    "violence_detected=false."
)

# OpenAPI-subset schema Gemini will conform its JSON output to.
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "violence_detected": {"type": "boolean"},
        "summary": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "violence",
                            "theft",
                            "harassment",
                            "accident",
                            "other",
                        ],
                    },
                    "description": {"type": "string"},
                    "start_time": {"type": "number"},
                    "end_time": {"type": "number"},
                    # The second the still is captured from — see _PROMPT.
                    "peak_time": {"type": "number"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "label",
                    "category",
                    "description",
                    "start_time",
                    "end_time",
                    "peak_time",
                    "confidence",
                ],
            },
        },
    },
    "required": ["violence_detected", "summary", "segments"],
}


class LlmDetectionError(Exception):
    """Raised for configuration / API / parsing failures in LLM detection."""


def _api_headers() -> dict:
    return {"x-goog-api-key": settings.gemini_api_key}


def _upload_video(video_bytes: bytes, mime_type: str) -> str:
    """Upload the video via the resumable Files API; return its file_uri once ACTIVE."""
    n = len(video_bytes)

    # 1) Start a resumable upload session.
    try:
        start = httpx.post(
            UPLOAD_START_URL,
            headers={
                **_api_headers(),
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(n),
                "X-Goog-Upload-Header-Content-Type": mime_type,
                "Content-Type": "application/json",
            },
            json={"file": {"display_name": "scan_video"}},
            timeout=60.0,
        )
    except httpx.HTTPError as e:
        raise LlmDetectionError(f"Could not reach Gemini (upload init): {e}") from e
    if start.status_code != 200:
        raise LlmDetectionError(
            f"Gemini upload init error {start.status_code}: {start.text[:250]}"
        )
    upload_url = start.headers.get("x-goog-upload-url")
    if not upload_url:
        raise LlmDetectionError("Gemini did not return an upload URL.")

    # 2) Upload the bytes and finalize.
    try:
        up = httpx.post(
            upload_url,
            headers={
                **_api_headers(),
                "Content-Length": str(n),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            content=video_bytes,
            timeout=600.0,
        )
    except httpx.HTTPError as e:
        raise LlmDetectionError(f"Could not upload video to Gemini: {e}") from e
    if up.status_code != 200:
        raise LlmDetectionError(
            f"Gemini upload error {up.status_code}: {up.text[:250]}"
        )

    info = up.json().get("file", {})
    name = info.get("name")  # e.g. "files/abc123"
    uri = info.get("uri")
    state = info.get("state")
    if not uri or not name:
        raise LlmDetectionError("Gemini upload returned no file reference.")

    # 3) Poll until the video is processed (ACTIVE) before we can analyze it.
    waited = 0
    while state == "PROCESSING" and waited < _PROCESS_TIMEOUT_S:
        time.sleep(3)
        waited += 3
        g = httpx.get(FILE_API_BASE + name, headers=_api_headers(), timeout=60.0)
        if g.status_code != 200:
            raise LlmDetectionError(
                f"Gemini file status error {g.status_code}: {g.text[:200]}"
            )
        state = g.json().get("state")

    if state != "ACTIVE":
        raise LlmDetectionError(
            f"Gemini could not process the video (state={state})."
        )
    return uri


def detect_violence_llm(video_bytes: bytes, mime_type: str) -> dict:
    """
    Analyze a video for violence using Gemini (via the Files API for large videos).

    Returns a dict matching LlmDetectionResponse:
        {model_id, violence_detected, summary, segments: [...]}
    Raises LlmDetectionError on misconfiguration, size, API, or parse failures.
    """
    if not settings.llm_enabled:
        raise LlmDetectionError(
            "Gemini is not configured. Add GEMINI_API_KEY to the backend .env."
        )
    if len(video_bytes) > MAX_FILE_BYTES:
        raise LlmDetectionError(
            f"Video too large ({len(video_bytes) // (1024 * 1024)} MB). "
            f"Max is {MAX_FILE_BYTES // (1024 * 1024)} MB."
        )

    file_uri = _upload_video(video_bytes, mime_type)

    payload = {
        "contents": [
            {
                "parts": [
                    {"file_data": {"mime_type": mime_type, "file_uri": file_uri}},
                    {"text": _PROMPT},
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": _RESPONSE_SCHEMA,
        },
    }

    url = GENERATE_URL.format(model=settings.gemini_model)
    try:
        resp = httpx.post(url, headers=_api_headers(), json=payload, timeout=600.0)
    except httpx.HTTPError as e:
        raise LlmDetectionError(f"Could not reach Gemini: {e}") from e

    if resp.status_code != 200:
        raise LlmDetectionError(f"Gemini API error {resp.status_code}: {resp.text[:300]}")

    try:
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError, ValueError) as e:
        logger.error("Failed to parse Gemini response: %s", e)
        raise LlmDetectionError("Could not parse the model's response.") from e

    segments = [_normalise_segment(raw) for raw in (result.get("segments") or [])]
    return {
        "model_id": settings.gemini_model,
        "violence_detected": bool(result.get("violence_detected", bool(segments))),
        "summary": str(result.get("summary", "")),
        "segments": segments,
    }


def _normalise_segment(raw: dict) -> dict:
    """Rename `peak_time` to the pipeline's `peak_second` and keep it in range.

    The model is asked for the moment worth showing, but nothing stops it
    returning one outside the span it just reported — and that second is what
    the cover frame is captured at, so an out-of-range value would illustrate
    the incident with a frame from somewhere else entirely.
    """
    segment = dict(raw)
    start = float(segment.get("start_time") or 0.0)
    end = float(segment.get("end_time") or start)
    if end < start:
        start, end = end, start
        segment["start_time"], segment["end_time"] = start, end

    peak = segment.pop("peak_time", None)
    try:
        peak = float(peak)
    except (TypeError, ValueError):
        peak = start + (end - start) / 2

    segment["peak_second"] = round(min(max(peak, start), end), 2)
    return segment
