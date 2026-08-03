"""BoHub ERP — Admin API multi-tienda WooCommerce (Fase B PR B-2).

  POST   /api/erp/integrations/woocommerce/stores            crear
  GET    /api/erp/integrations/woocommerce/stores            listar
  PATCH  /api/erp/integrations/woocommerce/stores/{id}       editar
  POST   /api/erp/integrations/woocommerce/stores/{id}/test-connection
  POST   /api/erp/integrations/woocommerce/stores/{id}/sync-backfill

Sistema=WOOCOMMERCE, account_id=slug de tienda (boprint/artisjet/flux).
Secretos CK/CS cifrados con Fernet on-save (leídos desde el modelo
extendido en PR B-1). Solo ADMIN.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.crypto import encrypt
from app.core.errors import not_found
from app.db.session import get_session
from app.erp.api.deps import require_erp_admin
from app.erp.models import IntegrationEvent, IntegrationEventStatus
from app.integrations.woocommerce.client import WooError, WooHTTPClient
from app.integrations.woocommerce.webhooks import (
    get_or_create_webhook_secret,
    regenerate_webhook_secret,
    set_initial_webhook_secret,
    webhook_url_for,
)
from app.models.crm import ExternalSystem, User
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)

router = APIRouter(
    prefix="/api/erp/integrations/woocommerce",
    tags=["erp-woocommerce"],
)

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class StoreCreate(BaseModel):
    account_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    base_url: str = Field(min_length=1, max_length=255)
    consumer_key: str = Field(min_length=1)
    consumer_secret: str = Field(min_length=1)
    enabled: bool = True
    # B-2-fix4: pedidos anteriores a esta fecha se auto-marcan como
    # procesados externamente al importarlos (ISO 8601 o YYYY-MM-DD).
    external_cutoff_date: str | None = None


class StoreUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    base_url: str | None = Field(default=None, max_length=255)
    consumer_key: str | None = None
    consumer_secret: str | None = None
    enabled: bool | None = None
    external_cutoff_date: str | None = None


class StoreRead(BaseModel):
    id: str
    account_id: str
    display_name: str
    base_url: str
    enabled: bool
    credential_status: str
    # Nunca devolvemos los secretos.


def _metadata(a: IntegrationAccount) -> dict[str, Any]:
    if not a.metadata_json:
        return {}
    try:
        data = json.loads(a.metadata_json)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _set_cutoff(a: IntegrationAccount, value: str | None) -> None:
    meta = _metadata(a)
    if value:
        meta["external_cutoff_date"] = value
    else:
        meta.pop("external_cutoff_date", None)
    a.metadata_json = json.dumps(meta) if meta else None


def _serialise(
    a: IntegrationAccount, summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": a.id,
        "account_id": a.account_id,
        "display_name": a.display_name,
        "base_url": a.base_url or "",
        "enabled": a.enabled,
        "credential_status": a.credential_status,
        "external_cutoff_date": _metadata(a).get("external_cutoff_date"),
        # B-3: resumen de webhooks para la tabla (0 queries extra por fila).
        "webhook_summary": summary or {
            "last_received_at": None, "count_24h": 0, "errors_24h": 0,
        },
    }


def _webhook_metrics(
    session: Session, account_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Métricas de webhooks por tienda: último recibido (histórico) +
    contadores de las últimas 24h. Dos queries totales, no N por fila."""
    out: dict[str, dict[str, Any]] = {
        aid: {"last_received_at": None, "count_24h": 0, "errors_24h": 0,
              "topics_24h": []}
        for aid in account_ids
    }
    if not account_ids:
        return out
    for aid, last in session.execute(
        select(IntegrationEvent.account_id, func.max(IntegrationEvent.created_at))
        .where(
            IntegrationEvent.system == "woocommerce",
            IntegrationEvent.account_id.in_(account_ids),
        )
        .group_by(IntegrationEvent.account_id)
    ):
        if aid in out and last is not None:
            out[aid]["last_received_at"] = last.isoformat()
    since = datetime.now(UTC) - timedelta(hours=24)
    topics: dict[str, set[str]] = {aid: set() for aid in account_ids}
    for ev in session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.system == "woocommerce",
        IntegrationEvent.account_id.in_(account_ids),
        IntegrationEvent.created_at >= since,
    )):
        m = out.get(ev.account_id)
        if m is None:
            continue
        m["count_24h"] += 1
        if ev.status == IntegrationEventStatus.FAILED:
            m["errors_24h"] += 1
        topics[ev.account_id].add(ev.event_type)
    for aid in account_ids:
        out[aid]["topics_24h"] = sorted(topics[aid])
    return out


def _get_woo(session: Session, store_id: str) -> IntegrationAccount:
    account = session.get(IntegrationAccount, store_id)
    if account is None or account.system != ExternalSystem.WOOCOMMERCE:
        raise not_found("WooCommerce store")
    return account


