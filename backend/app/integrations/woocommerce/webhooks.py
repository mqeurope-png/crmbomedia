"""BoHub ERP Fase B PR B-3 — helpers de webhooks WooCommerce.

Secreto por tienda (en `IntegrationAccount.metadata_json["webhook_secret"]`,
sin migración), verificación de firma HMAC-SHA256 y construcción del URL
público del receptor. Compartido por el receptor (`app.webhooks.woocommerce`)
y la admin API (`app.erp.api.woocommerce_admin`).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.integration_settings import IntegrationAccount

WEBHOOK_SECRET_KEY = "webhook_secret"

#: Topics de WooCommerce que disparan un import. Cualquier otro (product.*,
#: el ping de alta, etc.) se marca `ignored`.
SUPPORTED_TOPICS = frozenset({
    "order.created", "order.updated", "order.payment_complete",
})


def _metadata(store: IntegrationAccount) -> dict[str, Any]:
    if not store.metadata_json:
        return {}
    try:
        data = json.loads(store.metadata_json)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _write_metadata(store: IntegrationAccount, meta: dict[str, Any]) -> None:
    store.metadata_json = json.dumps(meta) if meta else None


def _new_secret() -> str:
    return secrets.token_urlsafe(32)


def get_or_create_webhook_secret(session: Session, store: IntegrationAccount) -> str:
    """Devuelve el secreto de la tienda; lo genera y persiste si falta.
    Idempotente — pensado para las 3 tiendas creadas antes de B-3."""
    meta = _metadata(store)
    secret = meta.get(WEBHOOK_SECRET_KEY)
    if not secret:
        secret = _new_secret()
        meta[WEBHOOK_SECRET_KEY] = secret
        _write_metadata(store, meta)
        session.commit()
    return secret


def set_initial_webhook_secret(store: IntegrationAccount) -> str:
    """Estampa un secreto nuevo en el metadata en memoria (sin commit) — para
    usar al crear la tienda, dentro de la misma transacción del create."""
    meta = _metadata(store)
    secret = _new_secret()
    meta[WEBHOOK_SECRET_KEY] = secret
    _write_metadata(store, meta)
    return secret


def regenerate_webhook_secret(session: Session, store: IntegrationAccount) -> str:
    """Rota el secreto (invalida el anterior). Persiste y devuelve el nuevo."""
    meta = _metadata(store)
    secret = _new_secret()
    meta[WEBHOOK_SECRET_KEY] = secret
    _write_metadata(store, meta)
    session.commit()
    return secret


def compute_signature(secret: str, raw_body: bytes) -> str:
    """base64(HMAC-SHA256(secret, raw_body)) — el formato de
    `X-WC-Webhook-Signature`."""
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_signature(secret: str, raw_body: bytes, provided: str | None) -> bool:
    """Comparación en tiempo constante contra la firma recibida."""
    if not provided:
        return False
    return hmac.compare_digest(compute_signature(secret, raw_body), provided)


def webhook_url_for(slug: str) -> str:
    """URL público del receptor para una tienda. Usa `frontend_base_url`
    (el mismo host que nginx sirve; `/webhooks/*` se proxya al api)."""
    base = (get_settings().frontend_base_url or "").rstrip("/")
    return f"{base}/webhooks/woocommerce/{slug}"
