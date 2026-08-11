"""
Asynchronous video analysis API.

The upload endpoint does the minimum needed to make the work durable — stream
the file to object storage, write a `videos` row and an `analysis_jobs` row,
push the id onto the queue — and returns `202 Accepted` in well under a second.
Nothing about the response waits on a model.

Progress then arrives by push, on two channels fed by the same event log:

    GET /detection/jobs/{id}/events   Server-Sent Events, for the browser
    POST to a registered webhook URL  for server-to-server consumers

A browser cannot receive an inbound webhook, which is why the UI subscribes to
SSE instead of registering a callback; the payloads are identical either way.
`GET /detection/jobs/{id}` remains available as a polling fallback.
"""

import asyncio
import hashlib
import json
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
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from prisma import Prisma

from config import settings
from middleware.auth import get_current_user
from schemas.jobs import JobCreated, JobStatus
from schemas.user import UserOut
from services import events, media_store, queue
from services.auth import decode_access_token
from services.database import get_prisma
from services.detection_runner import SUPPORTED_MODELS
from services.reference_data import get_model_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/detection/jobs", tags=["Async Detection"])

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MAX_VIDEO_SIZE_BYTES = 200 * 1024 * 1024  # 200 MB
UPLOAD_CHUNK_BYTES = 1024 * 1024  # stream in 1 MB pieces, never buffer the file

VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}


async def _current_user_from_stream_auth(
    token: Optional[str] = Query(
        None, description="JWT, for EventSource clients that cannot set headers."
    ),
    authorization: Optional[str] = Header(None),
    prisma: Prisma = Depends(get_prisma),
) -> UserOut:
    """Authenticate an SSE request.

    The browser `EventSource` API cannot attach an Authorization header, so the
    stream endpoint also accepts the token as a query parameter. The header is
    preferred whenever it is present.
    """
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:]
    elif token:
        raw = token

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials"
    )
    if not raw:
        raise unauthorized

    token_data = decode_access_token(raw)
    if token_data is None or token_data.email is None:
        raise unauthorized

    user = await prisma.user.find_unique(where={"email": token_data.email})
    if user is None:
        raise unauthorized
    return UserOut.model_validate(user)


async def _load_job(prisma: Prisma, user_id: int, job_public_id: str):
    """Fetch a job owned by `user_id` (job -> video -> user), or 404."""
    job = await prisma.analysisjob.find_unique(
        where={"publicId": job_public_id},
        include={"video": True, "model": True, "scan": True},
    )
    if job is None or job.video.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    return job


# --------------------------------------------------------------------------- #
# Submit
# --------------------------------------------------------------------------- #


@router.post("", response_model=JobCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    file: UploadFile = File(...),
    model: str = Form("auto", description="auto | yolo | owlv2 | llm | qwen | action"),
    queries: Optional[str] = Form(
        None, description="OWLv2 only: comma-separated objects to look for."
    ),
    num_frames: int = Form(24, ge=1, le=128),
    threshold: float = Form(0.2, ge=0.0, le=1.0),
    window_seconds: int = Form(3, ge=1, le=30),
    stride_seconds: int = Form(2, ge=1, le=30),
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> JobCreated:
    """Accept a video and queue it for analysis.

    Returns as soon as the bytes are safely in object storage — the models run
    in the worker. Subscribe to `events_url` to receive extracted frames within
    a second or two, then incidents as they are found.

    Uploading a file that was analysed before reuses the stored video instead of
    a second copy: the SHA-256 of the bytes is unique per user.
    """
    model = (model or "auto").lower().strip()
    if model not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown model '{model}'. Allowed: {', '.join(sorted(SUPPORTED_MODELS))}",
        )

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

    # Stream to a temp file, hashing as we go: a 200 MB upload never sits in RAM.
    handle, temp_path = tempfile.mkstemp(suffix=extension)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(handle, "wb") as buffer:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_VIDEO_SIZE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Video exceeds maximum size of "
                        f"{MAX_VIDEO_SIZE_BYTES // (1024 * 1024)} MB",
                    )
                digest.update(chunk)
                buffer.write(chunk)

        if total == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded video is empty"
            )

        checksum = digest.hexdigest()

        # Same bytes, same user -> reuse the stored object.
        video = await prisma.video.find_first(
            where={"userId": current_user.id, "checksumSha256": checksum}
        )
        reused = video is not None

        if video is None:
            object_key = media_store.video_key(current_user.id, extension)
            await media_store.put_file(
                object_key, temp_path, VIDEO_MIME.get(extension, "video/mp4")
            )
            video = await prisma.video.create(
                data={
                    "userId": current_user.id,
                    "originalFilename": file.filename,
                    "objectKey": object_key,
                    "mimeType": VIDEO_MIME.get(extension, "video/mp4"),
                    "fileSize": total,
                    "checksumSha256": checksum,
                }
            )
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    import uuid

    job = await prisma.analysisjob.create(
        data={
            "publicId": str(uuid.uuid4()),
            "videoId": video.id,
            "modelId": await get_model_id(prisma, model),
            "status": "QUEUED",
            "stage": "queued",
            "progress": 0,
            "numFrames": num_frames,
            "scoreThreshold": threshold,
            "windowSeconds": window_seconds,
            "strideSeconds": stride_seconds,
        }
    )

    # OWLv2 queries are a list, so they get rows rather than an array column.
    if model == "owlv2" and queries and queries.strip():
        parsed = [q.strip() for q in queries.split(",") if q.strip()]
        for ordinal, text in enumerate(parsed):
            await prisma.jobquery.create(
                data={"jobId": job.id, "ordinal": ordinal, "text": text}
            )

    await events.emit(
        prisma,
        job,
        events.JOB_QUEUED,
        stage="queued",
        progress=0,
        message=f"{file.filename} queued for analysis.",
    )

    arq_job_id = await queue.enqueue_analysis(job.id, job.publicId)
    if arq_job_id:
        await prisma.analysisjob.update(
            where={"id": job.id}, data={"arqJobId": arq_job_id}
        )
    else:
        logger.error("Job %s could not be queued — is the worker's Redis up?", job.publicId)

    logger.info(
        "User %s queued job %s (%s, %s bytes, model=%s, reused=%s)",
        current_user.id,
        job.publicId,
        file.filename,
        total,
        model,
        reused,
    )

    return JobCreated(
        job_id=job.publicId,
        video_id=video.id,
        status=job.status,
        model=model,
        filename=file.filename,
        queued=arq_job_id is not None,
        reused_video=reused,
        events_url=f"/detection/jobs/{job.publicId}/events",
    )


