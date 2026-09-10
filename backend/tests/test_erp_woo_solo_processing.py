"""ERP · WooCommerce — SOLO se crean pedidos en estado `processing`.

Cubre: la regla en el punto común de ingesta (`import_woo_order`), que un pedido
ya creado sigue actualizando `woo_status` (processing → completed / cancelled),
que el filtro aplica tanto al webhook como al backfill/sync, y la limpieza
reversible de los ya importados con la regla antigua (`cleanup.py`).
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.integrations.woocommerce.jobs as woo_jobs
import app.main  # noqa: F401
from app.core.crypto import encrypt
from app.db.base import Base
from app.erp.models import (
    ErpException,
    ExceptionType,
    IntegrationEvent,
    IntegrationEventStatus,
    InvoiceStatus,
    Order,
    OrderSource,
    PaymentStatus,
    PreparationStatus,
    ShipmentPackage,
)
from app.integrations.woocommerce.cleanup import (
    EXCLUSION_REASON_PREFIX,
    KEEP_STATUSES,
    cleanup_non_processing_orders,
)
from app.integrations.woocommerce.mapper import (
    CREATE_ON_STATUSES,
    import_woo_order,
    should_create_order,
)
from app.models.crm import Company, Contact, ExternalSystem, SyncLog
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    Base.metadata.drop_all(engine)


def _store(s: Session, slug: str = "boprint") -> IntegrationAccount:
    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=slug,
        enabled=True, mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=f"https://{slug}.example",
        consumer_key_encrypted=encrypt("ck"), consumer_secret_encrypted=encrypt("cs"),
        credential_status="configured",
    )
    s.add(a)
    s.commit()
    return a


def _woo_order(**over) -> dict:
    base = {
        "id": 555, "number": "555", "status": "processing",
        "total": "80.00", "currency": "EUR",
        "date_created": "2026-09-03T10:00:00Z", "date_paid": "2026-09-03T10:01:00Z",
        "billing": {"first_name": "Ana", "last_name": "Ruiz",
                    "email": "ana@ejemplo.com", "company": "Ana SL", "vat": ""},
        "line_items": [{"id": 1, "product_id": 9, "sku": "SKU-9",
                        "quantity": 1, "total": "80.00", "name": "Producto 9"}],
        "meta_data": [],
        "_store_slug": "boprint",
    }
    base.update(over)
    return base


def _count(s: Session, model) -> int:
    return int(s.scalar(select(func.count(model.id))) or 0)


def _orders(s: Session) -> list[Order]:
    return list(s.scalars(select(Order)))


# --- Parte 2: regla «solo processing» en el punto común -----------------------


def test_regla_solo_processing_constante():
    assert CREATE_ON_STATUSES == frozenset({"processing"})
    assert should_create_order({"status": "processing"})
    assert should_create_order({"status": "Processing"})  # insensible a mayúsculas
    for st in ("pending", "on-hold", "completed", "cancelled", "refunded",
               "failed", "checkout-draft", "", None):
        assert not should_create_order({"status": st}), st
    assert not should_create_order({})


@pytest.mark.parametrize(
    "status", ["pending", "on-hold", "completed", "cancelled", "refunded", "failed"],
)
def test_woo_crea_solo_processing(session_factory, status):
    """Un pedido DESCONOCIDO solo se crea si llega en `processing`; en cualquier
    otro estado se ignora sin crear NADA (ni pedido, ni contacto, ni empresa)."""
    with session_factory() as s:
        store = _store(s)
        contacts0, companies0 = _count(s, Contact), _count(s, Company)

        out = import_woo_order(s, store=store, woo_order=_woo_order(id=700, status=status))
        s.commit()
        assert out.created is False and out.order_id is None
        assert out.skipped_status == status
        assert _orders(s) == []
        assert _count(s, Contact) == contacts0
        assert _count(s, Company) == companies0

        # El mismo pedido, ya en processing → ahora sí se crea.
        out2 = import_woo_order(s, store=store, woo_order=_woo_order(id=700, status="processing"))
        s.commit()
        assert out2.created is True and out2.skipped_status is None
        orders = _orders(s)
        assert len(orders) == 1 and orders[0].external_id == "700"
        assert orders[0].woo_status == "processing"


def test_woo_processing_luego_completed(session_factory):
    """Un pedido creado en processing SIGUE actualizando `woo_status` en cada
    cambio (processing → completed → cancelled). El filtro solo gobierna el
    ALTA, no las actualizaciones (#376 se mantiene)."""
    with session_factory() as s:
        store = _store(s)
        out = import_woo_order(s, store=store, woo_order=_woo_order(id=800))
        s.commit()
        assert out.created is True
        order_id = out.order_id

        out2 = import_woo_order(s, store=store, woo_order=_woo_order(id=800, status="completed"))
        s.commit()
        assert out2.created is False and out2.skipped_status is None
        assert out2.order_id == order_id
        orders = _orders(s)
        assert len(orders) == 1 and orders[0].woo_status == "completed"

        out3 = import_woo_order(s, store=store, woo_order=_woo_order(id=800, status="cancelled"))
        s.commit()
        assert out3.created is False and out3.skipped_status is None
        orders = _orders(s)
        assert len(orders) == 1 and orders[0].woo_status == "cancelled"


# --- Parte 2: el filtro aplica al webhook Y al sync/backfill ------------------


def _seed_event(session_factory, *, topic: str, delivery: str, payload: dict,
                account_id: str = "boprint") -> str:
    with session_factory() as s:
        ev = IntegrationEvent(
            system="woocommerce", account_id=account_id,
            external_event_id=delivery, event_type=topic,
            payload_json=json.dumps(payload),
        )
        s.add(ev)
        s.commit()
        return ev.id


def test_woo_filtro_en_webhook_y_sync(session_factory):
    with session_factory() as s:
        _store(s)

    # -- Webhook: order.created de un pedido en `pending` → se ignora.
    ev_pending = _seed_event(
        session_factory, topic="order.created", delivery="d-1", payload={"id": 555},
    )
    fake = MagicMock()
    fake.get_order.return_value = _woo_order(id=555, status="pending")
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory), \
         patch.object(woo_jobs, "WooHTTPClient", return_value=fake):
        res = woo_jobs.process_webhook_event(ev_pending)
    assert res["created"] is False and res["order_id"] is None
    assert res["skipped_status"] == "pending"
    with session_factory() as s:
        assert _orders(s) == []
        ev = s.get(IntegrationEvent, ev_pending)
        assert ev.status == IntegrationEventStatus.PROCESSED  # consumido, no reintenta
        log = s.scalars(select(SyncLog).order_by(SyncLog.started_at.desc())).first()
        assert log is not None and "ignored" in (log.message or "")
        assert "pending" in (log.message or "")

    # -- Webhook: order.updated del mismo pedido ya en `processing` → se crea.
    ev_proc = _seed_event(
        session_factory, topic="order.updated", delivery="d-2", payload={"id": 555},
    )
    fake.get_order.return_value = _woo_order(id=555, status="processing")
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory), \
         patch.object(woo_jobs, "WooHTTPClient", return_value=fake):
        res = woo_jobs.process_webhook_event(ev_proc)
    assert res["created"] is True and res["skipped_status"] is None
    with session_factory() as s:
        orders = _orders(s)
        assert len(orders) == 1 and orders[0].woo_status == "processing"

    # -- Sync/backfill: evento order.backfill con payload `on-hold` → se ignora.
    ev_bf = _seed_event(
        session_factory, topic="order.backfill", delivery="bf-1",
        payload=_woo_order(id=556, status="on-hold"),
    )
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory):
        res = woo_jobs.import_order_from_event(ev_bf)
    assert res["created"] is False and res["skipped_status"] == "on-hold"
    with session_factory() as s:
        assert len(_orders(s)) == 1  # solo el 555
        assert s.get(IntegrationEvent, ev_bf).status == IntegrationEventStatus.PROCESSED

    # -- Sync/backfill: el mismo 556 en `processing` → se crea.
    ev_bf2 = _seed_event(
        session_factory, topic="order.backfill", delivery="bf-2",
        payload=_woo_order(id=556, status="processing"),
    )
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory):
        res = woo_jobs.import_order_from_event(ev_bf2)
    assert res["created"] is True
    with session_factory() as s:
        assert sorted(o.external_id for o in _orders(s)) == ["555", "556"]


