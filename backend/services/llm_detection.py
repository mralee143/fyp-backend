"""
LLM / Vision-LLM video violence detection via the Google Gemini REST API.

Uploads the video with the Gemini **Files API** (supports large videos up to
~2 GB), waits for it to be processed, then asks Gemini for structured violence
segments with timestamps. Uses httpx directly (no SDK) to avoid dependency
conflicts with the pinned FastAPI/pydantic stack.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Transient server-side conditions, as opposed to a bad key or a bad request.
# `gemini-flash-latest` returns 503 "this model is currently experiencing high
# demand" fairly often, and it clears in seconds.
#
# Retrying matters more here than the usual robustness argument. The cascade
# treats "Gemini could not answer" as permission to fall through to the local
# classifiers, and those are the ones that call a clip of a ball a fight. So a
# blip that lasts two seconds did not degrade the answer, it changed who gave
# it — silently, and to the least reliable model in the system. Spending a few
# seconds here keeps the verdict with the model that can actually read a scene.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = int(os.getenv("GEMINI_MAX_ATTEMPTS", "4"))
_RETRY_BASE_DELAY_S = float(os.getenv("GEMINI_RETRY_BASE_DELAY_S", "2.0"))

# Google answers a 429 with a `RetryInfo` saying how long to actually wait, and
# it is routinely longer than a doubling-from-two-seconds schedule reaches — a
# rate-limited call asks for ~32s, so backing off 2s, 4s and 8s just fails four
# times in fifteen seconds and reports defeat. Honour the number it gives.
#
# Past this cap the wait is worse than the fallback: an upload already sat
# through, and the local models can answer now. A daily quota reports delays in
# this range, which is the case that must fail fast rather than sleep.
_MAX_RETRY_DELAY_S = float(os.getenv("GEMINI_MAX_RETRY_DELAY_S", "45"))


def _server_retry_delay(response: httpx.Response) -> Optional[float]:
    """Seconds Google asked us to wait, from the error's RetryInfo detail.

    Returns None when the body carries no RetryInfo — including when it is not
    JSON at all, which a proxy error page will not be.
    """
    try:
        details = response.json()["error"]["details"]
    except (ValueError, KeyError, TypeError):
        return None

    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        if not str(detail.get("@type", "")).endswith("RetryInfo"):
            continue
        raw = str(detail.get("retryDelay", "")).strip().rstrip("s")
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _quota_violations(response: httpx.Response) -> list[dict]:
    """The QuotaFailure violations in an error body, or [] if there are none."""
    try:
        details = response.json()["error"]["details"]
    except (ValueError, KeyError, TypeError):
        return []

    for detail in details if isinstance(details, list) else []:
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith(
            "QuotaFailure"
        ):
            return [v for v in (detail.get("violations") or []) if isinstance(v, dict)]
    return []


def _out_of_daily_quota(response: httpx.Response) -> bool:
    """True when this 429 is the day's allowance being gone, not a fast caller.

    Worth separating because the two want opposite handling. A per-minute limit
    clears in seconds and is worth waiting out. A per-day one does not clear
    until midnight Pacific, and Google still attaches a ~30s `retryDelay` to
    it — follow that and every single scan stalls for a minute and a half
    before falling back to a local model that could have answered immediately.
    """
    return any(
        "PerDay" in str(violation.get("quotaId") or "")
        for violation in _quota_violations(response)
    )


def _quota_hint(response: httpx.Response) -> str:
    """A plain-language reason for a 429, or "" when it is not a quota failure.

    A 429 is either "you are going too fast" or "you have used up the plan",
    and the two need different reactions from whoever reads the log — one
    clears by itself, the other needs a billing change. The distinction is in
    the QuotaFailure detail, so pull it out rather than making the operator
    read a raw API dump to find it.
    """
    try:
        details = response.json()["error"]["details"]
    except (ValueError, KeyError, TypeError):
        return ""

    for violation in _quota_violations(response):
        quota_id = str(violation.get("quotaId") or "")
        value = violation.get("quotaValue")
        if "PerDay" in quota_id:
            return (
                f"the daily quota is used up ({value} requests/day on this "
                "plan) — it resets at midnight Pacific, or raise it by "
                "enabling billing on the Google AI Studio project"
            )
        if "PerMinute" in quota_id:
            return f"the per-minute rate limit was hit ({value}/min)"
        return f"quota '{quota_id}' was exhausted (limit {value})"
    return ""

GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
UPLOAD_START_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"
FILE_API_BASE = "https://generativelanguage.googleapis.com/v1beta/"

# Matches the backend's upload cap; Files API can handle well beyond this.
MAX_FILE_BYTES = 200 * 1024 * 1024  # 200 MB

# Above this, the video is re-encoded smaller before it is uploaded.
#
# The Files API accepts hundreds of megabytes, but the uplink here does not:
# a 106 MB dashcam compilation spent ten minutes on the wire and still timed
# out, then retried three more times before the cascade gave up and handed the
# clip to the CPU classifiers — the slowest and least accurate answer available,
# reached the slowest possible way.
#
# Sending those bytes was never necessary. Gemini samples video at about one
# frame per second, so a 30 fps 720p source is mostly detail it will never look
# at. Re-encoding held the same 461-second timeline in 13 MB instead of 106 MB.
#
# Duration and frame rate are deliberately untouched: incidents come back as
# timestamps in seconds and are replayed against the *original* file, so a
# transcode that trimmed or resampled would land every segment in the wrong
# place. Only resolution and bitrate are given up.
_UPLOAD_SHRINK_ABOVE_BYTES = int(
    float(os.getenv("GEMINI_UPLOAD_SHRINK_ABOVE_MB", "24")) * 1024 * 1024
)
_UPLOAD_SHRINK_HEIGHT = int(os.getenv("GEMINI_UPLOAD_SHRINK_HEIGHT", "480"))

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
    "Most videos contain no incident at all, and reporting one that is not "
    "there is a worse error than missing a borderline one. In particular, none "
    "of the following are incidents, and none of them should be reported:\n"
    "- Sport, games and play of any kind — football, cricket, basketball, "
    "wrestling as a sport, martial arts practice, children playing, or "
    "animals playing or running.\n"
    "- Fast movement, physical effort, falls during play, crowds, cheering, "
    "shouting or celebration.\n"
    "- Everyday objects that merely resemble a weapon: a ball, bat, stick, "
    "racket, tool, phone, remote, bottle, toy, or anything held in a hand that "
    "you cannot positively identify as a real weapon. Only report a weapon you "
    "can actually see and recognise as one.\n\n"
    "Judge what the footage shows, not what it could be a clip of. Name the "
    "visible evidence for every event you report in its description; if you "
    "cannot point to what you saw, do not report the event.\n\n"
    "`violence_detected` means \"any of the five categories above was found\", "
    "not \"physical violence was found\". Set it to true whenever you report "
    "one or more segments, including when they are all accidents or all theft. "
    "Set it to false only when the segments list is empty.\n\n"
    "If nothing above occurs, return an empty segments list and "
    "violence_detected=false. A clear video is a normal, expected result — say "
    "so plainly in the summary."
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
    """Raised for configuration / API / parsing failures in LLM detection.

    `status` carries the HTTP status when the failure came from Gemini, so the
    key pool can tell a dead end apart from something the next key might not
    hit. Failures with no status (a parse error, a video too large) are the
    caller's problem and are the same on every key.
    """

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


# A failure on one key that another key may not share. 503 is the one that
# forced this: Google returns UNAVAILABLE per project, so key 1 can be refused
# the same model key 2 is served — observed directly, 3.7-flash 503 on one key
# and 200 on the other. 500/502/504 are the same kind of server-side blip, and
# a 429 that is not the daily quota is a rate limit the next key has its own
# budget for. Everything else — 400 bad request, 403 bad key, a video Gemini
# will not read — fails identically everywhere, so it still stops the pool.
_KEY_PORTABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


# --------------------------------------------------------------------------- #
# Key pool
#
# GEMINI_API_KEY may hold several keys, comma- or whitespace-separated. They are
# not load-balanced: one key is used until its *daily* allowance is gone, then
# the next takes over. Spreading calls across keys instead would burn every
# allowance at the same rate and leave nothing in reserve for the evening.
#
# Only a PerDay 429 retires a key, and only until tomorrow. A per-minute limit
# clears on its own, so rotating on one would retire a perfectly good key over a
# few seconds of impatience.
# --------------------------------------------------------------------------- #
_KEY_LOCK = threading.Lock()
# key -> the ISO date its daily quota ran out. Compared against today, so a
# retired key comes back by itself; the worker need not be restarted at midnight.
_spent_on: dict[str, str] = {}


def _configured_keys() -> list[str]:
    """Every key in GEMINI_API_KEY, in the order they were written."""
    return [k for k in re.split(r"[,\s]+", settings.gemini_api_key or "") if k]


def _live_keys() -> list[str]:
    """The configured keys that have not spent their daily quota today."""
    today = date.today().isoformat()
    with _KEY_LOCK:
        return [k for k in _configured_keys() if _spent_on.get(k) != today]


def _mark_spent(api_key: str) -> None:
    """Retire a key for the rest of the day (Google resets at midnight Pacific)."""
    with _KEY_LOCK:
        _spent_on[api_key] = date.today().isoformat()


def _is_spent(api_key: str) -> bool:
    with _KEY_LOCK:
        return _spent_on.get(api_key) == date.today().isoformat()


def _key_label(api_key: str) -> str:
    """Name a key for the log without printing the secret."""
    keys = _configured_keys()
    position = keys.index(api_key) + 1 if api_key in keys else 0
    return f"key {position}/{len(keys)} (…{api_key[-4:]})"


def _api_headers(api_key: str) -> dict:
    return {"x-goog-api-key": api_key}


# Wall-clock ceiling for the whole analysis, when a caller sets one.
#
# The per-attempt timeouts below bound a single HTTP call, not the run: four
# attempts across two keys, each allowed 900s to upload and 600s to generate,
# outlast the ARQ job itself. The `auto` cascade sets a deadline so a Gemini
# that cannot finish — a stalled upload, a dead DNS — hands over to the models
# below it instead of taking the job down with it. A ContextVar, not a global,
# because the worker analyses two videos in two threads at once.
_DEADLINE: ContextVar[Optional[float]] = ContextVar("gemini_deadline", default=None)


def _budget_left() -> Optional[float]:
    """Seconds until the caller's deadline, or None when it set none."""
    deadline = _DEADLINE.get()
    return None if deadline is None else deadline - time.monotonic()


