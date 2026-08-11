"""
Redis caching layer.

Read-heavy endpoints (dashboard stats, scan history, a finished report) are
cached under a per-user namespace and invalidated by bumping that user's
version counter — a single INCR that logically expires every key belonging to
one user without a SCAN over the keyspace.

    cache key = "v1:{user_version}:{namespace}:{suffix}"

Cache is always optional. If Redis is down `cached_json` simply calls the
loader, so behaviour is identical, just slower.
"""

import json
import logging
from typing import Any, Awaitable, Callable, Optional

from services.redis_client import get_redis

logger = logging.getLogger(__name__)

# Bump when a cached payload's shape changes, to invalidate every old entry.
SCHEMA_VERSION = "v1"


def _version_key(user_id: int) -> str:
    return f"{SCHEMA_VERSION}:uver:{user_id}"


async def _user_version(user_id: int) -> int:
    """Current cache generation for a user. Starts at 1, bumped on every write."""
    redis = await get_redis()
    if redis is None:
        return 0
    try:
        raw = await redis.get(_version_key(user_id))
        return int(raw) if raw else 1
    except Exception:
        return 0


async def invalidate_user(user_id: int) -> None:
    """Expire every cached entry for one user (called after any write)."""
    redis = await get_redis()
    if redis is None:
        return
    try:
        await redis.incr(_version_key(user_id))
    except Exception as exc:  # pragma: no cover - cache must never break a write
        logger.debug("Cache invalidation failed for user %s: %s", user_id, exc)


async def cached_json(
    user_id: int,
    namespace: str,
    suffix: str,
    loader: Callable[[], Awaitable[Any]],
    ttl: Optional[int] = None,
) -> Any:
    """Return `loader()`'s result, served from Redis when a fresh copy exists.

    Args:
        user_id: Owner — scopes the key and ties it to that user's generation.
        namespace: Logical group, e.g. "stats", "history", "scan".
        suffix: Key within the namespace, e.g. the scan id.
        loader: Async function producing the value on a miss.
        ttl: Seconds to keep it; defaults to `settings.cache_ttl_seconds`.
    """
    from config import settings

    if not settings.cache_enabled:
        return await loader()

    redis = await get_redis()
    if redis is None:
        return await loader()

    version = await _user_version(user_id)
    key = f"{SCHEMA_VERSION}:{version}:{namespace}:{user_id}:{suffix}"

    try:
        hit = await redis.get(key)
        if hit is not None:
            return json.loads(hit)
    except Exception as exc:
        logger.debug("Cache read failed for %s: %s", key, exc)

    value = await loader()

    try:
        await redis.setex(
            key,
            ttl if ttl is not None else settings.cache_ttl_seconds,
            json.dumps(value, default=str),
        )
    except Exception as exc:
        logger.debug("Cache write failed for %s: %s", key, exc)

    return value


async def get_raw(key: str) -> Optional[str]:
    """Read a global (not user-scoped) key — used for presigned URL reuse."""
    redis = await get_redis()
    if redis is None:
        return None
    try:
        return await redis.get(f"{SCHEMA_VERSION}:{key}")
    except Exception:
        return None


async def set_raw(key: str, value: str, ttl: int) -> None:
    """Write a global (not user-scoped) key."""
    redis = await get_redis()
    if redis is None:
        return
    try:
        await redis.setex(f"{SCHEMA_VERSION}:{key}", ttl, value)
    except Exception:
        pass


async def stats() -> dict:
    """Hit/miss counters straight from Redis, for the admin dashboard."""
    redis = await get_redis()
    if redis is None:
        return {"enabled": False}
    try:
        info = await redis.info("stats")
        hits = int(info.get("keyspace_hits", 0))
        misses = int(info.get("keyspace_misses", 0))
        total = hits + misses
        return {
            "enabled": True,
            "keyspace_hits": hits,
            "keyspace_misses": misses,
            "hit_rate": round(hits / total, 4) if total else 0.0,
            "keys": await redis.dbsize(),
        }
    except Exception:
        return {"enabled": False}
