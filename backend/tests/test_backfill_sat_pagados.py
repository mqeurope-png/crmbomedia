"""BoHub ERP · Lote 5 — BACKFILL de pedidos YA pagados a la Cola SAT.

El script `scripts.backfill_sat_pagados` mete de una sola vez en la Cola SAT
(«Por embalar») los pedidos que ya estaban pagados pero se quedaron en la
pre-cola («pending_review»), reutilizando los MISMOS guards que la auto-entrada
del Lote 4 (`enqueue_paid_order`). Aquí se comprueba:

  - el DRY-RUN cuenta lo correcto y NO escribe nada;
  - `--apply --yes` promueve solo los elegibles (paid/partial_paid + pending_review,
    no anulado/completado/excluido), deja huella (historial PREPARATION +
    auditoría `erp.sat_auto_enqueued`) y no toca al resto;
  - una segunda pasada `--apply --yes` promueve 0 (idempotente).
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra todos los modelos en el MetaData
from app.db.base import Base
from app.erp.models import (
    Order,
    OrderStatusHistory,
    PaymentStatus,
    PreparationStatus,
    StatusDomain,
)
from app.erp.sat_autoenqueue import AUDIT_ACTION
from app.models.crm import AuditLog
from scripts.backfill_sat_pagados import main, select_candidates


@pytest.fixture()
def session_factory(monkeypatch) -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    # El script abre su sesión con `Session(get_engine())`; lo apuntamos al
    # engine de test (import diferido dentro de main → basta parchear el módulo).
    monkeypatch.setattr("app.db.session.get_engine", lambda: engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    Base.metadata.drop_all(engine)


def _mk(
    s: Session,
    number: str,
    *,
    payment: PaymentStatus,
    prep: PreparationStatus = PreparationStatus.PENDING_REVIEW,
    cancelled: bool = False,
    completed: bool = False,
    excluded: bool = False,
) -> Order:
    now = datetime.now(UTC)
    order = Order(
        order_number=number, payment_status=payment, preparation_status=prep,
        placed_at=now,
        cancelled_at=now if cancelled else None,
        completed_at=now if completed else None,
        seguimiento_excluded_at=now if excluded else None,
    )
    s.add(order)
    s.flush()
    return order


def _seed(s: Session) -> dict[str, str]:
    """Devuelve {etiqueta: order_id}. Dos elegibles, el resto se salta."""
    ids = {
        "paid_pending": _mk(s, "PAID-PENDING",
                            payment=PaymentStatus.PAID).id,                    # promueve
        "partial_pending": _mk(s, "PARTIAL-PENDING",
                               payment=PaymentStatus.PARTIAL_PAID).id,          # promueve
        "paid_cancelled": _mk(s, "PAID-CANCELLED",
                              payment=PaymentStatus.PAID, cancelled=True).id,   # skip
        "paid_completed": _mk(s, "PAID-COMPLETED",
                              payment=PaymentStatus.PAID, completed=True).id,   # skip
        "paid_excluded": _mk(s, "PAID-EXCLUDED",
                             payment=PaymentStatus.PAID, excluded=True).id,     # skip
        "unpaid_pending": _mk(s, "UNPAID-PENDING",
                              payment=PaymentStatus.PENDING).id,                # skip
        "paid_in_queue": _mk(s, "PAID-INQUEUE", payment=PaymentStatus.PAID,
                             prep=PreparationStatus.IN_QUEUE).id,               # skip (idem)
    }
    s.commit()
    return ids


_PROMOTED = {"paid_pending", "partial_pending"}


def _prep_history(s: Session, order_id: str) -> list[OrderStatusHistory]:
    return list(s.scalars(
        select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == order_id,
            OrderStatusHistory.domain == StatusDomain.PREPARATION,
        )
    ))


def _audits(s: Session, order_id: str) -> list[AuditLog]:
    return list(s.scalars(
        select(AuditLog).where(
            AuditLog.action == AUDIT_ACTION, AuditLog.target_id == order_id,
        )
    ))


def test_select_candidates_matches_guards(session_factory):
    """El prefiltro coincide con los guards: solo los dos elegibles salen."""
    with session_factory() as s:
        ids = _seed(s)
    with session_factory() as s:
        numbers = {o.order_number for o in select_candidates(s)}
    assert numbers == {"PAID-PENDING", "PARTIAL-PENDING"}
    assert len(ids) == 7


def test_dry_run_reports_count_and_writes_nothing(session_factory, capsys):
    with session_factory() as s:
        ids = _seed(s)

    assert main([]) == 0  # sin --apply → dry-run
    out = capsys.readouterr().out
    assert "se promoverían 2 de 2" in out

    # NADA escrito: todos siguen con su preparation_status original y sin huella.
    with session_factory() as s:
        for label, oid in ids.items():
            order = s.get(Order, oid)
            expected = (PreparationStatus.IN_QUEUE if label == "paid_in_queue"
                        else PreparationStatus.PENDING_REVIEW)
            assert order.preparation_status == expected, label
            assert _prep_history(s, oid) == [], label
            assert _audits(s, oid) == [], label


def test_apply_promotes_eligible_and_leaves_rest(session_factory, capsys):
    with session_factory() as s:
        ids = _seed(s)

    assert main(["--apply", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "2 pedidos promovidos" in out

    with session_factory() as s:
        for label, oid in ids.items():
            order = s.get(Order, oid)
            if label in _PROMOTED:
                assert order.preparation_status == PreparationStatus.IN_QUEUE, label
                rows = _prep_history(s, oid)
                assert len(rows) == 1
                assert (rows[0].from_status, rows[0].to_status) == (
                    "pending_review", "in_queue")
                assert rows[0].changed_by_user_id is None  # sistema (actor=None)
                assert len(_audits(s, oid)) == 1
            elif label == "paid_in_queue":
                # Ya estaba en cola: intacto, sin nueva huella.
                assert order.preparation_status == PreparationStatus.IN_QUEUE, label
                assert _prep_history(s, oid) == [], label
                assert _audits(s, oid) == [], label
            else:
                assert order.preparation_status == PreparationStatus.PENDING_REVIEW, label
                assert _prep_history(s, oid) == [], label
                assert _audits(s, oid) == [], label


def test_apply_is_idempotent_second_run_promotes_zero(session_factory, capsys):
    with session_factory() as s:
        ids = _seed(s)

    assert main(["--apply", "--yes"]) == 0
    capsys.readouterr()
    # Segunda pasada: ya no quedan candidatos → 0 promovidos, sin duplicar huella.
    assert main(["--apply", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "nada que hacer" in out

    with session_factory() as s:
        for oid in (ids["paid_pending"], ids["partial_pending"]):
            assert len(_prep_history(s, oid)) == 1  # una sola fila, no dos
            assert len(_audits(s, oid)) == 1
