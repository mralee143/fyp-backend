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

from prisma import Prisma

from middleware.auth import get_current_user
from schemas.detection import VideoDetectionResponse, LlmDetectionResponse
from schemas.user import UserOut
from services.database import get_prisma
from services.scan_store import save_scan, get_stats, get_history, get_scan
from services.clip_extract import extract_clips_for_segments
from services.explain import explain_segments
from services.annotate import annotate_segments
from services.violence_detection import detect_violence
from services.video_detection import detect_weapons
from services.yolo_detection import detect_weapons_yolo
from services.llm_detection import detect_violence_llm, LlmDetectionError
from services.qwen_detection import detect_violence_qwen, QwenDetectionError
from services.action_detection import detect_actions
from ml.vid_img import DEFAULT_OBJECT_QUERIES, detections_to_segments, video_duration

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/detection", tags=["detection"])

# Allowed video extensions and upload size cap.
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MAX_VIDEO_SIZE_BYTES = 200 * 1024 * 1024  # 200 MB

# Selectable detection backends.
#   owlv2 - zero-shot, detects any free-text query (flexible, less precise)
#   yolo  - fine-tuned weapon model, fixed classes (precise on guns/knives)
DETECTION_MODELS = {"owlv2", "yolo"}

# Extension -> MIME type for Gemini inline video data.
_VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}


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
    prisma: Prisma = Depends(get_prisma),
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
        await save_scan(prisma, current_user.id, file.filename, model, result)
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


