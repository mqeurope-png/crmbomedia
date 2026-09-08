"""ERP-F3-fix1 — ESTFAC=1 (cobro parcial) + cobros/saldo desde F_LCO.

Solo lectura: se lee F_LCO, se indexa por la clave COMPUESTA (TFALCO, CFALCO)
y se calcula el saldo pendiente. No se escribe nada en FACTUSOL.
"""
from __future__ import annotations

import logging
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.integrations.factusol.collections import (
    balance_mismatch,
    invoice_collections,
    load_collections_index,
    payment_annotator,
)
from app.integrations.factusol.documents import estado_label, list_documents
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_documents import FakeClient


def _cobro(serie, codigo, linea, importe, **over):
    row = {
        "TFALCO": str(serie), "CFALCO": codigo, "LINLCO": linea,
        "FECLCO": "2026-01-02T00:00:00", "IMPLCO": importe,
        "CPALCO": "002", "CPTLCO": f"COBRO FACTURA Nº: {serie} - {codigo}",
    }
    row.update(over)
    return row


def _fac(serie, codigo, total, estfac, **over):
    row = {
        "TIPFAC": str(serie), "CODFAC": codigo, "TOTFAC": total,
        "ESTFAC": estfac, "CLIFAC": 1, "CNOFAC": f"Cliente {codigo}",
        "FECFAC": "2026-01-01T00:00:00", "REFFAC": f"BOP-{codigo}",
    }
    row.update(over)
    return row


# --- Parte A: etiqueta ------------------------------------------------------


def test_estfac_partial_label() -> None:
    assert estado_label("facturas", "1") == "Cobro parcial"
    assert estado_label("facturas", 1) == "Cobro parcial"
    assert estado_label("facturas", "1.0") == "Cobro parcial"
    # 0 y 2 siguen; cualquier otro, crudo (no se adivina).
    assert estado_label("facturas", "0") == "Pendiente de cobro"
    assert estado_label("facturas", "2") == "Cobrada"
    assert estado_label("facturas", "3") == "Estado 3"


# --- Parte B: cobros + saldo ------------------------------------------------


def test_invoice_shows_collections_and_balance() -> None:
    # 5-260004 (Maria Elena): un cobro de 408,48 de un total de 420,74.
    client = FakeClient({"F_LCO": [_cobro(5, 260004, 1, 408.48)]})
    index = load_collections_index(client, ejercicio="2026")
    summary = invoice_collections(index, 5, 260004, 420.74)
    assert len(summary["cobros"]) == 1
    assert summary["cobros"][0]["importe"] == 408.48
    assert summary["cobros"][0]["fecha"] == "2026-01-02"
    assert summary["total_cobrado"] == 408.48
    assert summary["saldo_pendiente"] == 12.26   # 420,74 − 408,48


def test_invoice_with_no_collections_shows_message() -> None:
    # 1-260720 (SOLITIUM): sin cobros → saldo = total.
    client = FakeClient({"F_LCO": []})
    index = load_collections_index(client, ejercicio="2026")
    summary = invoice_collections(index, 1, 260720, 3623.95)
    assert summary["cobros"] == []
    assert summary["total_cobrado"] == 0.0
    assert summary["saldo_pendiente"] == 3623.95


def test_collections_indexed_by_composite_key() -> None:
    # Un cobro de la serie 1 no debe aparecer en la factura homónima de la 5.
    client = FakeClient({"F_LCO": [
        _cobro(1, 260004, 1, 100.0),
        _cobro(5, 260004, 1, 408.48),
    ]})
    index = load_collections_index(client, ejercicio="2026")
    assert invoice_collections(index, 5, 260004, 420.74)["total_cobrado"] == 408.48
    assert invoice_collections(index, 1, 260004, 100.0)["total_cobrado"] == 100.0


def test_collections_loaded_once_not_per_invoice() -> None:
    client = FakeClient({"F_LCO": [
        _cobro(5, 260004, 1, 408.48), _cobro(1, 260719, 1, 192.39),
    ]})
    annotate = payment_annotator(client, ejercicio="2026")
    docs = [
        {"serie": 5, "codigo": 260004, "total": 420.74, "estado": "1",
         "numero": "5-260004"},
        {"serie": 1, "codigo": 260719, "total": 192.39, "estado": "2",
         "numero": "1-260719"},
        {"serie": 1, "codigo": 260720, "total": 3623.95, "estado": "0",
         "numero": "1-260720"},
    ]
    annotate(docs)
    # F_LCO se lee UNA sola vez para todas las facturas (sin N+1).
    assert sum(1 for c in client.calls if c[0] == "F_LCO") == 1
    assert docs[0]["saldo_pendiente"] == 12.26
    assert docs[1]["saldo_pendiente"] == 0.0
    assert docs[2]["saldo_pendiente"] == 3623.95


def test_balance_mismatch_logs_warning_but_shows_real_data(caplog) -> None:
    # saldo 0 pero ESTFAC=0 (no «cobrada») → aviso, pero el dato real se
    # respeta (no se corrige nada).
    assert balance_mismatch("0", 0.0, 100.0) is not None
    assert balance_mismatch("2", 0.0, 100.0) is None      # cuadra
    assert balance_mismatch("0", 100.0, 100.0) is None    # cuadra
    assert balance_mismatch("1", 40.0, 100.0) is None     # parcial: cualquier saldo
    client = FakeClient({"F_LCO": [_cobro(5, 1, 1, 100.0)]})
    annotate = payment_annotator(client, ejercicio="2026")
    doc = {"serie": 5, "codigo": 1, "total": 100.0, "estado": "0",
           "numero": "5-000001"}
    with caplog.at_level(logging.WARNING):
        annotate([doc])
    assert any("saldo" in r.message.lower() for r in caplog.records)
    assert doc["saldo_pendiente"] == 0.0   # dato REAL, sin corregir


# --- Parte C: filtro incluye parciales --------------------------------------


def test_invoice_filter_includes_partial() -> None:
    client = FakeClient({"F_FAC": [
        _fac(5, 260004, 420.74, "1"),   # parcial
        _fac(1, 260719, 192.39, "2"),   # cobrada
        _fac(1, 260720, 3623.95, "0"),  # pendiente
    ]})
    partial = list_documents(client, "facturas", ejercicio="2026", estado="1")
    assert [d["numero"] for d in partial["items"]] == ["5-260004"]
    assert partial["items"][0]["estado_label"] == "Cobro parcial"


# --- endpoint (wiring de punta a punta) -------------------------------------


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
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_detail_endpoint_includes_collections(http) -> None:
    from unittest.mock import patch

    tables = {
        "F_FAC": [_fac(5, 260004, 420.74, "1")],
        "F_LFA": [], "F_ALB": [], "F_LAL": [],
        "F_LCO": [_cobro(5, 260004, 1, 408.48, CPALCO=8)],
        "F_FOP": [],  # ERP-F5: vacía en producción; el nombre ya no sale de aquí
    }
    with patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=FakeClient(tables),
    ):
        r = http.get(
            "/api/erp/factusol/documents/facturas/5/260004",
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estado_label"] == "Cobro parcial"
    assert body["saldo_pendiente"] == 12.26
    assert body["total_cobrado"] == 408.48
    assert len(body["cobros"]) == 1
    # ERP-F5: CPALCO es la CONTRAPARTIDA (8 = Streamtec Sabadell, serie 5),
    # no la forma de pago.
    assert body["cobros"][0]["contrapartida"] == "8"
    assert body["cobros"][0]["contrapartida_nombre"] == "Streamtec Sabadell"
    assert "forma_pago" not in body["cobros"][0]