def _check_budget(what: str) -> None:
    """Raise once the caller's deadline has passed."""
    left = _budget_left()
    if left is not None and left <= 0:
        raise LlmDetectionError(
            f"Gemini ran out of its time budget ({what}); handing over to the "
            "next model."
        )


def _request_with_retry(
    method: str, url: str, *, what: str, timeout: float, api_key: str, **kwargs
) -> httpx.Response:
    """Call Gemini, retrying transient failures with exponential backoff.

    Every leg of the analysis goes through here — session start, byte upload,
    status poll, generate — because a single unretried call is enough to lose
    the whole result: a 106 MB video that uploads fine still fails if the poll
    that follows it is answered by a dropped connection, and the caller cannot
    tell that apart from "Gemini has nothing to say about this video".

    Only conditions that clear on their own are retried — an overloaded model,
    a rate limit, a disconnect. A 400 or 403 is a bad request or a bad key and
    will fail identically every time, so it is returned immediately rather than
    spending four attempts confirming it.

    Args:
        method: HTTP verb, "GET" or "POST".
        url: Target URL.
        what: Human name for this leg, used in the log line.
        timeout: Per-attempt timeout in seconds.
        api_key: The key this call authenticates with. Every leg of one
            analysis must pass the same one — a file uploaded under one key
            belongs to that key's project and is invisible to any other.
        **kwargs: Passed through to httpx (``json``, ``content``, ``headers``).

    Returns:
        The last response received, whatever its status. Callers still check it
        — this only guarantees the transient ones have been given a fair chance.

    Raises:
        LlmDetectionError: If every attempt failed at the transport level.
    """
    headers = {**_api_headers(api_key), **(kwargs.pop("headers", None) or {})}

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        last = attempt == _MAX_ATTEMPTS
        # A deadline shortens this attempt rather than being checked after it:
        # a 900s upload timeout would otherwise sail past a 420s budget and only
        # notice on the way out.
        _check_budget(what)
        left = _budget_left()
        attempt_timeout = timeout if left is None else min(timeout, left)
        try:
            response = httpx.request(
                method, url, headers=headers, timeout=attempt_timeout, **kwargs
            )
        except httpx.HTTPError as exc:
            if last:
                raise LlmDetectionError(
                    f"Could not reach Gemini ({what}): {exc}"
                ) from exc
            # A dropped connection carries no RetryInfo, so back off on our own.
            delay = _RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
            reason = str(exc) or exc.__class__.__name__
        else:
            if response.status_code not in _RETRY_STATUSES or last:
                return response

            if response.status_code == 429 and _out_of_daily_quota(response):
                # Retire this key for the day. Retrying it is pointless, and
                # `detect_violence_llm` reads this to decide whether another
                # key can still answer before the cascade falls through.
                _mark_spent(api_key)
                logger.warning(
                    "Gemini %s: %s on %s — not retrying with it today",
                    what,
                    _quota_hint(response),
                    _key_label(api_key),
                )
                return response

            asked = _server_retry_delay(response)
            if asked is not None and asked > _MAX_RETRY_DELAY_S:
                # Google is telling us this will not clear soon. Hand back the
                # failure now so the caller can fall through to a model that
                # can answer, instead of holding the request open to wait.
                logger.info(
                    "Gemini %s: server asked for %.0fs (over the %.0fs cap) — "
                    "not retrying%s",
                    what,
                    asked,
                    _MAX_RETRY_DELAY_S,
                    f"; {_quota_hint(response)}" if response.status_code == 429 else "",
                )
                return response

            delay = asked if asked is not None else _RETRY_BASE_DELAY_S * (
                2 ** (attempt - 1)
            )
            reason = f"HTTP {response.status_code}"
            if response.status_code == 429 and (hint := _quota_hint(response)):
                reason = f"{reason} — {hint}"

        logger.info(
            "Gemini %s attempt %d/%d failed (%s); retrying in %.0fs",
            what,
            attempt,
            _MAX_ATTEMPTS,
            reason,
            delay,
        )
        left = _budget_left()
        if left is not None and delay >= left:
            _DEADLINE.set(time.monotonic())  # nothing left to retry into
            _check_budget(what)
        time.sleep(delay)

    # Unreachable: the loop either returns or raises on its final attempt.
    raise LlmDetectionError(f"Could not reach Gemini ({what}).")


