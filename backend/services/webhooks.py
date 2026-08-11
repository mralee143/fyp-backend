"""
Outbound webhook delivery.

A user registers an HTTPS endpoint and subscribes it to the events they care
about (`frame.ready`, `job.completed`, ...). When the worker emits an event,
every matching endpoint receives a signed POST — the same payload the browser
sees over SSE.

Signature
    Each request carries a Stripe-style header:

        X-Vision-Signature: t=<unix-seconds>,v1=<hex hmac-sha256>

    where the signed string is "<t>.<raw request body>" and the key is the
    endpoint's `secret`. Receivers must recompute the HMAC over the RAW body
    (not a re-serialised copy) and compare in constant time. Including the
    timestamp inside the signed string is what stops a captured request being
    replayed later — reject anything older than a few minutes.

Retries
    A delivery is retried with exponential backoff on any non-2xx response or
    network error, up to `settings.webhook_max_attempts`. Each attempt updates
    the same `webhook_deliveries` row, so the attempt count and the last error
    are always visible.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Backoff before attempt 2, 3, 4, 5 — capped so a dead endpoint stops soon.
_BACKOFF_SECONDS = (2, 8, 30, 120)


def generate_secret() -> str:
    """A fresh signing secret for a newly registered endpoint."""
    return f"whsec_{secrets.token_urlsafe(32)}"


def sign(secret: str, body: str, timestamp: Optional[int] = None) -> tuple[int, str]:
    """Return (timestamp, hex signature) for a raw request body."""
    ts = timestamp if timestamp is not None else int(time.time())
    digest = hmac.new(
        secret.encode("utf-8"), f"{ts}.{body}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return ts, digest


def verify(secret: str, body: str, header: str, tolerance_seconds: int = 300) -> bool:
    """Verify an `X-Vision-Signature` header. Provided for receivers and tests."""
    try:
        parts = dict(piece.split("=", 1) for piece in header.split(","))
        ts = int(parts["t"])
        received = parts["v1"]
    except (ValueError, KeyError):
        return False

    if abs(time.time() - ts) > tolerance_seconds:
        return False

    _, expected = sign(secret, body, ts)
    return hmac.compare_digest(expected, received)


async def _owner_id(prisma: Any, job: Any) -> Optional[int]:
    """Resolve the job's owner through job -> video -> user."""
    video = await prisma.video.find_unique(where={"id": job.videoId})
    return video.userId if video else None


async def _endpoints_for(prisma: Any, user_id: int, event_type: str) -> list[Any]:
    """Active endpoints of `user_id` subscribed to `event_type`."""
    return await prisma.webhookendpoint.find_many(
        where={
            "userId": user_id,
            "isActive": True,
            "subscriptions": {"some": {"eventType": {"is": {"name": event_type}}}},
        }
    )


async def dispatch_event(prisma: Any, job: Any, event: Any, payload: dict) -> None:
    """Deliver one job event to every endpoint subscribed to it."""
    try:
        user_id = await _owner_id(prisma, job)
        if user_id is None:
            return

        endpoints = await _endpoints_for(prisma, user_id, event.eventType)
        if not endpoints:
            return

        body = json.dumps(payload, default=str, separators=(",", ":"))
        await asyncio.gather(
            *(_deliver(prisma, endpoint, event, body) for endpoint in endpoints),
            return_exceptions=True,
        )
    except Exception as exc:  # pragma: no cover - never break the pipeline
        logger.error("Webhook dispatch failed for job %s: %s", job.id, exc)


async def _deliver(prisma: Any, endpoint: Any, event: Any, body: str) -> None:
    """POST one event to one endpoint, retrying with backoff until it sticks."""
    delivery = await prisma.webhookdelivery.create(
        data={"endpointId": endpoint.id, "jobEventId": event.id, "status": "PENDING"}
    )

    for attempt in range(1, settings.webhook_max_attempts + 1):
        started = time.perf_counter()
        status_code: Optional[int] = None
        error: Optional[str] = None

        try:
            ts, signature = sign(endpoint.secret, body)
            async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
                response = await client.post(
                    endpoint.url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "vision-webhooks/1.0",
                        "X-Vision-Event": event.eventType,
                        "X-Vision-Delivery": str(delivery.id),
                        "X-Vision-Attempt": str(attempt),
                        "X-Vision-Signature": f"t={ts},v1={signature}",
                    },
                )
            status_code = response.status_code
        except Exception as exc:
            error = str(exc)[:500]

        duration_ms = int((time.perf_counter() - started) * 1000)
        succeeded = status_code is not None and 200 <= status_code < 300
        exhausted = attempt >= settings.webhook_max_attempts

        await prisma.webhookdelivery.update(
            where={"id": delivery.id},
            data={
                "attempt": attempt,
                "status": "DELIVERED" if succeeded else ("FAILED" if exhausted else "PENDING"),
                "statusCode": status_code,
                "errorMessage": error,
                "durationMs": duration_ms,
                "deliveredAt": datetime.now(timezone.utc) if succeeded else None,
            },
        )

        if succeeded:
            return

        if exhausted:
            logger.warning(
                "Webhook %s gave up after %d attempts (last status=%s error=%s)",
                endpoint.url,
                attempt,
                status_code,
                error,
            )
            return

        await asyncio.sleep(_BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)])


async def send_test(prisma: Any, endpoint: Any) -> dict:
    """Fire a synthetic `webhook.test` event so a user can verify their endpoint."""
    payload = {
        "event": "webhook.test",
        "endpoint_id": endpoint.id,
        "message": "Test delivery from the vision API.",
        "created_at": time.time(),
    }
    body = json.dumps(payload, default=str, separators=(",", ":"))
    ts, signature = sign(endpoint.secret, body)

    try:
        async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
            response = await client.post(
                endpoint.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "vision-webhooks/1.0",
                    "X-Vision-Event": "webhook.test",
                    "X-Vision-Signature": f"t={ts},v1={signature}",
                },
            )
        return {"ok": 200 <= response.status_code < 300, "status_code": response.status_code}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}
