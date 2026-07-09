"""
Video weapon/object detection routes.

Provides a FastAPI endpoint that accepts a video upload and runs OWLv2
zero-shot detection to find weapons (or any user-specified objects) in the
video, returning bounding boxes, confidence scores and timestamps.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool

from middleware.auth import get_current_user
from schemas.detection import VideoDetectionResponse
from schemas.user import UserOut
from services.video_detection import detect_weapons
from services.yolo_detection import detect_weapons_yolo
from vid_img import DEFAULT_OBJECT_QUERIES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/detection", tags=["detection"])

# Allowed video extensions and upload size cap.
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MAX_VIDEO_SIZE_BYTES = 200 * 1024 * 1024  # 200 MB

# Selectable detection backends.
#   owlv2 - zero-shot, detects any free-text query (flexible, less precise)
#   yolo  - fine-tuned weapon model, fixed classes (precise on guns/knives)
DETECTION_MODELS = {"owlv2", "yolo"}


def _parse_queries(raw: Optional[str]) -> tuple[str, ...]:
    """Parse comma-separated queries, falling back to the default list."""
    if not raw or not raw.strip():
        return DEFAULT_OBJECT_QUERIES
    parsed = tuple(q.strip() for q in raw.split(",") if q.strip())
    return parsed or DEFAULT_OBJECT_QUERIES


@router.post("/video", response_model=VideoDetectionResponse)
async def detect_video(
    file: UploadFile = File(...),
    model: str = Form(
        "owlv2",
        description="Detection backend: 'owlv2' (zero-shot, any free-text "
        "query) or 'yolo' (fine-tuned weapon model: Gun/Knife/Grenade/Explosive).",
    ),
    queries: Optional[str] = Form(
        None,
        description="OWLv2 only: comma-separated things to detect, e.g. "
        "'a gun,a knife'. Ignored by 'yolo'. Defaults to a built-in list.",
    ),
    num_frames: int = Form(24, ge=1, le=128),
    threshold: float = Form(0.2, ge=0.0, le=1.0),
    current_user: UserOut = Depends(get_current_user),
) -> VideoDetectionResponse:
    """Detect weapons/objects in an uploaded video.

    Accepts multipart/form-data with a video file plus optional detection
    parameters. Samples frames across the video and runs the selected detector,
    returning every match with a bounding box, score and timestamp.

    Two backends are available via the ``model`` field:
      * ``owlv2`` - zero-shot open-vocabulary detection driven by ``queries``.
      * ``yolo`` - a YOLOv8 model fine-tuned for weapons (fixed classes,
        higher precision on real firearms/blades); ``queries`` is ignored.

    Args:
        file: Uploaded video file (multipart/form-data).
        model: Detection backend, 'owlv2' or 'yolo'.
        queries: OWLv2-only comma-separated object descriptions to detect.
        num_frames: Number of frames to sample (1-128).
        threshold: Minimum detection confidence to keep (0.0-1.0).
        current_user: Authenticated user from JWT token.

    Returns:
        VideoDetectionResponse: Detections, per-label counts and a
        ``weapon_detected`` flag.

    Raises:
        HTTPException: 400 if the file/model is missing/invalid or too large.
        HTTPException: 401 if authentication fails.
        HTTPException: 500 if detection fails unexpectedly.
    """
    model = model.lower().strip()
    if model not in DETECTION_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown model '{model}'. Allowed: {', '.join(sorted(DETECTION_MODELS))}",
        )

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required",
        )

    extension = Path(file.filename).suffix.lower()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported video type '{extension}'. Allowed: "
                f"{', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}"
            ),
        )

    file_content = await file.read()
    if not file_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded video is empty",
        )
    if len(file_content) > MAX_VIDEO_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video exceeds maximum size of {MAX_VIDEO_SIZE_BYTES // (1024 * 1024)} MB",
        )

    parsed_queries = _parse_queries(queries)

    # decord needs a real file path, so persist to a temp file for inference.
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=extension, delete=False
        ) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        logger.info(
            f"User {current_user.id} running '{model}' detection on "
            f"{file.filename} ({len(file_content)} bytes)"
        )

        # Model inference is blocking/CPU-heavy - keep the event loop free.
        if model == "yolo":
            result = await run_in_threadpool(
                detect_weapons_yolo,
                video_path=Path(tmp_path),
                num_frames=num_frames,
                score_threshold=threshold,
            )
        else:
            result = await run_in_threadpool(
                detect_weapons,
                video_path=Path(tmp_path),
                queries=parsed_queries,
                num_frames=num_frames,
                score_threshold=threshold,
            )
        return VideoDetectionResponse.model_validate(result)

    except ValueError as e:
        # Raised when no frames could be sampled (e.g. corrupt video).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error during video detection: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during video detection",
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError as cleanup_error:
                logger.warning(
                    f"Failed to remove temp video {tmp_path}: {cleanup_error}"
                )
