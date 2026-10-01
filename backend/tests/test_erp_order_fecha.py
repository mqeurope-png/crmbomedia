"""ERP · Cambiar la fecha del pedido (`PATCH /api/erp/orders/{id}/fecha`).

Solo en pedidos que no vienen de la tienda (manuales, muestras, creados desde
un documento de FACTUSOL): la nueva fecha llega a la ficha, las listas y
Seguimiento (pantalla, Excel y hoja); un pedido web no se puede cambiar; queda
en la auditoría; las fechas de factura no se mueven; no toca FACTUSOL.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderSource
from app.main import app
from app.models.crm import AuditLog, Company
from tests._test_helpers import auth_headers, seed_test_users

EMPRESA = "empresa-fecha"


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(Company(id=EMPRESA, name="Cliente Demo SL", factusol_company_id="55555"))
        seed.commit()
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


def _manual(client: TestClient) -> str:
    r = client.post("/api/erp/orders", headers=auth_headers(client, "pedidos"), json={
        "order_number": "MAN-0001", "company_id": EMPRESA,
        "lines": [{"product_sku": "SKU-1", "description": "Art", "quantity": 1,
                   "unit_price": 100}],
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_pedido_manual_cambia_la_fecha_y_se_ve_en_ficha_y_seguimiento(client, session_factory):
    oid = _manual(client)
    headers = auth_headers(client, "pedidos")
    r = client.patch(f"/api/erp/orders/{oid}/fecha", headers=headers, json={"fecha": "2026-09-24"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] is True
    assert client.get(f"/api/erp/orders/{oid}", headers=headers).json()["placed_at"].startswith(
        "2026-09-24")
    # Seguimiento (pantalla / Excel / hoja salen de las mismas filas).
    with session_factory() as s:
        from app.erp.api.seguimiento import _rows

        fila = next(f for f in _rows(s) if f["id"] == oid)
        assert fila["fecha"] == "2026-09-24"
        # Auditoría: anterior → nueva, quién.
        log = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.order_date_changed", AuditLog.target_id == oid)).one()
        assert '"to": "2026-09-24"' in (log.metadata_json or "")
        assert log.actor_user_id is not None
    # Repetir la misma fecha no cambia nada ni deja otro rastro.
    r = client.patch(f"/api/erp/orders/{oid}/fecha", headers=headers, json={"fecha": "2026-09-24"})
    assert r.json()["changed"] is False


def test_un_pedido_web_no_se_puede_cambiar(client, session_factory):
    with session_factory() as s:
        o = Order(order_number="BOPRIN-99959", external_source=OrderSource.WOOCOMMERCE,
                  external_id="99959", company_id=EMPRESA,
                  placed_at=datetime(2026, 9, 30, 10, tzinfo=UTC))
        s.add(o)
        s.commit()
        oid = o.id
    r = client.patch(f"/api/erp/orders/{oid}/fecha", headers=auth_headers(client, "pedidos"),
                     json={"fecha": "2026-09-01"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "web_order"
    with session_factory() as s:
        assert s.get(Order, oid).placed_at.date().isoformat() == "2026-09-30"


def test_sin_permiso_de_edicion_no_se_puede(client):
    oid = _manual(client)
    r = client.patch(f"/api/erp/orders/{oid}/fecha", headers=auth_headers(client, "sat"),
                     json={"fecha": "2026-09-01"})
    assert r.status_code == 403


def test_en_un_pedido_desde_factura_la_fecha_factura_no_se_mueve(client, session_factory):
    """La «Fecha factura» de un pedido creado desde una factura salía de su
    fecha de alta (FECFAC): al cambiar la del pedido, se queda la del documento."""
    with session_factory() as s:
        o = Order(order_number="FAC-2-526109", external_source=OrderSource.FACTUSOL_FACTURA,
                  external_id="526109", company_id=EMPRESA, invoice_status="invoiced_by_erp",
                  factusol_invoice_number="526109", factusol_invoice_serie=2,
                  placed_at=datetime(2026, 9, 20, tzinfo=UTC),
                  approved_at=datetime(2026, 9, 20, tzinfo=UTC))
        s.add(o)
        s.commit()
        oid = o.id
    r = client.patch(f"/api/erp/orders/{oid}/fecha", headers=auth_headers(client, "pedidos"),
                     json={"fecha": "2026-09-15"})
    assert r.status_code == 200, r.text
    with session_factory() as s:
        from app.erp.api.seguimiento import _rows

        fila = next(f for f in _rows(s) if f["id"] == oid)
        assert fila["fecha"] == "2026-09-15"
        assert fila["fecha_factura"] == "2026-09-20"
