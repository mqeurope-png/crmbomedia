"""Sprint 0 — Receiver PROTOTIPO de webhooks WooCommerce.

⚠️ NO registrado en app.main a propósito (restricción Sprint 0: no tocar
producción). Para probarlo en local:

    # backend/app/main.py (SOLO en local, no commitear):
    # from app.integrations.woocommerce.webhooks_prototype import router as woo_wh
    # app.include_router(woo_wh)

WooCommerce firma cada webhook con HMAC-SHA256 del BODY CRUDO usando el
secret configurado al crear el webhook (WP admin → WooCommerce → Ajustes →
Avanzado → Webhooks), y lo manda en base64 en `X-WC-Webhook-Signature`.
Headers relevantes:
  X-WC-Webhook-Source      URL de la tienda (multi-tienda: identifica origen)
  X-WC-Webhook-Topic       p.ej. "order.created" / "order.updated"
  X-WC-Webhook-Resource    "order"
  X-WC-Webhook-Event       "created" / "updated"
  X-WC-Webhook-Signature   base64(hmac_sha256(secret, raw_body))
  X-WC-Webhook-ID / X-WC-Webhook-Delivery-ID

Nota: al crear/activar el webhook, WooCommerce manda un ping SIN payload
JSON válido (body "webhook_id=N") — responder 200 sin validar firma para
que WP lo marque activo (mismo patrón que el ping de Brevo).

Topics a configurar para el ERP: order.created, order.updated. (No hay
topic order.payment_complete nativo — el pago se detecta con order.updated
cuando `date_paid` pasa de null a fecha, o status → processing.)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/woocommerce", tags=["erp-prototype"])


def _secret_for(store: str) -> str:
    """Multi-tienda: un secret por tienda vía prefijo de entorno
    (WOO_MBOLASERS_WEBHOOK_SECRET…). En el MVP → integration_accounts."""
    return os.environ.get(f"WOO_{store.upper()}_WEBHOOK_SECRET", "")


def verify_signature(secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    if not secret or not signature_header:
        return False
    expected = base64.b64encode(
        hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, signature_header)


@router.post("/{store}")
async def receive(store: str, request: Request) -> Response:
    raw = await request.body()
    topic = request.headers.get("x-wc-webhook-topic", "")
    delivery = request.headers.get("x-wc-webhook-delivery-id", "")

    # Ping de activación (sin JSON): 200 y fuera.
    if raw.startswith(b"webhook_id="):
        logger.info("woo webhook ping store=%s body=%s", store, raw[:60])
        return Response(status_code=200)

    if not verify_signature(
        _secret_for(store), raw, request.headers.get("x-wc-webhook-signature")
    ):
        logger.warning("woo webhook firma inválida store=%s topic=%s", store, topic)
        return Response(status_code=401)

    try:
        payload = json.loads(raw)
    except ValueError:
        return Response(status_code=400)

    # Sprint 0: solo log de shape (id, status, date_paid, total, n líneas).
    logger.info(
        "woo webhook OK store=%s topic=%s delivery=%s order_id=%s status=%s "
        "date_paid=%s total=%s lines=%d",
        store, topic, delivery,
        payload.get("id"), payload.get("status"), payload.get("date_paid"),
        payload.get("total"), len(payload.get("line_items") or []),
    )
    # MVP: aquí se encola el job RQ que upserta en `orders` + dispara la
    # máquina de estados de pago (ver docs/erp/state-machines.md).
    return Response(status_code=200)
