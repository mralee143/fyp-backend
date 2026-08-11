"""
Object storage for pipeline media: uploaded videos, extracted frames and cut
incident clips.

Everything the pipeline produces lands in the `vision-media` bucket and the
database stores only the object key. The browser never talks to MinIO with
credentials — it receives short-lived presigned URLs, which are themselves
cached in Redis so that re-rendering a report does not re-sign the same object
dozens of times.

The MinIO SDK is synchronous, so every call is pushed to a worker thread to keep
the event loop free.

Key layout
    videos/{user_id}/{uuid}{ext}
    frames/{video_id}/{sequence:04d}.jpg
    frames/{video_id}/annotated/{sequence:04d}.jpg
    clips/{scan_id}/{ordinal}.mp4
"""

import asyncio
import logging
import uuid
from datetime import timedelta
from io import BytesIO
from typing import Optional

from minio import Minio
from minio.error import S3Error

from config import settings
from services import cache

logger = logging.getLogger(__name__)

# Object keys that predate object storage (backfilled rows) — no object exists.
LEGACY_PREFIX = "legacy/"

_client: Optional[Minio] = None
_signing_client: Optional[Minio] = None


def get_client() -> Minio:
    """Lazily build the media-bucket client and ensure the bucket exists."""
    global _client
    if _client is None:
        client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        if not client.bucket_exists(settings.minio_media_bucket):
            client.make_bucket(settings.minio_media_bucket)
            logger.info("Created media bucket %s", settings.minio_media_bucket)
        _client = client
    return _client


# --------------------------------------------------------------------------- #
# Key builders — one place that knows the layout
# --------------------------------------------------------------------------- #


def video_key(user_id: int, extension: str) -> str:
    """Key for a newly uploaded video."""
    return f"videos/{user_id}/{uuid.uuid4().hex}{extension}"


def frame_key(video_id: int, sequence: int, annotated: bool = False) -> str:
    """Key for one extracted frame of a video."""
    folder = "annotated/" if annotated else ""
    return f"frames/{video_id}/{folder}{sequence:04d}.jpg"


def clip_key(scan_id: int, ordinal: int, annotated: bool = False) -> str:
    """Key for one cut incident clip."""
    suffix = "-boxed" if annotated else ""
    return f"clips/{scan_id}/{ordinal}{suffix}.mp4"


# --------------------------------------------------------------------------- #
# Transfer
# --------------------------------------------------------------------------- #


def put_bytes_sync(object_key: str, data: bytes, content_type: str) -> str:
    """Blocking upload. Called directly from worker threads."""
    client = get_client()
    client.put_object(
        bucket_name=settings.minio_media_bucket,
        object_name=object_key,
        data=BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return object_key


async def put_bytes(object_key: str, data: bytes, content_type: str) -> str:
    """Upload bytes without blocking the event loop."""
    return await asyncio.to_thread(put_bytes_sync, object_key, data, content_type)


def put_file_sync(object_key: str, file_path: str, content_type: str) -> str:
    """Blocking upload straight from a path — avoids reading a video into RAM."""
    client = get_client()
    client.fput_object(
        bucket_name=settings.minio_media_bucket,
        object_name=object_key,
        file_path=file_path,
        content_type=content_type,
    )
    return object_key


async def put_file(object_key: str, file_path: str, content_type: str) -> str:
    """Upload a file from disk without blocking the event loop."""
    return await asyncio.to_thread(put_file_sync, object_key, file_path, content_type)


def get_bytes_sync(object_key: str) -> bytes:
    """Blocking download of a whole object (used for chat frame attachments)."""
    client = get_client()
    response = None
    try:
        response = client.get_object(settings.minio_media_bucket, object_key)
        return response.read()
    finally:
        if response is not None:
            response.close()
            response.release_conn()


async def get_bytes(object_key: str) -> bytes:
    """Download an object without blocking the event loop."""
    return await asyncio.to_thread(get_bytes_sync, object_key)


def fget_sync(object_key: str, destination: str) -> str:
    """Blocking download to a local path — the worker needs a real file for ffmpeg."""
    get_client().fget_object(settings.minio_media_bucket, object_key, destination)
    return destination


async def remove(object_key: str) -> None:
    """Best-effort delete; a missing object is not an error."""
    def _remove() -> None:
        try:
            get_client().remove_object(settings.minio_media_bucket, object_key)
        except S3Error:
            pass

    await asyncio.to_thread(_remove)


# --------------------------------------------------------------------------- #
# Presigned URLs
# --------------------------------------------------------------------------- #


def _get_signing_client() -> Minio:
    """The client used to sign browser-facing URLs.

    Normally the same client that uploads. It differs only when the API reaches
    MinIO under a name the browser cannot resolve — in Docker the API talks to
    ``minio:9000`` while the browser only knows ``localhost:9000``. A presigned
    URL covers the Host header in its signature, so the host cannot be swapped
    after the fact: the URL has to be signed against the public name from the
    start.

    The region has to be pinned at construction. Left unset, the SDK looks it up
    with a live GetBucketLocation call before signing — and this client points at
    an address only the browser can reach, so that lookup would fail. It is read
    once from the working client instead, and the signature carries it.
    """
    global _signing_client
    if not settings.minio_public_endpoint:
        return get_client()
    if _signing_client is None:
        client = get_client()
        try:
            region = client._get_region(settings.minio_media_bucket)
        except Exception:  # private API, and MinIO's default is us-east-1
            region = None
        _signing_client = Minio(
            endpoint=settings.minio_public_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region=region or "us-east-1",
        )
    return _signing_client


def _presign_sync(object_key: str, ttl: int) -> str:
    return _get_signing_client().presigned_get_object(
        settings.minio_media_bucket, object_key, expires=timedelta(seconds=ttl)
    )


async def presigned_url(object_key: str) -> Optional[str]:
    """A time-limited GET URL for an object, reused from Redis while valid.

    The cached copy expires a minute before the signature does, so a URL handed
    to the browser is never on the edge of expiry.
    """
    if not object_key:
        return None

    ttl = settings.presigned_url_ttl_seconds
    cache_key = f"presign:{settings.minio_media_bucket}:{object_key}"

    hit = await cache.get_raw(cache_key)
    if hit:
        return hit

    try:
        url = await asyncio.to_thread(_presign_sync, object_key, ttl)
    except Exception as exc:
        logger.warning("Presign failed for %s: %s", object_key, exc)
        return None

    await cache.set_raw(cache_key, url, max(ttl - 60, 60))
    return url


async def resolve_media_url(object_key: Optional[str]) -> Optional[str]:
    """Turn any stored media reference into something the browser can fetch.

    Handles the three shapes that can appear in the database:
      * ``None``                 -> no media
      * ``/media/clips/x.mp4``   -> a clip written before object storage; still
                                    served by the FastAPI static mount
      * ``legacy/...``           -> a backfilled row whose file no longer exists
      * anything else            -> a MinIO key, returned as a presigned URL
    """
    if not object_key:
        return None
    if object_key.startswith("/"):
        return object_key
    if object_key.startswith(LEGACY_PREFIX):
        return None
    return await presigned_url(object_key)