# --------------------------------------------------------------------------- #
# Status / frames
# --------------------------------------------------------------------------- #


async def _job_status(prisma: Prisma, job) -> JobStatus:
    """Build a status snapshot including every frame produced so far."""
    from services.scan_repository import _image_dict

    images = await prisma.image.find_many(
        where={"videoId": job.videoId}, order=[{"sequence": "asc"}]
    )
    last_event = await prisma.jobevent.find_first(
        where={"jobId": job.id}, order={"sequence": "desc"}
    )

    return JobStatus(
        job_id=job.publicId,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        model=job.model.code,
        filename=job.video.originalFilename,
        error=job.errorMessage,
        queued_at=job.queuedAt.isoformat() if job.queuedAt else None,
        started_at=job.startedAt.isoformat() if job.startedAt else None,
        finished_at=job.finishedAt.isoformat() if job.finishedAt else None,
        scan_id=job.scan.id if job.scan else None,
        video_id=job.videoId,
        frame_count=len(images),
        last_sequence=last_event.sequence if last_event else 0,
        frames=[await _image_dict(image) for image in images],
    )


@router.get("/{job_public_id}", response_model=JobStatus)
async def get_job(
    job_public_id: str,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> JobStatus:
    """Current state of a job, with every frame extracted so far.

    Polling fallback for clients that cannot hold an SSE connection open.
    """
    job = await _load_job(prisma, current_user.id, job_public_id)
    return await _job_status(prisma, job)


@router.get("/{job_public_id}/frames")
async def get_job_frames(
    job_public_id: str,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> list[dict]:
    """Every image extracted for this job's video, with its caption."""
    from services.scan_repository import _image_dict

    job = await _load_job(prisma, current_user.id, job_public_id)
    images = await prisma.image.find_many(
        where={"videoId": job.videoId}, order=[{"kind": "asc"}, {"sequence": "asc"}]
    )
    return [await _image_dict(image) for image in images]


@router.get("")
async def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> list[dict]:
    """The user's recent analysis jobs, newest first."""
    jobs = await prisma.analysisjob.find_many(
        where={"video": {"is": {"userId": current_user.id}}},
        include={"video": True, "model": True, "scan": True},
        order={"id": "desc"},
        take=limit,
    )
    return [
        {
            "job_id": job.publicId,
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "model": job.model.code,
            "filename": job.video.originalFilename,
            "scan_id": job.scan.id if job.scan else None,
            "queued_at": job.queuedAt.isoformat() if job.queuedAt else None,
            "finished_at": job.finishedAt.isoformat() if job.finishedAt else None,
            "error": job.errorMessage,
        }
        for job in jobs
    ]


# --------------------------------------------------------------------------- #
# Live stream
# --------------------------------------------------------------------------- #


def _sse(payload: dict) -> str:
    """Format one payload as an SSE frame, carrying its sequence as the id."""
    return (
        f"id: {payload.get('sequence', 0)}\n"
        f"event: {payload.get('event', 'message')}\n"
        f"data: {json.dumps(payload, default=str)}\n\n"
    )


@router.get("/{job_public_id}/events")
async def stream_job_events(
    job_public_id: str,
    request: Request,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    since: int = Query(0, ge=0, description="Resume after this event sequence."),
    current_user: UserOut = Depends(_current_user_from_stream_auth),
    prisma: Prisma = Depends(get_prisma),
) -> StreamingResponse:
    """Stream this job's events as they happen (Server-Sent Events).

    Frames arrive as `frame.ready` with a ready-to-render image URL, incidents
    as `segment.ready`, and the stream closes on `job.completed`/`job.failed`.

    On reconnect the browser replays `Last-Event-ID` automatically, so no event
    is missed or repeated. The subscription is opened *before* history is
    replayed, closing the window where a concurrently published event could slip
    between the two.
    """
    job = await _load_job(prisma, current_user.id, job_public_id)

    resume_from = since
    if last_event_id:
        try:
            resume_from = max(resume_from, int(last_event_id))
        except ValueError:
            pass

    async def generator():
        pubsub = await events.open_subscription(job.publicId)
        highest = resume_from

        try:
            # 1. Everything that already happened.
            for payload in await events.replay(prisma, job, resume_from):
                highest = max(highest, payload.get("sequence", 0))
                yield _sse(payload)

            # A job that finished before the client connected needs no stream.
            current = await prisma.analysisjob.find_unique(where={"id": job.id})
            if current and current.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                yield "event: stream.end\ndata: {}\n\n"
                return

            # 2. Live, with no broker -> fall back to polling the event table.
            if pubsub is None:
                async for payload in _poll_events(prisma, job, highest, request):
                    yield _sse(payload)
                yield "event: stream.end\ndata: {}\n\n"
                return

            async for payload in events.iter_subscription(pubsub, job.publicId):
                if await request.is_disconnected():
                    break
                if not payload:
                    yield ": keep-alive\n\n"  # comment frame; keeps proxies open
                    continue
                sequence = payload.get("sequence", 0)
                if sequence <= highest:
                    continue  # already replayed
                highest = sequence
                yield _sse(payload)
                if payload.get("event") in events.TERMINAL_EVENTS:
                    break

            yield "event: stream.end\ndata: {}\n\n"
        except asyncio.CancelledError:  # pragma: no cover - client hung up
            raise
        except Exception as exc:
            logger.error("SSE stream failed for job %s: %s", job.publicId, exc)
            yield f"event: stream.error\ndata: {json.dumps({'message': str(exc)})}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx buffering the stream into uselessness.
            "X-Accel-Buffering": "no",
        },
    )


