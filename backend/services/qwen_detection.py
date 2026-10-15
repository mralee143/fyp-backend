"""
Local Qwen2.5-VL violence detection (backend side).

The Qwen model lives in a separate venv (`qwen_env`) with its own heavy deps, so
we run inference as a subprocess and parse its structured JSON result. This
keeps the backend's pinned dependency stack untouched.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Python interpreter of the qwen_env venv, and the inference script. The venv
# lives beside `backend/` at the repo root; the API runs from `backend/`.
QWEN_PYTHON = os.getenv("QWEN_PYTHON", str(Path("..") / "qwen_env" / "Scripts" / "python.exe"))
QWEN_SCRIPT = os.getenv("QWEN_SCRIPT", str(Path("ml") / "qwen_infer.py"))
SENTINEL = "__QWEN_RESULT__"

# CPU inference on a 7B model is slow — allow plenty of time.
QWEN_TIMEOUT_S = int(os.getenv("QWEN_TIMEOUT_S", "1800"))


class QwenDetectionError(Exception):
    """Raised for environment / subprocess / parsing failures."""


def detect_violence_qwen(video_path: str, timeout_s: Optional[int] = None) -> dict:
    """
    Run Qwen2.5-VL on a video and return a dict matching LlmDetectionResponse.

    Args:
        video_path: Local path to the video.
        timeout_s: Override for `QWEN_TIMEOUT_S`. The `auto` cascade passes a
            shorter budget than a directly requested `qwen` run, so a slow CPU
            pass moves the cascade on instead of consuming the whole job.

    Raises QwenDetectionError on any failure.
    """
    if not Path(QWEN_PYTHON).exists():
        raise QwenDetectionError(
            f"Qwen environment not found at {QWEN_PYTHON}. Is qwen_env installed?"
        )
    if not Path(QWEN_SCRIPT).exists():
        raise QwenDetectionError(f"Qwen script not found: {QWEN_SCRIPT}")

    try:
        proc = subprocess.run(
            [QWEN_PYTHON, QWEN_SCRIPT, video_path],
            capture_output=True,
            text=True,
            timeout=timeout_s or QWEN_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        raise QwenDetectionError(
            f"Qwen analysis timed out after {timeout_s or QWEN_TIMEOUT_S}s."
        ) from e

    if proc.returncode != 0:
        detail = _stderr_gist(proc.stderr)
        logger.error("Qwen subprocess failed (exit %s): %s", proc.returncode, detail)
        raise QwenDetectionError(
            f"Qwen analysis failed (exit {proc.returncode}): {detail or 'no error output'}"
        )

    for line in proc.stdout.splitlines():
        if line.startswith(SENTINEL):
            try:
                return json.loads(line[len(SENTINEL):])
            except json.JSONDecodeError as e:
                raise QwenDetectionError("Could not parse Qwen output.") from e

    logger.error(
        "Qwen produced no result. stdout tail: %s | stderr: %s",
        proc.stdout[-300:],
        _stderr_gist(proc.stderr),
    )
    raise QwenDetectionError("Qwen produced no result.")


# tqdm draws with carriage returns and repaints the same bar hundreds of times,
# so the last 800 characters of stderr are reliably bar frames and never the
# traceback that explains the exit. Keeping only lines that are not progress
# output is the difference between a usable log line and "42%|####2".
_NOISE = re.compile(
    r"(\d+%\|)|(it/s\])|(s/it\])|^\s*$|^Fetching |^Loading weights|unauthenticated requests"
)


def _stderr_gist(stderr: str, limit: int = 400) -> str:
    """The part of a failed run's stderr worth putting in a log line."""
    lines = [
        line.strip()
        for chunk in (stderr or "").splitlines()
        for line in chunk.split("\r")
        if line.strip() and not _NOISE.search(line)
    ]
    return " | ".join(lines)[-limit:]
