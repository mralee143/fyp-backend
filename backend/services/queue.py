"""
ARQ queue client used by the API process to hand work to the worker.

Only the job's database id crosses the queue. Every parameter of the run — the
model, frame count, threshold, OWLv2 queries — is already stored on the
`analysis_jobs` row, so the worker reads a self-describing job instead of
trusting a serialised payload. That keeps the queue message tiny, survives a
Redis flush (the job can be re-enqueued from the database) and means a retry can
never run with different arguments than the original attempt.
"""

import logging
from typing import Optional

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from config import settings

logger = logging.getLogger(__name__)

# Queue name shared by the API (producer) and worker (consumer).
QUEUE_NAME = "vision:jobs"
ANALYZE_TASK = "analyze_video"

_pool: Optional[ArqRedis] = None


def redis_settings() -> RedisSettings:
    """ARQ connection settings derived from the single REDIS_URL setting."""
    return RedisSettings.from_dsn(settings.redis_url)


async def get_pool() -> Optional[ArqRedis]:
    """Shared enqueue pool, or None when Redis is unreachable."""
    global _pool
    if _pool is not None:
        return _pool
    try:
        _pool = await create_pool(redis_settings(), default_queue_name=QUEUE_NAME)
        logger.info("ARQ queue ready on %s", QUEUE_NAME)
        return _pool
    except Exception as exc:
        logger.error("Could not connect to the ARQ queue: %s", exc)
        return None


async def close_pool() -> None:
    """Close the enqueue pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue_analysis(job_id: int, job_public_id: str) -> Optional[str]:
    """Queue an analysis job. Returns the ARQ job id, or None if queuing failed.

    `_job_id` is set from the job's public uuid so that enqueuing the same job
    twice (a double-clicked upload, a retried request) is idempotent — ARQ drops
    the duplicate instead of running the models a second time.
    """
    pool = await get_pool()
    if pool is None:
        return None

    try:
        arq_job = await pool.enqueue_job(
            ANALYZE_TASK,
            job_id,
            _job_id=f"analyze:{job_public_id}",
            _queue_name=QUEUE_NAME,
        )
        return arq_job.job_id if arq_job else None
    except Exception as exc:
        logger.error("Failed to enqueue job %s: %s", job_id, exc)
        return None


async def queue_depth() -> Optional[int]:
    """How many jobs are waiting — surfaced on the admin dashboard."""
    pool = await get_pool()
    if pool is None:
        return None
    try:
        return await pool.zcard(QUEUE_NAME)
    except Exception:
        return None