def test_backfill_lista_solo_processing_en_woo(session_factory):
    """El backfill ya pide a Woo solo `processing` (primera barrera); la segunda
    es el mapper. Comprobamos la primera con un cliente simulado."""
    with session_factory() as s:
        _store(s)
    fake = MagicMock()
    fake.list_orders.return_value = []
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory), \
         patch.object(woo_jobs, "WooHTTPClient", return_value=fake):
        woo_jobs.sync_orders_backfill("boprint")
    assert fake.list_orders.called
    for call in fake.list_orders.call_args_list:
        assert call.kwargs.get("status") == "processing", call


# --- Parte 3: limpieza reversible de los ya importados ------------------------


def _order(
    s: Session, *, woo_id: str, woo_status: str | None, store: IntegrationAccount,
    cliente: str = "Cliente SL", invoiced: bool = False, paid: bool = False,
    preparation: PreparationStatus | None = None, total: str = "10.00",
) -> Order:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    o = Order(
        external_source=OrderSource.WOOCOMMERCE, external_id=woo_id,
        store_id=store.id, order_number=f"{store.account_id}-{woo_id}",
        company_id=comp.id, woo_status=woo_status,
        total_amount=Decimal(total),
        placed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    if invoiced:
        o.invoice_status = InvoiceStatus.GENERATED
        o.factusol_invoice_number = f"5-{woo_id}"
    if paid:
        o.payment_status = PaymentStatus.PAID
    if preparation is not None:
        o.preparation_status = preparation
    s.add(o)
    s.flush()
    return o


def _seed_cleanup_scenario(s: Session, store: IntegrationAccount) -> dict[str, Order]:
    """10 pedidos: 2 candidatos, 5 protegidos, 2 en flujo normal, 1 sin estado."""
    o = {
        "pending": _order(s, woo_id="1", woo_status="pending", store=store),
        "onhold": _order(s, woo_id="2", woo_status="on-hold", store=store),
        "cancel_fact": _order(s, woo_id="3", woo_status="cancelled", store=store,
                              invoiced=True),
        "pending_paid": _order(s, woo_id="4", woo_status="pending", store=store,
                               paid=True),
        "onhold_bulto": _order(s, woo_id="5", woo_status="on-hold", store=store),
        "failed_sat": _order(s, woo_id="6", woo_status="failed", store=store),
        "processing": _order(s, woo_id="7", woo_status="processing", store=store),
        "completed": _order(s, woo_id="8", woo_status="completed", store=store),
        "sin_estado": _order(s, woo_id="9", woo_status=None, store=store),
        "pending_packed": _order(s, woo_id="10", woo_status="pending", store=store,
                                 preparation=PreparationStatus.PACKED),
    }
    s.add(ShipmentPackage(order_id=o["onhold_bulto"].id, weight_kg=1,
                          height_cm=10, width_cm=10, depth_cm=10))
    s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id=o["failed_sat"].id))
    s.commit()
    return o