@router.post("/video/llm", response_model=LlmDetectionResponse)
async def detect_video_llm(
    file: UploadFile = File(...),
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> LlmDetectionResponse:
    """Analyze an uploaded video for violence using a vision-LLM (Gemini).

    Unlike ``/video`` (object boxes), this returns described violence
    *segments* with start/end timestamps and confidence — a higher-level
    understanding of fights, assault, weapons, robbery, shooting, explosions.

    Requires ``GEMINI_API_KEY`` in the backend environment.
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required"
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
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded video is empty"
        )
    if len(file_content) > MAX_VIDEO_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video exceeds maximum size of {MAX_VIDEO_SIZE_BYTES // (1024 * 1024)} MB",
        )

    mime = _VIDEO_MIME.get(extension, "video/mp4")
    logger.info(
        f"User {current_user.id} running LLM violence detection on "
        f"{file.filename} ({len(file_content)} bytes)"
    )

    try:
        # Blocking HTTP call to Gemini — keep the event loop free.
        result = await run_in_threadpool(
            detect_violence_llm, video_bytes=file_content, mime_type=mime
        )
        await save_scan(prisma, current_user.id, file.filename, "llm", result)
        return LlmDetectionResponse.model_validate(result)
    except LlmDetectionError as e:
        msg = str(e)
        # Map configuration/size vs upstream API failures to sensible codes.
        if "not configured" in msg:
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif "too large" in msg:
            code = status.HTTP_400_BAD_REQUEST
        else:
            code = status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=code, detail=msg)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during LLM detection: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during LLM video analysis",
        )


@router.post("/video/qwen", response_model=LlmDetectionResponse)
async def detect_video_qwen(
    file: UploadFile = File(...),
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> LlmDetectionResponse:
    """Analyze a video for violence with the local Qwen2.5-VL vision-LLM.

    Fully offline (no API key/quota). Describes violence/theft/harassment with
    timestamps. Slow on CPU — expect a few minutes per clip.
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required"
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
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded video is empty"
        )
    if len(file_content) > MAX_VIDEO_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video exceeds maximum size of {MAX_VIDEO_SIZE_BYTES // (1024 * 1024)} MB",
        )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name
        logger.info(
            f"User {current_user.id} running Qwen detection on {file.filename}"
        )
        result = await run_in_threadpool(detect_violence_qwen, video_path=tmp_path)
        await save_scan(prisma, current_user.id, file.filename, "qwen", result)
        return LlmDetectionResponse.model_validate(result)
    except QwenDetectionError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during Qwen detection: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during Qwen video analysis",
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@router.post("/video/action", response_model=LlmDetectionResponse)
async def detect_video_action(
    file: UploadFile = File(...),
    window_seconds: int = Form(3),
    stride_seconds: int = Form(2),
    threshold: float = Form(0.5),
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> LlmDetectionResponse:
    """Recognise violent/criminal *actions* in a video with a local model.

    Where ``/video`` finds objects (a gun in frame) this recognises behaviour
    over time — fighting, assault, abuse, robbery, shooting — and returns the
    segments where it happens. Runs locally, so unlike ``/video/llm`` it needs
    no API key, but it is limited to the classes it was trained on and cannot
    detect harassment (use ``/video/llm`` for that).

    Shares ``LlmDetectionResponse`` with the LLM route so both render on the
    same frontend timeline.
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required"
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
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded video is empty"
        )
    if len(file_content) > MAX_VIDEO_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video exceeds maximum size of {MAX_VIDEO_SIZE_BYTES // (1024 * 1024)} MB",
        )

    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        logger.info(
            f"User {current_user.id} running action detection on "
            f"{file.filename} ({len(file_content)} bytes)"
        )

        # Model inference is blocking/GPU-heavy - keep the event loop free.
        result = await run_in_threadpool(
            detect_actions,
            video_path=Path(tmp_path),
            window_seconds=window_seconds,
            stride_seconds=stride_seconds,
            threshold=threshold,
        )
        await save_scan(prisma, current_user.id, file.filename, "action", result)
        return LlmDetectionResponse.model_validate(result)

    except ValueError as e:
        # Raised when no windows could be read (e.g. corrupt video).
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during action detection: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during action detection",
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError as cleanup_error:
                logger.warning(
                    f"Failed to remove temp video {tmp_path}: {cleanup_error}"
                )


@router.post("/video/auto", response_model=LlmDetectionResponse)
async def detect_video_auto(
    file: UploadFile = File(...),
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> LlmDetectionResponse:
    """Analyse a video with Gemini, falling back to the local models.

    Gemini answers whenever it can, and its verdict is final either way. Only
    when it cannot answer at all — no API key, exhausted quota, unreachable —
    does the cascade run: violence classifier -> action model (13 crime types
    incl. accidents) -> YOLO weapons -> OWLv2 zero-shot objects -> Qwen VLM
    (thorough, minutes), first hit wins.

    A model that raises — no API key, checkpoint that won't load — is skipped
    and logged, so the cascade degrades rather than failing.
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required"
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
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded video is empty"
        )
    if len(file_content) > MAX_VIDEO_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video exceeds maximum size of {MAX_VIDEO_SIZE_BYTES // (1024 * 1024)} MB",
        )

    mime = _VIDEO_MIME.get(extension, "video/mp4")
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        detected: Optional[dict] = None
        clear_result: Optional[dict] = None
        duration = video_duration(Path(tmp_path))

        def as_segments(raw: dict, category: str) -> dict:
            """Reshape an object-detector result into the segment response.

            YOLO and OWLv2 answer with per-frame boxes (`weapon_detected`,
            `detections`), which `LlmDetectionResponse` cannot carry — this is
            what kept them out of the cascade.

            The detector's own verdict gates this: boxes too weak or too
            isolated to corroborate each other are not an incident, so a lone
            uncertain hit is reported as a clear video rather than becoming a
            segment on the timeline (see `vid_img.weapon_verdict`).
            """
            if not raw.get("weapon_detected"):
                return {
                    "model_id": raw.get("model_id") or "object-detector",
                    "violence_detected": False,
                    "summary": raw.get("summary")
                    or "No weapons or objects of interest were found.",
                    "segments": [],
                }

            segments = detections_to_segments(
                raw.get("detections") or [],
                duration,
                int(raw.get("frames_scanned") or 0),
                category,
            )
            labels = sorted({seg["label"] for seg in segments})
            return {
                "model_id": raw.get("model_id") or "object-detector",
                "violence_detected": bool(segments),
                "summary": (
                    f"Detected {', '.join(labels)} across {len(segments)} segment(s)."
                    if segments
                    else "No weapons or objects of interest were found."
                ),
                "segments": segments,
            }

        # Gemini decides when it can. It is the only model here that
        # understands the scene rather than pattern-matching motion or shapes,
        # so its "nothing happened" is as trustworthy as its "here it is" — and
        # a cascade that only ever takes the first *yes* has no way to report a
        # clear video, because each extra stage adds another chance to guess
        # wrong and none to overrule one. See `detection_runner._run_auto`.
        try:
            gemini = await run_in_threadpool(
                lambda: detect_violence_llm(video_bytes=file_content, mime_type=mime)
            )
            logger.info(
                "auto: Gemini answered (violence_detected=%s) — taking it as final",
                bool(gemini.get("violence_detected")),
            )
            detected = gemini if gemini.get("violence_detected") else None
            clear_result = None if detected else gemini
        except Exception as e:
            logger.info("auto: gemini skipped (%s)", str(e)[:120])
            gemini = None

        # The local models, in turn, only when Gemini could not answer at all.
        # They are a fallback for an unreachable API, not a second opinion.
        cascade = () if gemini is not None else (
            # Dedicated fight/assault classifier.
            ("violence", lambda: detect_violence(video_path=Path(tmp_path))),
            # 13 UCF-Crime classes incl. accidents/robbery. High threshold: the
            # action model over-guesses weak "Fighting/Assault" (~65%) on normal
            # clips. Real crimes score 95%+; violence handles fights.
            (
                "action",
                lambda: detect_actions(
                    video_path=Path(tmp_path),
                    window_seconds=3,
                    stride_seconds=2,
                    threshold=0.85,
                ),
            ),
            # Fixed-class weapon detector — precise on guns/knives/grenades.
            (
                "yolo",
                lambda: as_segments(
                    detect_weapons_yolo(video_path=Path(tmp_path)), "weapon"
                ),
            ),
            # Zero-shot sweep for anything the fixed classes miss.
            (
                "owlv2",
                lambda: as_segments(
                    detect_weapons(
                        video_path=Path(tmp_path), queries=DEFAULT_OBJECT_QUERIES
                    ),
                    "weapon",
                ),
            ),
            # Thorough but minutes-long on 8 GB VRAM — deliberately last.
            ("qwen", lambda: detect_violence_qwen(video_path=str(tmp_path))),
        )

        for name, run_model in cascade:
            if detected is not None:
                break
            try:
                r = await run_in_threadpool(run_model)
            except Exception as e:
                # A missing API key or an unloadable checkpoint must not sink
                # the whole cascade — the next model still gets its turn.
                logger.info("auto: %s skipped (%s)", name, str(e)[:120])
                continue
            if r.get("violence_detected"):
                logger.info("auto: %s found an incident", name)
                detected = r
            elif clear_result is None:
                clear_result = r

        result = detected or clear_result or {
            "model_id": "auto",
            "violence_detected": False,
            "summary": "No incidents detected by any model.",
            "segments": [],
        }
        # Explain each incident (what + which objects/vehicles) and cut its clip.
        if result.get("segments"):
            await run_in_threadpool(
                explain_segments, Path(tmp_path), result["segments"]
            )
            await run_in_threadpool(
                extract_clips_for_segments, tmp_path, result["segments"]
            )
            # Draw bounding boxes on each incident clip.
            await run_in_threadpool(annotate_segments, result["segments"])
        await save_scan(prisma, current_user.id, file.filename, "auto", result)
        return LlmDetectionResponse.model_validate(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during auto detection: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during detection",
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@router.get("/stats")
async def detection_stats(
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> dict:
    """Aggregate scan counts for the current user (dashboard cards)."""
    return await get_stats(prisma, current_user.id)


@router.get("/history")
async def detection_history(
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> list[dict]:
    """The current user's most recent scans (metadata)."""
    return await get_history(prisma, current_user.id)


@router.get("/scan/{scan_id}")
async def detection_scan_detail(
    scan_id: int,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> dict:
    """Full detail of one scan (for the report page)."""
    row = await get_scan(prisma, current_user.id, scan_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")
    return row