async def _poll_events(prisma: Prisma, job, after: int, request: Request):
    """Database-polling fallback used when Redis pub/sub is unavailable."""
    highest = after
    for _ in range(600):  # ~20 minutes at 2s, matching the job timeout
        if await request.is_disconnected():
            return
        for payload in await events.replay(prisma, job, highest):
            highest = max(highest, payload.get("sequence", 0))
            yield payload
            if payload.get("event") in events.TERMINAL_EVENTS:
                return
        await asyncio.sleep(2)


@router.post("/{job_public_id}/retry", response_model=JobCreated)
async def retry_job(
    job_public_id: str,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> JobCreated:
    """Re-queue a failed job against the already-stored video."""
    job = await _load_job(prisma, current_user.id, job_public_id)
    if job.status not in {"FAILED", "CANCELLED"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is {job.status}; only a failed job can be retried.",
        )

    import uuid

    retry = await prisma.analysisjob.create(
        data={
            "publicId": str(uuid.uuid4()),
            "videoId": job.videoId,
            "modelId": job.modelId,
            "status": "QUEUED",
            "stage": "queued",
            "numFrames": job.numFrames,
            "scoreThreshold": job.scoreThreshold,
            "windowSeconds": job.windowSeconds,
            "strideSeconds": job.strideSeconds,
        }
    )
    arq_job_id = await queue.enqueue_analysis(retry.id, retry.publicId)

    return JobCreated(
        job_id=retry.publicId,
        video_id=retry.videoId,
        status=retry.status,
        model=job.model.code,
        filename=job.video.originalFilename,
        queued=arq_job_id is not None,
        reused_video=True,
        events_url=f"/detection/jobs/{retry.publicId}/events",
    )
