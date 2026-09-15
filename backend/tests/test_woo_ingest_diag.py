"""Diagnóstico SOLO LECTURA de «los pedidos web no aparecen» (incidencia del
filtro «solo processing» de #387). Prueba el parseo de los mensajes y las
consultas de conteo (descartes por estado, pedidos distintos nunca creados,
creados-pero-ocultos, fechas de corte). No escribe nada de producción.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.erp.models import (
    IntegrationEvent,
    IntegrationEventStatus,
    Order,
    OrderSource,
)
from app.erp.woo_ingest_diag import (
    diagnose,
    distinct_dropped_orders,
    format_report,
    hidden_web_orders,
    parse_order_id,
    parse_skipped_status,
    scan_ignored_webhooks,
    store_cutoffs,
)
from app.models.crm import ExternalSystem, SyncLog
from app.models.integration_settings import IntegrationAccount

SINCE = datetime(2026, 9, 13, tzinfo=timezone.utc)
AFTER = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
BEFORE = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)


def _ignored_msg(status: str) -> str:
    return f"webhook order.updated: order ignored (status={status}, not processing)"


# --- parseo puro -------------------------------------------------------------


def test_parse_skipped_status() -> None:
    assert parse_skipped_status(_ignored_msg("on-hold")) == "on-hold"
    assert parse_skipped_status(_ignored_msg("pending")) == "pending"
    assert parse_skipped_status("webhook order.updated: order created") is None
    assert parse_skipped_status(None) is None


def test_parse_order_id() -> None:
    assert parse_order_id('{"id": 555, "status": "on-hold"}') == "555"
    assert parse_order_id('{"status": "on-hold"}') is None
    assert parse_order_id("not json") is None
    assert parse_order_id(None) is None


# --- BD ----------------------------------------------------------------------


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    Base.metadata.drop_all(engine)


def _store(s: Session, slug: str, *, cutoff: str | None = None) -> IntegrationAccount:
    meta = f'{{"external_cutoff_date": "{cutoff}"}}' if cutoff else None
    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug,
        display_name=slug.title(), metadata_json=meta,
    )
    s.add(a)
    s.flush()
    return a


def _synclog(s: Session, slug: str, message: str, at: datetime) -> None:
    s.add(SyncLog(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug,
        operation="webhook_process", status="success",
        started_at=at, message=message,
    ))


def _event(s: Session, slug: str, woo_id: int, at: datetime,
           topic: str = "order.updated") -> None:
    s.add(IntegrationEvent(
        system="woocommerce", account_id=slug, external_event_id=f"{slug}-{woo_id}-{topic}",
        event_type=topic, payload_json=f'{{"id": {woo_id}}}',
        status=IntegrationEventStatus.PROCESSED, created_at=at,
    ))


def _web_order(s: Session, oid: str, woo_id: str, store: IntegrationAccount, *,
               at: datetime, externalized: datetime | None = None,
               excluded: datetime | None = None) -> None:
    s.add(Order(
        id=oid, order_number=f"WEB-{woo_id}", external_source=OrderSource.WOOCOMMERCE,
        external_id=woo_id, store_id=store.id, total_amount=10.0, currency="EUR",
        payment_status="paid", preparation_status="pending_review",
        created_at=at, externally_processed_at=externalized,
        seguimiento_excluded_at=excluded,
    ))


def test_scan_ignored_webhooks_por_estado_y_ventana(session: Session) -> None:
    _synclog(session, "boprint", _ignored_msg("on-hold"), AFTER)
    _synclog(session, "boprint", _ignored_msg("on-hold"), AFTER)
    _synclog(session, "artisjet", _ignored_msg("completed"), AFTER)
    _synclog(session, "boprint", "webhook order.created: order created", AFTER)  # no descarte
    _synclog(session, "boprint", _ignored_msg("pending"), BEFORE)  # fuera de ventana
    session.commit()
    total, by_status, by_store_status = scan_ignored_webhooks(session, SINCE)
    assert total == 3
    assert by_status == {"on-hold": 2, "completed": 1}
    assert by_store_status["boprint"] == {"on-hold": 2}
    assert by_store_status["artisjet"] == {"completed": 1}


def test_distinct_dropped_orders_cuenta_pedidos_no_eventos(session: Session) -> None:
    store = _store(session, "boprint")
    # El pedido 555 generó 2 eventos y SÍ existe → no cuenta.
    _event(session, "boprint", 555, AFTER, topic="order.created")
    _event(session, "boprint", 555, AFTER, topic="order.updated")
    _web_order(session, "o555", "555", store, at=AFTER)
    # El pedido 556 llegó (2 eventos) y NO existe → 1 pedido afectado.
    _event(session, "boprint", 556, AFTER, topic="order.created")
    _event(session, "boprint", 556, AFTER, topic="order.updated")
    session.commit()
    count, sample = distinct_dropped_orders(session, SINCE)
    assert count == 1
    assert sample == ["boprint#556"]


def test_hidden_web_orders(session: Session) -> None:
    store = _store(session, "boprint")
    _web_order(session, "o1", "1", store, at=AFTER)                       # visible
    _web_order(session, "o2", "2", store, at=AFTER, externalized=AFTER)   # oculto (corte)
    _web_order(session, "o3", "3", store, at=AFTER, excluded=AFTER)       # oculto (excluido)
    _web_order(session, "o0", "0", store, at=BEFORE)                      # fuera de ventana
    session.commit()
    created, externalized, excluded = hidden_web_orders(session, SINCE)
    assert created == 3
    assert externalized == 1
    assert excluded == 1


def test_store_cutoffs(session: Session) -> None:
    _store(session, "boprint", cutoff="2026-01-01")
    _store(session, "artisjet")
    session.commit()
    cutoffs = {c["store"]: c["external_cutoff_date"] for c in store_cutoffs(session)}
    assert cutoffs == {"boprint": "2026-01-01", "artisjet": None}


def test_diagnose_and_report(session: Session) -> None:
    store = _store(session, "boprint", cutoff="2026-01-01")
    _synclog(session, "boprint", _ignored_msg("on-hold"), AFTER)
    _event(session, "boprint", 556, AFTER)
    _web_order(session, "o1", "1", store, at=AFTER, externalized=AFTER)
    session.commit()
    diag = diagnose(session, since=SINCE)
    assert diag.ignored_events_total == 1
    assert diag.ignored_by_status == {"on-hold": 1}
    assert diag.distinct_dropped_orders == 1
    assert diag.hidden_externalized == 1
    text = format_report(diag)
    assert "status=on-hold: 1" in text
    assert "SOLO LECTURA" in text