@router.get("/stores")
def list_stores(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    _ = current_user
    rows = list(session.scalars(select(IntegrationAccount).where(
        IntegrationAccount.system == ExternalSystem.WOOCOMMERCE,
    ).order_by(IntegrationAccount.account_id)))
    metrics = _webhook_metrics(session, [r.account_id for r in rows])
    return {"items": [_serialise(r, metrics.get(r.account_id)) for r in rows]}


@router.post("/stores", status_code=201)
def create_store(
    payload: StoreCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    _ = current_user
    if not _SLUG_RE.match(payload.account_id):
        raise HTTPException(400, "account_id inválido: minúsculas/números/guiones")
    dup = session.scalar(select(IntegrationAccount.id).where(
        IntegrationAccount.system == ExternalSystem.WOOCOMMERCE,
        IntegrationAccount.account_id == payload.account_id,
    ))
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe una tienda WooCommerce con slug {payload.account_id!r}",
        )
    account = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE,
        account_id=payload.account_id,
        display_name=payload.display_name,
        enabled=payload.enabled,
        mode=IntegrationMode.LIVE,
        status=IntegrationStatus.CONFIGURED,
        base_url=payload.base_url.rstrip("/"),
        consumer_key_encrypted=encrypt(payload.consumer_key),
        consumer_secret_encrypted=encrypt(payload.consumer_secret),
        credential_status="configured",
    )
    _set_cutoff(account, payload.external_cutoff_date)
    # B-3: cada tienda nace con su secreto de webhook (misma transacción).
    set_initial_webhook_secret(account)
    session.add(account)
    session.commit()
    session.refresh(account)
    return _serialise(account)


@router.patch("/stores/{store_id}")
def update_store(
    store_id: str,
    payload: StoreUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    _ = current_user
    account = _get_woo(session, store_id)
    if payload.display_name is not None:
        account.display_name = payload.display_name
    if payload.base_url is not None:
        account.base_url = payload.base_url.rstrip("/")
    if payload.consumer_key is not None:
        account.consumer_key_encrypted = encrypt(payload.consumer_key)
    if payload.consumer_secret is not None:
        account.consumer_secret_encrypted = encrypt(payload.consumer_secret)
    if payload.enabled is not None:
        account.enabled = payload.enabled
    if payload.external_cutoff_date is not None:
        _set_cutoff(account, payload.external_cutoff_date or None)
    session.commit()
    session.refresh(account)
    return _serialise(account)


@router.get("/stores/{store_id}/webhook-status")
def webhook_status(
    store_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """URL + últimos-4 del secreto + métricas 24h para el editor de tienda."""
    _ = current_user
    account = _get_woo(session, store_id)
    secret = get_or_create_webhook_secret(session, account)
    metrics = _webhook_metrics(session, [account.account_id])[account.account_id]
    return {
        "webhook_url": webhook_url_for(account.account_id),
        "webhook_secret_last4": secret[-4:] if secret else "",
        "last_received_at": metrics["last_received_at"],
        "count_24h": metrics["count_24h"],
        "errors_24h": metrics["errors_24h"],
        "topics_received_24h": metrics["topics_24h"],
    }


@router.post("/stores/{store_id}/regenerate-webhook-secret")
def regenerate_secret(
    store_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Rota el secreto de webhook. Devuelve el nuevo COMPLETO una única vez
    (luego solo se muestran los últimos 4). Hay que actualizarlo en el admin
    de WordPress de la tienda para que los webhooks sigan validando."""
    account = _get_woo(session, store_id)
    new_secret = regenerate_webhook_secret(session, account)
    try:
        record_event(
            session, action="erp.woocommerce_webhook_secret_regenerated",
            target_type="integration_account", target_id=account.id,
            actor=current_user, metadata={"account_id": account.account_id},
        )
        session.commit()
    except Exception:  # noqa: BLE001 — audit nunca bloquea
        pass
    return {
        "webhook_secret": new_secret,
        "webhook_url": webhook_url_for(account.account_id),
    }


@router.post("/stores/{store_id}/test-connection")
def test_connection(
    store_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    _ = current_user
    account = _get_woo(session, store_id)
    try:
        WooHTTPClient(account).list_orders(per_page=1)
    except WooError as exc:
        return {"ok": False, "status": exc.status, "detail": exc.body[:500]}
    return {"ok": True}


@router.post("/stores/{store_id}/sync-backfill")
def sync_backfill(
    store_id: str,
    since_iso: str | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Encola el backfill en la cola `woocommerce:backfill`. Si Redis no
    está disponible en local, se ejecuta síncronamente (útil en pruebas)."""
    _ = current_user
    account = _get_woo(session, store_id)
    try:
        from redis import Redis  # noqa: PLC0415
        from rq import Queue  # noqa: PLC0415

        from app.integrations.woocommerce.jobs import (  # noqa: PLC0415
            WOO_QUEUE_BACKFILL,
            sync_orders_backfill,
        )
        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn: Redis = redis_connection()
        conn.ping()
        job = Queue(WOO_QUEUE_BACKFILL, connection=conn).enqueue(
            sync_orders_backfill, account.account_id, since_iso,
        )
        return {"ok": True, "queued": True, "job_id": job.id}
    except Exception as exc:  # noqa: BLE001 — sin Redis en local → ejecuta ya
        from app.integrations.woocommerce.jobs import sync_orders_backfill  # noqa: PLC0415

        try:
            outcome = sync_orders_backfill(account.account_id, since_iso)
        except WooError as werr:
            raise HTTPException(
                502, {"code": "woo_error", "detail": werr.body[:500]}
            ) from werr
        return {"ok": True, "queued": False, "outcome": outcome, "note": str(exc)[:120]}