def _shrink_for_upload(video_bytes: bytes, mime_type: str) -> bytes:
    """Re-encode an oversized video small enough to actually upload.

    Best-effort: any failure returns the original bytes, because a slow upload
    is still better than no analysis. Returns the original too when the encode
    fails to make it smaller, which happens on clips that were already tight.
    """
    if len(video_bytes) <= _UPLOAD_SHRINK_ABOVE_BYTES:
        return video_bytes

    try:
        import imageio_ffmpeg
    except ImportError:
        logger.info("Cannot shrink upload: imageio_ffmpeg is not installed")
        return video_bytes

    suffix = ".mp4" if "mp4" in mime_type else Path(mime_type).suffix or ".mp4"
    tmp_dir = Path(tempfile.mkdtemp(prefix="gemini_shrink_"))
    src = tmp_dir / f"src{suffix}"
    dst = tmp_dir / "small.mp4"
    try:
        src.write_bytes(video_bytes)
        proc = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                "-y", "-i", str(src),
                # -2 keeps the aspect ratio while forcing an even width, which
                # libx264 requires with yuv420p.
                "-vf", f"scale=-2:{_UPLOAD_SHRINK_HEIGHT}",
                "-c:v", "libx264", "-crf", "32", "-preset", "veryfast",
                "-an",  # the prompt asks about what is visible, never audio
                "-movflags", "+faststart", str(dst),
            ],
            capture_output=True,
            timeout=900,
        )
        if proc.returncode != 0 or not dst.exists():
            logger.info(
                "Upload shrink failed (rc=%s); sending the original",
                proc.returncode,
            )
            return video_bytes
        smaller = dst.read_bytes()
        if not smaller or len(smaller) >= len(video_bytes):
            return video_bytes
        logger.info(
            "Shrank upload %.1f MB -> %.1f MB before sending to Gemini",
            len(video_bytes) / 1048576,
            len(smaller) / 1048576,
        )
        return smaller
    except Exception as exc:
        logger.info("Upload shrink skipped (%s); sending the original", exc)
        return video_bytes
    finally:
        for f in (src, dst):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


