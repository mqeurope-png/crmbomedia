"""ERP-F3 — estado de cobro de las facturas (ESTFAC): etiquetas, marcado por
clave compuesta reutilizando el escritor único, idempotencia, config vacía,
filtro y auto-marcado."""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings, Order, OrderSource
from app.integrations.factusol import service
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.documents import estado_label, list_documents
from app.integrations.factusol.service import (
    _auto_mark_paid_enabled,
    _order_is_paid,
    invoice_payment_value,
    mark_invoice_payment,
    mark_origin_converted,
)
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users


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


@pytest.fixture()
def db(session_factory) -> Generator[Session, None, None]:
    with session_factory() as s:
        yield s


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _set_cfg(s: Session, **payload: Any) -> None:
    cfg = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is None:
        cfg = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
        s.add(cfg)
    cfg.factusol_series_json = json.dumps(payload)
    s.commit()


class RecordingClient:
    """Cliente FACTUSOL mínimo: registra `update_record` y puede fallar."""

    def __init__(self, *, fail: bool = False, f_fac_rows: list[dict] | None = None):
        self.default_ejercicio = "2026"
        self.updates: list[tuple[str, dict]] = []
        self._fail = fail
        self._rows = f_fac_rows or []

    def update_record(self, tabla, data, *, ejercicio=None):
        if self._fail:
            raise FactusolError("update failed", status=500)
        self.updates.append((tabla, data))
        return {"ok": True}

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        return list(self._rows) if tabla == "F_FAC" else []


# --- Parte A: etiquetas -----------------------------------------------------


def test_estfac_labels_pending_and_paid() -> None:
    assert estado_label("facturas", 0) == "Pendiente de cobro"
    assert estado_label("facturas", 2) == "Cobrada"
    assert estado_label("facturas", "2.0") == "Cobrada"
    # F3-fix1: 1 ya está confirmado (cobro parcial); el resto sigue crudo.
    assert estado_label("facturas", 1) == "Cobro parcial"
    assert estado_label("facturas", 9) == "Estado 9"
    assert estado_label("facturas", None) == "—"


# --- Parte B: marcado -------------------------------------------------------


def test_mark_invoice_paid_writes_composite_key(db) -> None:
    _set_cfg(db)  # sin overrides → defaults 2/0
    client = RecordingClient()
    marked, motivo = mark_invoice_payment(
        client, db, serie=1, codigo=260720, paid=True, ejercicio="2026",
    )
    assert marked and motivo is None
    assert client.updates == [
        ("F_FAC", {"TIPFAC": 1, "CODFAC": 260720, "ESTFAC": "2"}),
    ]
    # La inversa escribe el pendiente (0).
    mark_invoice_payment(
        client, db, serie=1, codigo=260720, paid=False, ejercicio="2026",
    )
    assert client.updates[-1] == (
        "F_FAC", {"TIPFAC": 1, "CODFAC": 260720, "ESTFAC": "0"},
    )


def test_mark_invoice_paid_uses_shared_helper(db) -> None:
    # Ni el marcado de cobro ni el de origen deben tener implementación propia:
    # ambos pasan por el escritor ÚNICO `_write_document_estado`.
    _set_cfg(db, estpcl_invoiced="2")
    client = RecordingClient()
    with patch.object(
        service, "_write_document_estado", return_value=(True, None),
    ) as shared:
        mark_invoice_payment(
            client, db, serie=1, codigo=260720, paid=True, ejercicio="2026",
        )
        mark_origin_converted(
            client, db, source_type="pedidos", serie=1, codigo=5,
            ejercicio="2026",
        )
    assert shared.call_count == 2
    tablas = {call.kwargs["tabla"] for call in shared.call_args_list}
    assert tablas == {"F_FAC", "F_PCL"}


def test_mark_paid_is_idempotent(db) -> None:
    _set_cfg(db)
    client = RecordingClient()
    marked, motivo = mark_invoice_payment(
        client, db, serie=1, codigo=260720, paid=True, ejercicio="2026",
        current_estado="2",  # ya cobrada
    )
    assert marked and motivo is None
    assert client.updates == []  # no se reescribe


def test_mark_paid_failure_does_not_update_ui_state(db) -> None:
    _set_cfg(db)
    client = RecordingClient(fail=True)
    marked, motivo = mark_invoice_payment(
        client, db, serie=1, codigo=260720, paid=True, ejercicio="2026",
    )
    # NUNCA lanza; devuelve (False, motivo) → el frontend no cambia su estado.
    assert marked is False and motivo


def test_empty_config_skips_marking(db) -> None:
    _set_cfg(db, estfac_cobrada="")  # vacío explícito = no marcar
    assert invoice_payment_value(db, paid=True) is None
    client = RecordingClient()
    marked, motivo = mark_invoice_payment(
        client, db, serie=1, codigo=260720, paid=True, ejercicio="2026",
    )
    assert marked is False and motivo
    assert client.updates == []


# --- Parte C: filtro --------------------------------------------------------


def _fac_row(cod: int, estfac: str) -> dict:
    return {
        "TIPFAC": "1", "CODFAC": cod, "ESTFAC": estfac,
        "CLIFAC": 1, "CNOFAC": f"Cliente {cod}",
        "FECFAC": "2026-07-31T00:00:00", "TOTFAC": 100.0,
        "REFFAC": f"BOP-{cod}",
    }


def test_invoice_filter_by_payment_status(db) -> None:
    client = RecordingClient(f_fac_rows=[
        _fac_row(260719, "2"),  # cobrada (MOVIATICOS)
        _fac_row(260720, "0"),  # pendiente (SOLITIUM)
    ])
    pendientes = list_documents(client, "facturas", ejercicio="2026", estado="0")
    assert [d["codigo"] for d in pendientes["items"]] == [260720]
    cobradas = list_documents(client, "facturas", ejercicio="2026", estado="2")
    assert [d["codigo"] for d in cobradas["items"]] == [260719]
    todas = list_documents(client, "facturas", ejercicio="2026")
    assert todas["total"] == 2


# --- Parte D: auto-marcado --------------------------------------------------


def _order(db: Session, *, payment_status: str = "pending") -> Order:
    o = Order(external_source=OrderSource.WOOCOMMERCE, order_number="W-1",
              total_amount=100, currency="EUR", payment_status=payment_status)
    db.add(o)
    db.commit()
    return o


def test_auto_mark_paid_only_when_order_paid_and_enabled(db) -> None:
    paid = _order(db, payment_status="paid")
    pending = _order(db, payment_status="pending")
    # Por defecto (sin config) está DESACTIVADO.
    _set_cfg(db)
    assert _auto_mark_paid_enabled(db) is False
    # Activado, solo cuenta un pedido realmente pagado.
    _set_cfg(db, auto_mark_paid_when_order_paid=True)
    assert _auto_mark_paid_enabled(db) is True
    assert _order_is_paid(paid) is True
    assert _order_is_paid(pending) is False


# --- Parte B: endpoint (permiso + confirmación) -----------------------------


def test_mark_paid_requires_confirmation(http) -> None:
    r = http.post(
        "/api/erp/factusol/documents/facturas/1/260720/payment",
        headers=auth_headers(http, "admin"),
        json={"confirm": False, "paid": True},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "confirmation_required"


def test_mark_paid_requires_erp_edit_permission(http) -> None:
    # `manager` puede VER el ERP pero no EDITAR (no está en ERP_EDIT_ROLES).
    r = http.post(
        "/api/erp/factusol/documents/facturas/1/260720/payment",
        headers=auth_headers(http, "manager"),
        json={"confirm": True, "paid": True},
    )
    assert r.status_code == 403
