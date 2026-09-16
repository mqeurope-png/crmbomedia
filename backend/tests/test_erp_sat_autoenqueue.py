"""BoHub ERP · Lote 4 — AUTO-ENTRADA en la Cola SAT al pagar (sin aprobación).

Todo pedido PAGADO entra solo en la Cola SAT («Por embalar») en cuanto se
cobra, sin pasar por la aprobación de la Cola PEDIDOS:

  - un pedido web reconciliado a pagado (vía el mapper) queda `in_queue`, con
    su fila de historial PREPARATION + auditoría `erp.sat_auto_enqueued`, SIN
    aprobación, y aparece en `GET /api/erp/sat/queue` (`preparing`);
  - un pedido manual marcado pagado (vía `record_payment_intent`) entra igual;
  - guards: anulado / completado / quitado a mano / no pagado NO entran; un
    pedido ya en cola / preparándose / embalado es idempotente (sin duplicar);
  - la aprobación sigue aparte (no se estampa `approved_at`).
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.crypto import encrypt
from app.db.base import Base
from app.db.session import get_session
from app.erp.factusol_albaran import record_payment_intent
from app.erp.models import (
    Order,
    OrderStatusHistory,
    PaymentStatus,
    PreparationStatus,
    StatusDomain,
)
from app.erp.sat_autoenqueue import AUDIT_ACTION, AUTO_ENQUEUE_REASON, enqueue_paid_order
from app.integrations.woocommerce.mapper import import_woo_order
from app.main import app
from app.models.crm import AuditLog, ExternalSystem, User
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import auth_headers, seed_test_users


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
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --- helpers -----------------------------------------------------------------


def _mk_store(s: Session, slug="boprint") -> IntegrationAccount:
    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug,
        display_name=f"Woo {slug}", enabled=True,
        mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=f"https://{slug}.example",
        consumer_key_encrypted=encrypt("ck_test"),
        consumer_secret_encrypted=encrypt("cs_test"),
        credential_status="configured",
    )
    s.add(a)
    s.commit()
    return a


def _woo(**over) -> dict:
    base = {
        "id": 100, "number": "1001", "status": "processing",
        "total": "129.00", "currency": "EUR",
        "date_created": "2026-08-01T08:00:00Z",
        "date_paid": "2026-08-01T08:01:00Z",
        "billing": {"first_name": "Laura", "last_name": "Pérez",
                    "email": "laura@ejemplo.com", "country": "ES"},
        "line_items": [{"id": 1, "product_id": 42, "sku": "SKU-A",
                        "quantity": 1, "total": "129.00", "name": "Art A"}],
        "meta_data": [],
    }
    base.update(over)
    return base


def _import(s: Session, store, **over) -> Order:
    payload = _woo(**over)
    payload["_store_slug"] = store.account_id
    out = import_woo_order(s, store=store, woo_order=payload)
    s.commit()
    return s.get(Order, out.order_id)


def _mk_manual(s: Session, *, number="MAN-0001",
               prep=PreparationStatus.PENDING_REVIEW,
               payment=PaymentStatus.PENDING) -> Order:
    o = Order(order_number=number, preparation_status=prep, payment_status=payment)
    s.add(o)
    s.flush()
    return o


def _prep_history(s: Session, order_id: str) -> list[OrderStatusHistory]:
    return list(s.scalars(
        select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == order_id,
            OrderStatusHistory.domain == StatusDomain.PREPARATION,
        )
    ))


def _auto_audits(s: Session, order_id: str) -> list[AuditLog]:
    return list(s.scalars(
        select(AuditLog).where(
            AuditLog.action == AUDIT_ACTION, AuditLog.target_id == order_id,
        )
    ))


_PAID_RESOLVED = {
    "paid": True, "forma_pago": "002", "forma_pago_nombre": "Transferencia",
    "contrapartida": "8", "contrapartida_nombre": "Banco Sabadell",
    "fecha": "2026-09-16",
}


# --- 1) web reconciliado a pagado entra en la Cola SAT -----------------------


def test_web_order_reconciled_to_paid_enters_queue(client, session_factory):
    """Un pedido web que llega SIN pago (pending_review) y luego se reconcilia a
    pagado entra solo en la Cola SAT: in_queue + historial PREPARATION +
    auditoría `erp.sat_auto_enqueued`, SIN aprobación, y sale en la cola."""
    with session_factory() as s:
        store = _mk_store(s)
        # 1er webhook: sin date_paid → pending, se queda en la pre-cola.
        order = _import(s, store, date_paid=None)
        assert order.payment_status == PaymentStatus.PENDING
        assert order.preparation_status == PreparationStatus.PENDING_REVIEW
        oid = order.id

        # 2º webhook (order.updated) con date_paid → paid → entra en la cola.
        _import(s, store, date_paid="2026-08-01T09:00:00Z")

    with session_factory() as s:
        order = s.get(Order, oid)
        assert order.payment_status == PaymentStatus.PAID
        assert order.preparation_status == PreparationStatus.IN_QUEUE
        # No se aprobó: la aprobación sigue siendo un paso aparte.
        assert order.approved_at is None
        assert order.approved_by_user_id is None
        # Historial: fila PREPARATION pending_review → in_queue con el motivo.
        rows = _prep_history(s, oid)
        assert len(rows) == 1
        assert (rows[0].from_status, rows[0].to_status) == ("pending_review", "in_queue")
        assert rows[0].reason == AUTO_ENQUEUE_REASON
        assert rows[0].changed_by_user_id is None  # sistema
        # Auditoría propia de la entrada automática.
        assert len(_auto_audits(s, oid)) == 1

    # Aparece en la cola SAT «por embalar».
    body = client.get("/api/erp/sat/queue",
                      headers=auth_headers(client, "sat")).json()
    assert oid in [i["id"] for i in body["preparing"]]


def test_web_order_created_already_paid_enters_queue(session_factory):
    """Un pedido web creado YA pagado (date_paid en el primer webhook) entra
    directo en la Cola SAT en la propia creación."""
    with session_factory() as s:
        store = _mk_store(s)
        order = _import(s, store)  # _woo() trae date_paid → paid
        assert order.payment_status == PaymentStatus.PAID
        assert order.preparation_status == PreparationStatus.IN_QUEUE
        assert order.approved_at is None
        assert len(_auto_audits(s, order.id)) == 1


# --- 2) manual marcado pagado entra igual ------------------------------------


def test_manual_order_marked_paid_enters_queue(session_factory):
    """`record_payment_intent` (alta manual con cobro / conversión FACTUSOL):
    al confirmar el pago el pedido entra en la Cola SAT, atribuido al usuario."""
    with session_factory() as s:
        actor = s.scalar(select(User).where(User.email == "pedidos@example.com"))
        order = _mk_manual(s)
        record_payment_intent(s, order, dict(_PAID_RESOLVED), actor_user_id=actor.id)
        s.commit()

        assert order.payment_status == PaymentStatus.PAID
        assert order.preparation_status == PreparationStatus.IN_QUEUE
        assert order.approved_at is None
        rows = _prep_history(s, order.id)
        assert len(rows) == 1
        assert rows[0].reason == AUTO_ENQUEUE_REASON
        assert rows[0].changed_by_user_id == actor.id  # atribuido al que cobró
        assert len(_auto_audits(s, order.id)) == 1


# --- 3) guards ---------------------------------------------------------------


def test_guard_unpaid_order_does_not_enter(session_factory):
    with session_factory() as s:
        order = _mk_manual(s, payment=PaymentStatus.PENDING)
        assert enqueue_paid_order(s, order) is False
        assert order.preparation_status == PreparationStatus.PENDING_REVIEW
        assert _prep_history(s, order.id) == []


@pytest.mark.parametrize("field", ["cancelled_at", "completed_at",
                                   "seguimiento_excluded_at"])
def test_guard_cancelled_completed_excluded_do_not_enter(session_factory, field):
    with session_factory() as s:
        order = _mk_manual(s, payment=PaymentStatus.PAID)
        setattr(order, field, datetime.now(UTC))
        s.flush()
        assert enqueue_paid_order(s, order) is False
        assert order.preparation_status == PreparationStatus.PENDING_REVIEW
        assert _prep_history(s, order.id) == []


def test_guard_credit_and_partial_paid_do_enter(session_factory):
    """PAYMENT_OK_FOR_PREPARATION incluye credit_approved y partial_paid."""
    with session_factory() as s:
        for i, pay in enumerate(
            (PaymentStatus.CREDIT_APPROVED, PaymentStatus.PARTIAL_PAID)
        ):
            order = _mk_manual(s, number=f"MAN-CP-{i}", payment=pay)
            assert enqueue_paid_order(s, order) is True
            assert order.preparation_status == PreparationStatus.IN_QUEUE


@pytest.mark.parametrize("prep", [
    PreparationStatus.IN_QUEUE, PreparationStatus.PREPARING,
    PreparationStatus.PACKED, PreparationStatus.BLOCKED,
])
def test_idempotent_never_touches_states_past_pre_queue(session_factory, prep):
    """Un pedido pagado que ya salió de la pre-cola no se toca (idempotente):
    ni cambia de estado ni escribe historial/auditoría."""
    with session_factory() as s:
        order = _mk_manual(s, payment=PaymentStatus.PAID, prep=prep)
        assert enqueue_paid_order(s, order) is False
        assert order.preparation_status == prep
        assert _prep_history(s, order.id) == []
        assert _auto_audits(s, order.id) == []


def test_idempotent_no_duplicate_history_on_second_call(session_factory):
    with session_factory() as s:
        order = _mk_manual(s, payment=PaymentStatus.PAID)
        assert enqueue_paid_order(s, order) is True
        # Segunda llamada: ya en cola → no-op, sin duplicar historial/auditoría.
        assert enqueue_paid_order(s, order) is False
        s.commit()  # la auditoría la persiste el commit del caller (autoflush off)
        assert order.preparation_status == PreparationStatus.IN_QUEUE
        assert len(_prep_history(s, order.id)) == 1
        assert len(_auto_audits(s, order.id)) == 1


# --- 4) las salidas siguen funcionando (no se re-encola tras salir) ----------


def test_exit_from_queue_not_re_enqueued(client, session_factory):
    """Al marcar embalado (salida del taller) el pedido sale de «por embalar»;
    un intento posterior de auto-entrada NO lo devuelve a la cola (guard)."""
    with session_factory() as s:
        order = _mk_manual(s, payment=PaymentStatus.PAID)
        assert enqueue_paid_order(s, order) is True
        # Salida: el pedido avanza a packed (como haría «marcar procesado»).
        order.preparation_status = PreparationStatus.PACKED
        s.flush()
        # Reintento de auto-entrada: guard `pending_review` lo impide.
        assert enqueue_paid_order(s, order) is False
        assert order.preparation_status == PreparationStatus.PACKED
        oid = order.id
        s.commit()

    # Ya no está en «por embalar» (packed va a «listos para envío»).
    body = client.get("/api/erp/sat/queue",
                      headers=auth_headers(client, "sat")).json()
    assert oid not in [i["id"] for i in body["preparing"]]