def test_limpieza_dry_run_lista_candidatos_y_protegidos_sin_escribir(session_factory):
    assert KEEP_STATUSES == frozenset({"processing", "completed"})
    with session_factory() as s:
        store = _store(s)
        _seed_cleanup_scenario(s, store)

        res = cleanup_non_processing_orders(s, dry_run=True)
        assert res["ok"] and res["preview"] is True
        assert [c["order_number"] for c in res["candidates"]] == ["boprint-2", "boprint-1"]
        assert res["por_estado"] == {"on-hold": 1, "pending": 1}
        assert {p["order_number"]: p["motivos"] for p in res["protected"]} == {
            "boprint-3": ["facturado"],
            "boprint-4": ["cobrado/pagado"],
            "boprint-5": ["albarán/envío"],
            "boprint-6": ["excepción/tarea SAT"],
            "boprint-10": ["en preparación (packed)"],
        }
        assert res["protegidos_por_motivo"] == {
            "albarán/envío": 1, "cobrado/pagado": 1, "en preparación": 1,
            "excepción/tarea SAT": 1, "facturado": 1,
        }
        assert res["sin_estado"] == 1
        assert res["excluded"] == 0
        cand = res["candidates"][0]
        assert cand["cliente"] == "Cliente SL" and cand["importe"] == 10.0

        # dry-run NO escribe nada.
        s.expire_all()
        assert all(o.seguimiento_excluded_at is None for o in _orders(s))


