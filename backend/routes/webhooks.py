"""
Webhook management API.

Register a URL, pick the events you care about, and the worker will POST each
one to you as it happens — the server-to-server counterpart of the browser's SSE
stream, driven by the same `job_events` log.

Verifying a delivery:

    import hmac, hashlib, time

    def verify(secret, raw_body, header, tolerance=300):
        parts = dict(p.split("=", 1) for p in header.split(","))
        if abs(time.time() - int(parts["t"])) > tolerance:
            return False                      # too old — likely a replay
        expected = hmac.new(
            secret.encode(), f'{parts["t"]}.{raw_body}'.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, parts["v1"])

Compute the HMAC over the RAW body, not a re-serialised copy — re-encoding JSON
changes the bytes and the signature will not match.
"""

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from prisma import Prisma

from middleware.auth import get_current_user
from schemas.jobs import WebhookDeliveryOut, WebhookEndpointIn, WebhookEndpointOut
from schemas.user import UserOut
from services import webhooks
from services.database import get_prisma
from services.reference_data import WEBHOOK_EVENT_TYPES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _serialize(endpoint, include_secret: bool = False) -> WebhookEndpointOut:
    """Shape an endpoint row for the API, hiding the secret unless just created."""
    names = sorted(
        subscription.eventType.name
        for subscription in (endpoint.subscriptions or [])
        if subscription.eventType
    )
    return WebhookEndpointOut(
        id=endpoint.id,
        url=endpoint.url,
        description=endpoint.description,
        is_active=endpoint.isActive,
        events=names,
        created_at=endpoint.createdAt.isoformat() if endpoint.createdAt else None,
        secret=endpoint.secret if include_secret else None,
    )


async def _load_endpoint(prisma: Prisma, user_id: int, endpoint_id: int):
    """Fetch an endpoint owned by the user, or 404."""
    endpoint = await prisma.webhookendpoint.find_unique(
        where={"id": endpoint_id},
        include={"subscriptions": {"include": {"eventType": True}}},
    )
    if endpoint is None or endpoint.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook endpoint not found"
        )
    return endpoint


@router.get("/events")
async def list_event_types(
    current_user: UserOut = Depends(get_current_user),
) -> list[str]:
    """Every event name an endpoint can subscribe to."""
    return list(WEBHOOK_EVENT_TYPES)


@router.post("/endpoints", response_model=WebhookEndpointOut, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    payload: WebhookEndpointIn,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> WebhookEndpointOut:
    """Register a URL to receive job events.

    The response contains the signing secret. It is shown **once** — store it
    now; later reads omit it.
    """
    parsed = urlparse(payload.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL must be an absolute http(s) address.",
        )

    requested = payload.events or list(WEBHOOK_EVENT_TYPES)
    unknown = set(requested) - set(WEBHOOK_EVENT_TYPES)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown event(s): {', '.join(sorted(unknown))}",
        )

    endpoint = await prisma.webhookendpoint.create(
        data={
            "userId": current_user.id,
            "url": payload.url,
            "secret": webhooks.generate_secret(),
            "description": payload.description,
        }
    )

    for name in requested:
        event_type = await prisma.webhookeventtype.find_unique(where={"name": name})
        if event_type is not None:
            await prisma.webhooksubscription.create(
                data={"endpointId": endpoint.id, "eventTypeId": event_type.id}
            )

    created = await _load_endpoint(prisma, current_user.id, endpoint.id)
    logger.info("User %s registered webhook %s", current_user.id, payload.url)
    return _serialize(created, include_secret=True)


@router.get("/endpoints", response_model=list[WebhookEndpointOut])
async def list_endpoints(
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> list[WebhookEndpointOut]:
    """Every endpoint this user has registered."""
    endpoints = await prisma.webhookendpoint.find_many(
        where={"userId": current_user.id},
        include={"subscriptions": {"include": {"eventType": True}}},
        order={"id": "desc"},
    )
    return [_serialize(endpoint) for endpoint in endpoints]


@router.delete("/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    endpoint_id: int,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> None:
    """Remove an endpoint and stop delivering to it."""
    await _load_endpoint(prisma, current_user.id, endpoint_id)
    await prisma.webhookendpoint.delete(where={"id": endpoint_id})


@router.post("/endpoints/{endpoint_id}/test")
async def test_endpoint(
    endpoint_id: int,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> dict:
    """Send a signed `webhook.test` event so you can verify your receiver."""
    endpoint = await _load_endpoint(prisma, current_user.id, endpoint_id)
    return await webhooks.send_test(prisma, endpoint)


@router.get("/deliveries", response_model=list[WebhookDeliveryOut])
async def list_deliveries(
    limit: int = Query(50, ge=1, le=200),
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
) -> list[WebhookDeliveryOut]:
    """Recent delivery attempts across this user's endpoints."""
    deliveries = await prisma.webhookdelivery.find_many(
        where={"endpoint": {"is": {"userId": current_user.id}}},
        include={"jobEvent": True},
        order={"id": "desc"},
        take=limit,
    )
    return [
        WebhookDeliveryOut(
            id=delivery.id,
            endpoint_id=delivery.endpointId,
            event=delivery.jobEvent.eventType if delivery.jobEvent else "unknown",
            status=delivery.status,
            attempt=delivery.attempt,
            status_code=delivery.statusCode,
            error=delivery.errorMessage,
            duration_ms=delivery.durationMs,
            created_at=delivery.createdAt.isoformat() if delivery.createdAt else None,
        )
        for delivery in deliveries
    ]
