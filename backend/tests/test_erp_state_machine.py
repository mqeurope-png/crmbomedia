"""BoHub ERP Fase A PR 2 — máquina de estados declarativa + engine.

Cobertura adversarial (patrón PR #263 workflows): cada guard tiene un test
que VIOLA su precondición y espera TransitionError — si alguien borra el
guard de definitions/engine, la suite falla. Ídem para la matriz de roles
y las evidencias requeridas.
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra todos los modelos
from app.db.base import Base
from app.erp.models import (
    Order,
    OrderLine,
    OrderStatusHistory,
    StatusDomain,
)
from app.erp.state_machine import (
    TransitionError,
    apply_transition,
    available_transitions,
)
from app.models.crm import AuditLog, User, UserRole
from tests._test_helpers import seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


def _user(s: Session, role: UserRole) -> User:
    return s.scalar(select(User).where(User.role == role))


def _mk_order(s: Session, *, mapped: bool = True, **over) -> Order:
    order = Order(order_number=over.pop("order_number", "MAN-0001"), **over)
    s.add(order)
    s.flush()
    s.add(OrderLine(
        order_id=order.id, product_sku="SKU-MBO-3050",
        product_codart="MBO3050" if mapped else None,
        description="MBO 3050", quantity=1, unit_price=100, line_total=100,
    ))
    s.flush()
    s.refresh(order)
    return order


def _t(s, order, domain, to, actor, **kw):
    return apply_transition(
        s, order=order, domain=domain, to_status=to, actor=actor, **kw
    )


def _add_package(s, order) -> None:
    """Fase D: embalar exige ≥1 bulto medido — se añade antes de packed."""
    from app.erp.models import ShipmentPackage  # noqa: PLC0415

    s.add(ShipmentPackage(order_id=order.id, position=1, weight_kg=2,
                          height_cm=10, width_cm=10, depth_cm=10))
    s.flush()


# --- flujo feliz + historial -------------------------------------------------


def test_full_happy_path_walks_all_four_domains(session_factory):
    with session_factory() as s:
        admin = _user(s, UserRole.ADMIN)
        sat = _user(s, UserRole.SAT)
        pedidos = _user(s, UserRole.PEDIDOS)
        order = _mk_order(s)

        _t(s, order, StatusDomain.PAYMENT, "paid", None)  # webhook (system)
        _t(s, order, StatusDomain.PREPARATION, "in_queue", pedidos)  # aprobar
        _t(s, order, StatusDomain.PREPARATION, "preparing", sat)
        _add_package(s, order)
        _t(s, order, StatusDomain.PREPARATION, "packed", sat)
        _t(s, order, StatusDomain.TRANSPORT, "label_created", pedidos)
        _t(s, order, StatusDomain.TRANSPORT, "in_transit", pedidos,
           evidence={"tracking_number": "GN-88123"})
        _t(s, order, StatusDomain.TRANSPORT, "delivered", None)
        _t(s, order, StatusDomain.INVOICE, "pending", pedidos)
        _t(s, order, StatusDomain.INVOICE, "generated", None)
        s.commit()

        assert order.payment_status == "paid"
        assert order.preparation_status == "packed"
        assert order.transport_status == "delivered"
        assert order.invoice_status == "generated"
        assert order.tracking_number == "GN-88123"
        n_hist = s.scalar(select(func.count(OrderStatusHistory.id)))
        assert n_hist == 9  # una fila por transición
        _ = admin


def test_invalid_transition_rejected_when_arc_not_defined(session_factory):
    with session_factory() as s:
        admin = _user(s, UserRole.ADMIN)
        order = _mk_order(s)
        # pending_review → packed directamente NO es un arco.
        with pytest.raises(TransitionError) as exc:
            _t(s, order, StatusDomain.PREPARATION, "packed", admin)
        assert exc.value.code == "invalid_transition"


# --- guards (mutación adversarial: violar la precondición DEBE fallar) ------


def test_guard_preparation_requires_payment_ok(session_factory):
    with session_factory() as s:
        sat = _user(s, UserRole.SAT)
        pedidos = _user(s, UserRole.PEDIDOS)
        order = _mk_order(s)  # payment aún pending
        _t(s, order, StatusDomain.PREPARATION, "in_queue", pedidos)
        with pytest.raises(TransitionError) as exc:
            _t(s, order, StatusDomain.PREPARATION, "preparing", sat)
        assert exc.value.code == "guard_failed"
        assert "payment_ok_for_preparation" in str(exc.value)
        # credit_approved TAMBIÉN desbloquea (B2B).
        admin = _user(s, UserRole.ADMIN)
        _t(s, order, StatusDomain.PAYMENT, "credit_approved", admin)
        _t(s, order, StatusDomain.PREPARATION, "preparing", sat)
        assert order.preparation_status == "preparing"


def test_guard_label_requires_packed(session_factory):
    with session_factory() as s:
        admin = _user(s, UserRole.ADMIN)
        order = _mk_order(s)
        _t(s, order, StatusDomain.PAYMENT, "paid", None)
        # Sin embalar: crear envío debe fallar.
        with pytest.raises(TransitionError) as exc:
            _t(s, order, StatusDomain.TRANSPORT, "label_created", admin)
        assert exc.value.code == "guard_failed"
        assert "packed_before_label" in str(exc.value)


def test_guard_in_transit_requires_tracking_number(session_factory):
    with session_factory() as s:
        admin = _user(s, UserRole.ADMIN)
        sat = _user(s, UserRole.SAT)
        pedidos = _user(s, UserRole.PEDIDOS)
        order = _mk_order(s)
        _t(s, order, StatusDomain.PAYMENT, "paid", None)
        _t(s, order, StatusDomain.PREPARATION, "in_queue", pedidos)
        _t(s, order, StatusDomain.PREPARATION, "preparing", sat)
        _add_package(s, order)
        _t(s, order, StatusDomain.PREPARATION, "packed", sat)
        _t(s, order, StatusDomain.TRANSPORT, "label_created", admin)
        with pytest.raises(TransitionError) as exc:
            _t(s, order, StatusDomain.TRANSPORT, "in_transit", admin)
        assert exc.value.code == "guard_failed"
        assert "tracking_required" in str(exc.value)
        # Con evidencia → pasa y persiste el tracking en el pedido.
        _t(s, order, StatusDomain.TRANSPORT, "in_transit", admin,
           evidence={"tracking_number": "DSV-5512"})
        assert order.tracking_number == "DSV-5512"


def test_guard_invoice_requires_paid_and_all_codart_mapped(session_factory):
    with session_factory() as s:
        admin = _user(s, UserRole.ADMIN)
        # Caso 1: línea SIN mapear → falla aunque esté pagado.
        order = _mk_order(s, mapped=False)
        _t(s, order, StatusDomain.PAYMENT, "paid", None)
        _t(s, order, StatusDomain.INVOICE, "pending", admin)
        with pytest.raises(TransitionError) as exc:
            _t(s, order, StatusDomain.INVOICE, "generated", admin)
        assert exc.value.code == "guard_failed"
        assert "SKU-MBO-3050" in str(exc.value)
        # Caso 2: mapeado pero pago NO 'paid' (credit_approved) → falla.
        order2 = _mk_order(s, order_number="MAN-0002")
        _t(s, order2, StatusDomain.PAYMENT, "credit_approved", admin)
        _t(s, order2, StatusDomain.INVOICE, "pending", admin)
        with pytest.raises(TransitionError) as exc2:
            _t(s, order2, StatusDomain.INVOICE, "generated", admin)
        assert exc2.value.code == "guard_failed"


# --- matriz de roles ---------------------------------------------------------


def test_role_matrix_enforced(session_factory):
    with session_factory() as s:
        sat = _user(s, UserRole.SAT)
        user = _user(s, UserRole.USER)
        pedidos = _user(s, UserRole.PEDIDOS)
        order = _mk_order(s)
        # SAT no aprueba pedidos (solo admin/pedidos).
        with pytest.raises(TransitionError) as e1:
            _t(s, order, StatusDomain.PREPARATION, "in_queue", sat)
        assert e1.value.code == "role_forbidden"
        # USER (comercial) no dispara ninguna transición de preparación.
        with pytest.raises(TransitionError) as e2:
            _t(s, order, StatusDomain.PREPARATION, "in_queue", user)
        assert e2.value.code == "role_forbidden"
        # PEDIDOS sí aprueba; pero NO marca packed (eso es de SAT/admin).
        _t(s, order, StatusDomain.PAYMENT, "paid", None)
        _t(s, order, StatusDomain.PREPARATION, "in_queue", pedidos)
        _t(s, order, StatusDomain.PREPARATION, "preparing",
           _user(s, UserRole.SAT))
        with pytest.raises(TransitionError) as e3:
            _t(s, order, StatusDomain.PREPARATION, "packed", pedidos)
        assert e3.value.code == "role_forbidden"


def test_refund_is_admin_only_and_requires_reason(session_factory):
    with session_factory() as s:
        admin = _user(s, UserRole.ADMIN)
        manager = _user(s, UserRole.MANAGER)
        order = _mk_order(s)
        _t(s, order, StatusDomain.PAYMENT, "paid", None)
        with pytest.raises(TransitionError) as e1:
            _t(s, order, StatusDomain.PAYMENT, "refunded", manager,
               evidence={"reason": "x"})
        assert e1.value.code == "role_forbidden"
        # Admin sin motivo → evidencia obligatoria.
        with pytest.raises(TransitionError) as e2:
            _t(s, order, StatusDomain.PAYMENT, "refunded", admin)
        assert e2.value.code == "evidence_missing"
        _t(s, order, StatusDomain.PAYMENT, "refunded", admin,
           evidence={"reason": "cliente cancela"})
        assert order.payment_status == "refunded"


def test_system_actor_cannot_fire_office_only_arcs(session_factory):
    with session_factory() as s:
        order = _mk_order(s)
        # credit_approved es admin/manager — el sistema no puede.
        with pytest.raises(TransitionError) as exc:
            _t(s, order, StatusDomain.PAYMENT, "credit_approved", None)
        assert exc.value.code == "role_forbidden"


# --- historial + audit + helpers --------------------------------------------


def test_history_row_records_evidence_metadata_and_actor(session_factory):
    with session_factory() as s:
        sat = _user(s, UserRole.SAT)
        pedidos = _user(s, UserRole.PEDIDOS)
        order = _mk_order(s)
        _t(s, order, StatusDomain.PAYMENT, "paid", None)
        _t(s, order, StatusDomain.PREPARATION, "in_queue", pedidos)
        _t(s, order, StatusDomain.PREPARATION, "preparing", sat)
        _t(s, order, StatusDomain.PREPARATION, "blocked", sat,
           reason="falta cable", evidence={"reason": "falta cable alimentación"})
        s.commit()
        h = s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.to_status == "blocked")).one()
        assert h.changed_by_user_id == sat.id
        assert h.from_status == "preparing"
        assert json.loads(h.metadata_json)["reason"] == "falta cable alimentación"


def test_transition_writes_audit_log(session_factory):
    with session_factory() as s:
        pedidos = _user(s, UserRole.PEDIDOS)
        order = _mk_order(s)
        _t(s, order, StatusDomain.PREPARATION, "in_queue", pedidos)
        s.commit()
        audits = [
            a for a in s.scalars(select(AuditLog).where(
                AuditLog.action == "erp.order_status_changed"))
        ]
        assert len(audits) == 1
        meta = json.loads(audits[0].metadata_json or "{}")
        assert meta["domain"] == "preparation"
        assert (meta["from"], meta["to"]) == ("pending_review", "in_queue")


def test_available_transitions_filters_by_role_and_state(session_factory):
    with session_factory() as s:
        sat = _user(s, UserRole.SAT)
        pedidos = _user(s, UserRole.PEDIDOS)
        order = _mk_order(s)  # preparation = pending_review
        # PEDIDOS ve «Aprobar pedido»; SAT no ve nada desde pending_review.
        assert [t.to_status for t in available_transitions(
            order, StatusDomain.PREPARATION, pedidos)] == ["in_queue"]
        assert available_transitions(order, StatusDomain.PREPARATION, sat) == []