def test_limpieza_apply_excluye_reversible_e_idempotente(session_factory):
    with session_factory() as s:
        store = _store(s)
        o = _seed_cleanup_scenario(s, store)

        res = cleanup_non_processing_orders(s, dry_run=False, actor_user_id=None)
        assert res["ok"] and res["preview"] is False
        assert res["excluded"] == 2
        assert res["por_estado"] == {"on-hold": 1, "pending": 1}

        s.expire_all()
        for key in ("pending", "onhold"):
            ex = s.get(Order, o[key].id)
            assert ex.seguimiento_excluded_at is not None
            assert ex.seguimiento_excluded_reason.startswith(EXCLUSION_REASON_PREFIX)
            assert ex.woo_status in ex.seguimiento_excluded_reason
            # Soft: el pedido sigue existiendo (reversible con «Reincluir»).
            assert ex.woo_status in ("pending", "on-hold")
        # NUNCA se toca lo facturado / cobrado / con documentos / SAT / en
        # preparación, ni el flujo normal, ni los sin estado.
        for key in ("cancel_fact", "pending_paid", "onhold_bulto", "failed_sat",
                    "processing", "completed", "sin_estado", "pending_packed"):
            assert s.get(Order, o[key].id).seguimiento_excluded_at is None, key
        assert len(_orders(s)) == 10  # no se borra nada

        # Idempotente: segunda pasada no encuentra candidatos ni toca nada.
        res2 = cleanup_non_processing_orders(s, dry_run=False)
        assert res2["excluded"] == 0 and res2["candidates"] == []
        assert res2["por_estado"] == {}
        assert len(res2["protected"]) == 5 and res2["sin_estado"] == 1

        # «Reincluir» (mismos campos que el seguimiento) lo devuelve a candidato.
        back = s.get(Order, o["pending"].id)
        back.seguimiento_excluded_at = None
        back.seguimiento_excluded_reason = None
        s.commit()
        res3 = cleanup_non_processing_orders(s, dry_run=True)
        assert [c["order_number"] for c in res3["candidates"]] == ["boprint-1"]


def test_limpieza_filtra_por_tienda(session_factory):
    with session_factory() as s:
        a = _store(s, "boprint")
        b = _store(s, "artisjet")
        _order(s, woo_id="1", woo_status="pending", store=a)
        _order(s, woo_id="1", woo_status="on-hold", store=b)
        s.commit()

        assert cleanup_non_processing_orders(s, store_account_id="nope")["ok"] is False

        res = cleanup_non_processing_orders(s, dry_run=False, store_account_id="artisjet")
        assert res["excluded"] == 1
        assert [c["order_number"] for c in res["candidates"]] == ["artisjet-1"]
        s.expire_all()
        by_num = {o.order_number: o for o in _orders(s)}
        assert by_num["artisjet-1"].seguimiento_excluded_at is not None
        assert by_num["boprint-1"].seguimiento_excluded_at is None

        # Sin filtro: solo queda el de boprint.
        res_all = cleanup_non_processing_orders(s, dry_run=True)
        assert [c["order_number"] for c in res_all["candidates"]] == ["boprint-1"]
