"""
Shared async Redis connection pool.

One pool per process, created lazily and reused by the cache, the pub/sub
progress stream and the ARQ enqueue path. `redis.asyncio` multiplexes commands
over a single connection, so opening a client per request would waste a socket
for no gain.

Every helper here degrades gracefully: if Redis is unreachable the caller gets
`None` rather than an exception, so a dead cache slows the API down but never
takes it out.
"""

import logging
from typing import Optional

from redis.asyncio import Redis, from_url

from config import settings

logger = logging.getLogger(__name__)

_client: Optional[Redis] = None
_unavailable_logged = False


async def get_redis() -> Optional[Redis]:
    """Return the shared client, or None when Redis cannot be reached."""
    global _client, _unavailable_logged

    if _client is not None:
        return _client

    try:
        client = from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=5,
            health_check_interval=30,
        )
        await client.ping()
        _client = client
        _unavailable_logged = False
        logger.info("Connected to Redis at %s", settings.redis_url)
        return _client
    except Exception as exc:
        if not _unavailable_logged:
            logger.warning(
                "Redis unavailable (%s) — cache and live progress disabled", exc
            )
            _unavailable_logged = True
        return None


async def close_redis() -> None:
    """Close the pool on application shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
