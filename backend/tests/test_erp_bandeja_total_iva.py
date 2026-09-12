"""ERP · FIX bandeja — el TOTAL es el importe FINAL con IVA, no la base.

`orders.total_amount` (columna TOTAL de la bandeja, cabecera de la ficha,
Cola SAT, limpieza Woo) guardaba en los pedidos creados desde FACTUSOL y en
los manuales la suma de líneas SIN impuestos. Ahora: el total del documento
de origen (TOTPRE / TOTPCL, con IVA) o Σ línea × (1 + IVA); los exentos
quedan igual (base = total). La migración 20260915_0106 recalcula los
existentes (no web) y su `downgrade` vuelve a la base.
"""
from __future__ import annotations

import importlib.util
import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderLine, OrderSource
from app.erp.orders_from_factusol import create_order_from_factusol_document
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_pedido_desde_factusol import FakeClient, _lps, _pre

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260915_0106_order_total_with_tax.py"
)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session_factory(engine) -> Generator[sessionmaker, None, None]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(Company(id="acme", name="Acme SL", factusol_company_id="55555"))
        seed.commit()
    yield factory


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _migration_module():
    spec = importlib.util.spec_from_file_location("mig_total_iva", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_bandeja_total_con_iva(http, session_factory, engine) -> None:
    """`total_amount` es el importe FINAL: TOTPRE (con IVA) en los pedidos
    creados desde FACTUSOL, líneas con su IVA en los manuales; y la
    migración recalcula los existentes sin tocar los pedidos web."""
    # (a) desde un presupuesto FACTUSOL: base 100 + 21 % → TOTPRE 121.
    fake = FakeClient({
        "F_PRE": [_pre(600, total=121.0)],
        "F_LPS": [_lps(600, 1, art="MBO", desc="Cabezal", cant=1, precio=100.0, iva=21.0)],
        "F_FPA": [{"CODFPA": "002", "DESFPA": "Transferencia"}],
    })
    with session_factory() as s:
        o = create_order_from_factusol_document(
            s, fake, doc_type="presupuestos", serie=1, codigo=600, ejercicio="2026",
            company_id="acme",
        )
        s.commit()
        assert float(o.total_amount) == 121.0
        # Sin total en la cabecera → líneas con su IVA (21 % por defecto).
        fake2 = FakeClient({
            "F_PRE": [_pre(601, total=0.0)],
            "F_LPS": [_lps(601, 1, art="A", cant=2, precio=10.0, iva=21.0)],
            "F_FPA": [],
        })
        o2 = create_order_from_factusol_document(
            s, fake2, doc_type="presupuestos", serie=1, codigo=601, ejercicio="2026",
            company_id="acme",
        )
        s.commit()
        assert float(o2.total_amount) == 24.2

    # (b) manual: 100 € al 21 % + 50 € exentos → 171.
    r = http.post("/api/erp/orders", headers=auth_headers(http, "admin"), json={
        "order_number": "MAN-IVA", "company_id": "acme",
        "lines": [
            {"product_sku": "A", "description": "Con IVA", "quantity": 1,
             "unit_price": 100, "tax_rate": 21},
            {"product_sku": "B", "description": "Exento", "quantity": 1,
             "unit_price": 50, "tax_rate": 0},
        ],
    })
    assert r.status_code == 201, r.text
    assert r.json()["total_amount"] == pytest.approx(171.0)
    rows = http.get("/api/erp/orders", headers=auth_headers(http, "user")).json()["items"]
    assert {x["order_number"]: x["total_amount"] for x in rows}["MAN-IVA"] == pytest.approx(171.0)
    detail = http.get(f"/api/erp/orders/{r.json()['id']}",
                      headers=auth_headers(http, "user")).json()
    assert detail["total_amount"] == pytest.approx(171.0)          # cabecera de la ficha

    # (c) la migración recalcula los existentes: base guardada → total.
    with session_factory() as s:
        legacy = Order(
            id="o-legacy", external_source=OrderSource.MANUAL, order_number="MAN-OLD",
            company_id="acme", total_amount=100.0,
        )
        s.add(legacy)
        s.flush()
        s.add(OrderLine(order_id="o-legacy", position=0, product_sku="A", description="A",
                        quantity=1, unit_price=100, tax_rate=21, line_total=100.0))
        s.add(Order(
            id="o-fase1", external_source=OrderSource.FACTUSOL_PEDIDO, order_number="PCL-5-000001",
            company_id="acme", total_amount=50.0,
            packing_json=json.dumps({"factusol_source": {"doc_type": "pedidos", "total": 60.5}}),
        ))
        s.add(Order(
            id="o-woo", external_source=OrderSource.WOOCOMMERCE, external_id="1",
            order_number="BOPRIN-1", company_id="acme", total_amount=99.0,
        ))
        s.commit()
    module = _migration_module()
    with engine.begin() as conn, patch.object(module.op, "get_bind", return_value=conn):
        module.upgrade()
    with session_factory() as s:
        assert float(s.get(Order, "o-legacy").total_amount) == 121.0
        assert float(s.get(Order, "o-fase1").total_amount) == 60.5
        assert float(s.get(Order, "o-woo").total_amount) == 99.0     # Woo no se toca
    with engine.begin() as conn, patch.object(module.op, "get_bind", return_value=conn):
        module.downgrade()
    with session_factory() as s:
        assert float(s.get(Order, "o-legacy").total_amount) == 100.0