def _upload_video(video_bytes: bytes, mime_type: str, api_key: str) -> str:
    """Upload the video via the resumable Files API; return its file_uri once ACTIVE.

    The returned URI is only usable by `api_key` — Files API uploads live in the
    key's own project, so the analysis that follows must use this same key.
    """
    n = len(video_bytes)

    # 1) Start a resumable upload session.
    start = _request_with_retry(
        "POST",
        UPLOAD_START_URL,
        what="upload init",
        api_key=api_key,
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(n),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": "scan_video"}},
        timeout=60.0,
    )
    if start.status_code != 200:
        raise LlmDetectionError(
            f"Gemini upload init error {start.status_code}: {start.text[:250]}",
            status=start.status_code,
        )
    upload_url = start.headers.get("x-goog-upload-url")
    if not upload_url:
        raise LlmDetectionError("Gemini did not return an upload URL.")

    # 2) Upload the bytes and finalize. Re-sends the whole file on a retry
    #    rather than resuming from an offset — the session supports resuming,
    #    but a dropped 100 MB upload is rare enough that the simpler path is
    #    worth more than the bandwidth it occasionally costs.
    up = _request_with_retry(
        "POST",
        upload_url,
        what="upload",
        api_key=api_key,
        headers={
            "Content-Length": str(n),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        content=video_bytes,
        timeout=600.0,
    )
    if up.status_code != 200:
        raise LlmDetectionError(
            f"Gemini upload error {up.status_code}: {up.text[:250]}",
            status=up.status_code,
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
        g = _request_with_retry(
            "GET",
            FILE_API_BASE + name,
            what="file status",
            timeout=60.0,
            api_key=api_key,
        )
        if g.status_code != 200:
            raise LlmDetectionError(
                f"Gemini file status error {g.status_code}: {g.text[:200]}",
                status=g.status_code,
            )
        state = g.json().get("state")

    if state != "ACTIVE":
        raise LlmDetectionError(
            f"Gemini could not process the video (state={state})."
        )
    return uri


def detect_violence_llm(
    video_bytes: bytes, mime_type: str, budget_s: Optional[float] = None
) -> dict:
    """
    Analyze a video for violence using Gemini (via the Files API for large videos).

    Tries each configured key in turn, moving on only when one has spent its
    *daily* allowance — see the key-pool notes above. Every other failure is
    raised immediately: a second key cannot fix a video Gemini refuses to read,
    and trying makes one bad upload cost two allowances instead of one.

    Args:
        video_bytes: The clip to analyse.
        mime_type: Its MIME type.
        budget_s: Wall-clock ceiling for the whole call, keys and retries
            included. The `auto` cascade passes one so a Gemini that cannot
            finish gives way to the next model; a directly requested `llm` run
            passes none and is bounded only by the per-attempt timeouts.

    Returns a dict matching LlmDetectionResponse:
        {model_id, violence_detected, summary, segments: [...]}
    Raises LlmDetectionError on misconfiguration, size, API, or parse failures.
    """
    token = _DEADLINE.set(
        None if budget_s is None else time.monotonic() + budget_s
    )
    try:
        return _detect_across_keys(video_bytes, mime_type)
    finally:
        _DEADLINE.reset(token)


def _detect_across_keys(video_bytes: bytes, mime_type: str) -> dict:
    """`detect_violence_llm`'s body, with the deadline already in place."""
    if not settings.llm_enabled:
        raise LlmDetectionError(
            "Gemini is not configured. Add GEMINI_API_KEY to the backend .env."
        )
    if len(video_bytes) > MAX_FILE_BYTES:
        raise LlmDetectionError(
            f"Video too large ({len(video_bytes) // (1024 * 1024)} MB). "
            f"Max is {MAX_FILE_BYTES // (1024 * 1024)} MB."
        )

    keys = _live_keys()
    if not keys:
        raise LlmDetectionError(
            f"All {len(_configured_keys())} Gemini key(s) have used up today's "
            "quota. They reset at midnight Pacific; add another key to "
            "GEMINI_API_KEY (comma-separated) to carry on before then."
        )

    last: LlmDetectionError | None = None
    for api_key in keys:
        # Handing a spent key's video to the next one is only worth it if there
        # is time left to actually analyse it.
        _check_budget("starting the next key")
        try:
            return _detect_with_key(video_bytes, mime_type, api_key)
        except LlmDetectionError as exc:
            last = exc
            if _is_spent(api_key):
                reason = "is out of daily quota"
            elif exc.status in _KEY_PORTABLE_STATUSES:
                reason = f"was refused with HTTP {exc.status}"
            else:
                # Same answer on every key — a second one only wastes its
                # allowance confirming it.
                raise
            logger.info(
                "%s %s; handing this video to the next key",
                _key_label(api_key),
                reason,
            )

    # No key could answer. The cascade takes it from here.
    raise last or LlmDetectionError("No Gemini key could answer.")


def _detect_with_key(video_bytes: bytes, mime_type: str, api_key: str) -> dict:
    """One full analysis — upload and generate — against a single key."""
    file_uri = _upload_video(
        _shrink_for_upload(video_bytes, mime_type), mime_type, api_key
    )

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
    resp = _request_with_retry(
        "POST", url, what="generate", json=payload, timeout=600.0, api_key=api_key
    )

    if resp.status_code != 200:
        # Name the cause when it is a quota. Otherwise the cascade logs a wall
        # of JSON, silently answers from a local model, and nobody learns that
        # the accurate model has been switched off by billing rather than bug.
        if resp.status_code == 429 and (hint := _quota_hint(resp)):
            raise LlmDetectionError(f"Gemini quota exceeded — {hint}.")
        raise LlmDetectionError(
            f"Gemini API error {resp.status_code}: {resp.text[:300]}",
            status=resp.status_code,
        )

    try:
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError, ValueError) as e:
        logger.error("Failed to parse Gemini response: %s", e)
        raise LlmDetectionError("Could not parse the model's response.") from e

    segments = [_normalise_segment(raw) for raw in (result.get("segments") or [])]

    # `violence_detected` is the pipeline's "something was flagged" bit — it
    # gates the whole report, across all five categories. The name is older
    # than the categories and reads narrower than it is, and Gemini answers the
    # name it is given: shown a dashcam compilation it returned ten `accident`
    # segments and `violence_detected: false`, which is defensible English and
    # would have published a report listing ten crashes above the words "no
    # incidents detected". Reported segments are what settles it.
    flagged = bool(result.get("violence_detected")) or bool(segments)
    if segments and not result.get("violence_detected"):
        logger.info(
            "Gemini returned %d segment(s) with violence_detected=false — "
            "treating the segments as the verdict",
            len(segments),
        )

    return {
        "model_id": settings.gemini_model,
        "violence_detected": flagged,
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
