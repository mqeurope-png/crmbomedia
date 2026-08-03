"""BoHub ERP Fase B PR B-3 — receptor de webhooks WooCommerce.

`POST /webhooks/woocommerce/{account_slug}` — fuera de `/api/*`, sin auth del
CRM: la seguridad es la firma HMAC-SHA256 por tienda. Persiste el evento en
`integration_events` (dedup por delivery-id) y encola el procesamiento async;
responde 200 en < 1s (Woo reintenta si tarda > 15s).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.models import IntegrationEvent, IntegrationEventStatus
from app.integrations.woocommerce.jobs import enqueue_webhook_event
from app.integrations.woocommerce.webhooks import (
    get_or_create_webhook_secret,
    verify_signature,
)
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _find_store(session: Session, slug: str) -> IntegrationAccount | None:
    return session.scalar(select(IntegrationAccount).where(
        IntegrationAccount.system == ExternalSystem.WOOCOMMERCE,
        IntegrationAccount.account_id == slug,
        IntegrationAccount.enabled.is_(True),
    ))


@router.post("/woocommerce/{account_slug}")
async def woocommerce_webhook(
    account_slug: str,
    request: Request,
    session: Session = Depends(get_session),
) -> Any:
    store = _find_store(session, account_slug)
    if store is None:
        return JSONResponse({"error": "unknown_store"}, status_code=404)

    # Bytes EXACTOS que envió Woo — la firma HMAC se calcula sobre ellos.
    # No llamar request.json() antes.
    raw_body = await request.body()

    secret = get_or_create_webhook_secret(session, store)
    provided = request.headers.get("X-WC-Webhook-Signature", "")
    if not verify_signature(secret, raw_body, provided):
        return JSONResponse({"error": "invalid_signature"}, status_code=401)

    delivery_id = request.headers.get("X-WC-Webhook-Delivery-ID")
    if not delivery_id:
        # Fallback raro (Woo siempre lo manda) — hash del body para dedup.
        delivery_id = hashlib.sha256(raw_body).hexdigest()
    topic = request.headers.get("X-WC-Webhook-Topic", "unknown")

    existing = session.scalar(select(IntegrationEvent).where(
        IntegrationEvent.system == "woocommerce",
        IntegrationEvent.account_id == store.account_id,
        IntegrationEvent.external_event_id == delivery_id,
    ))
    if existing is not None:
        return {"received": True, "event_id": existing.id, "duplicate": True}

    # `created_at` (TimestampMixin) hace de received_at.
    event = IntegrationEvent(
        system="woocommerce",
        account_id=store.account_id,
        external_event_id=delivery_id,
        event_type=topic,
        payload_json=raw_body.decode("utf-8", errors="replace"),
        status=IntegrationEventStatus.RECEIVED,
    )
    session.add(event)
    session.commit()

    enqueue_webhook_event(event.id)
    logger.info(
        "woo webhook recibido store=%s topic=%s delivery=%s event=%s",
        store.account_id, topic, delivery_id, event.id,
    )
    return {"received": True, "event_id": event.id, "duplicate": False}
