"""RQ jobs de la integración WooCommerce (Fase B PR B-2).

`import_order_from_event(event_id)` consume una row de `integration_events`
y crea/actualiza el Order en el ERP. Idempotente: si `external_event_id`
ya está `processed`, no re-procesa. Reintentos con backoff exponencial
(RETRY_BACKOFF_SECONDS del modelo) — al 5º fallo → `failed` (visible en
bandeja de excepciones).

`sync_orders_backfill(store_account_id, since_iso)` importa el histórico
de una tienda (páginas de 50) sin depender de webhooks — útil tras alta
o para recuperarse de webhooks perdidos.

Los jobs se encolan en `worker-sync` (patrón CRM), cola
`woocommerce:import` o `woocommerce:backfill`.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.session import get_engine
from app.erp.models import (
    MAX_RETRIES,
    RETRY_BACKOFF_SECONDS,
    IntegrationEvent,
    IntegrationEventStatus,
)
from app.integrations.woocommerce.client import WooError, WooHTTPClient
from app.integrations.woocommerce.mapper import ImportOutcome, import_woo_order
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

WOO_QUEUE_IMPORT = "woocommerce:import"
WOO_QUEUE_BACKFILL = "woocommerce:backfill"


def _session_factory() -> sessionmaker:
    """Instancia una session-factory ligada al engine actual. Los jobs RQ
    crean su propia sesión (no reusan la del request); esto vive fuera
    del pool de FastAPI y se cierra al terminar el job."""
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def _get_store(session, account_id: str) -> IntegrationAccount | None:
    return session.scalar(select(IntegrationAccount).where(
        IntegrationAccount.system == ExternalSystem.WOOCOMMERCE,
        IntegrationAccount.account_id == account_id,
    ))


def import_order_from_event(event_id: str) -> dict[str, Any]:
    """Procesa un `integration_events` row. Devuelve dict con outcome
    para el logging del worker."""
    with _session_factory()() as session:
        event = session.get(IntegrationEvent, event_id)
        if event is None:
            return {"skipped": True, "reason": "event_not_found"}
        if event.status == IntegrationEventStatus.PROCESSED:
            return {"skipped": True, "reason": "already_processed"}
        if event.system != "woocommerce":
            return {"skipped": True, "reason": "wrong_system"}

        event.status = IntegrationEventStatus.PROCESSING
        session.commit()

        store = _get_store(session, event.account_id)
        if store is None:
            _mark_failed(session, event, f"store {event.account_id!r} not found")
            return {"ok": False, "error": "store_not_found"}

        try:
            payload = json.loads(event.payload_json)
            # Anota el slug para que el mapper lo use en origin_account_id
            # y en el prefijo del order_number.
            payload["_store_slug"] = store.account_id
            outcome = import_woo_order(session, store=store, woo_order=payload)
            event.status = IntegrationEventStatus.PROCESSED
            event.processed_at = datetime.now(UTC)
            event.error_message = None
            session.commit()
            return _outcome_dict(outcome, event_id)
        except (WooError, ValueError) as exc:
            _mark_retry_or_failed(session, event, str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 — el worker RQ lo re-encolará
            _mark_retry_or_failed(session, event, f"unexpected: {exc}")
            raise


def _mark_failed(session, event: IntegrationEvent, message: str) -> None:
    event.status = IntegrationEventStatus.FAILED
    event.error_message = message
    session.commit()


def _mark_retry_or_failed(session, event: IntegrationEvent, message: str) -> None:
    event.error_message = message[:2000]
    if event.retry_count + 1 >= MAX_RETRIES:
        event.status = IntegrationEventStatus.FAILED
        event.next_retry_at = None
    else:
        event.status = IntegrationEventStatus.RECEIVED
        delay = RETRY_BACKOFF_SECONDS[event.retry_count]
        event.retry_count += 1
        event.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
    session.commit()


def _outcome_dict(outcome: ImportOutcome, event_id: str) -> dict[str, Any]:
    return {
        "event_id": event_id, "order_id": outcome.order_id,
        "created": outcome.created,
        "contact_created": outcome.contact_created,
        "company_created": outcome.company_created,
        "unmapped_skus_count": len(outcome.unmapped_skus),
    }


def sync_orders_backfill(
    store_account_id: str, since_iso: str | None = None,
    max_pages: int = 20,
) -> dict[str, Any]:
    """Descarga hasta `max_pages * 50` pedidos desde `since_iso` y los
    escribe en `integration_events` (dedup por Woo id), luego el worker
    los procesará como si vinieran por webhook."""
    with _session_factory()() as session:
        store = _get_store(session, store_account_id)
        if store is None:
            return {"ok": False, "error": "store_not_found"}
        client = WooHTTPClient(store)

        enqueued = 0
        skipped = 0
        for page in range(1, max_pages + 1):
            orders = client.list_orders(
                status="processing", since=since_iso, per_page=50, page=page,
            )
            if not orders:
                break
            for woo in orders:
                if _event_exists(session, store, woo):
                    skipped += 1
                    continue
                session.add(IntegrationEvent(
                    system="woocommerce",
                    account_id=store.account_id,
                    external_event_id=f"backfill:{woo['id']}",
                    event_type="order.backfill",
                    payload_json=json.dumps(woo, default=str),
                ))
                enqueued += 1
            session.commit()
        return {"ok": True, "enqueued": enqueued, "skipped": skipped}


def _event_exists(session, store: IntegrationAccount, woo: dict[str, Any]) -> bool:
    """Dedup por Woo id — el backfill no repite eventos ya creados."""
    row = session.scalar(select(IntegrationEvent).where(
        IntegrationEvent.system == "woocommerce",
        IntegrationEvent.account_id == store.account_id,
        IntegrationEvent.external_event_id == f"backfill:{woo['id']}",
    ))
    return row is not None
