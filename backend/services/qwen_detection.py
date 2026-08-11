"""
Local Qwen2.5-VL violence detection (backend side).

The Qwen model lives in a separate venv (`qwen_env`) with its own heavy deps, so
we run inference as a subprocess and parse its structured JSON result. This
keeps the backend's pinned dependency stack untouched.
"""

import json
import logging
import os
import subprocess
from pathlib import Path

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


def detect_violence_qwen(video_path: str) -> dict:
    """
    Run Qwen2.5-VL on a video and return a dict matching LlmDetectionResponse.

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
            timeout=QWEN_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        raise QwenDetectionError("Qwen analysis timed out.") from e

    if proc.returncode != 0:
        logger.error("Qwen subprocess failed: %s", proc.stderr[-800:])
        raise QwenDetectionError(
            f"Qwen analysis failed: {proc.stderr.strip()[-300:] or 'unknown error'}"
        )

    for line in proc.stdout.splitlines():
        if line.startswith(SENTINEL):
            try:
                return json.loads(line[len(SENTINEL):])
            except json.JSONDecodeError as e:
                raise QwenDetectionError("Could not parse Qwen output.") from e

    logger.error("Qwen produced no result. stdout tail: %s", proc.stdout[-500:])
    raise QwenDetectionError("Qwen produced no result.")
