"""Lote 5 · Cola SAT — PATCH /api/erp/orders/{id}/tracking.

Guarda el nº de seguimiento sin tocar el estado del pedido (no lo marca
recogido ni enviado). Gate de VISTA (mismo que «Marcar recogido»), así que el
rol de taller/SAT puede rellenarlo. Recorta y limpia (vacío → null).
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users

SEED_COMPANY_ID = "seed-company-tracking"


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
        seed.add(Company(id=SEED_COMPANY_ID, name="Cliente Demo SL",
                         factusol_company_id="55555"))
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


def _create_order(client: TestClient) -> str:
    """Crea un pedido manual (rol pedidos) y devuelve su id."""
    r = client.post(
        "/api/erp/orders",
        json={
            "order_number": "TRK-0001",
            "company_id": SEED_COMPANY_ID,
            "lines": [
                {"product_sku": "SKU-1", "description": "Art", "quantity": 1,
                 "unit_price": 100},
            ],
        },
        headers=auth_headers(client, "pedidos"),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_set_and_clear_tracking(client):
    oid = _create_order(client)

    # Guarda (con espacios que se recortan).
    r = client.patch(
        f"/api/erp/orders/{oid}/tracking",
        json={"tracking_number": "  TRACK-123  "},
        headers=auth_headers(client, "pedidos"),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"id": oid, "tracking_number": "TRACK-123"}

    # Persiste: se lee en la ficha del pedido.
    detail = client.get(
        f"/api/erp/orders/{oid}", headers=auth_headers(client, "pedidos"),
    ).json()
    assert detail["tracking_number"] == "TRACK-123"

    # Vacío → null (se limpia).
    r = client.patch(
        f"/api/erp/orders/{oid}/tracking",
        json={"tracking_number": "   "},
        headers=auth_headers(client, "pedidos"),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"id": oid, "tracking_number": None}

    # null explícito también limpia.
    r = client.patch(
        f"/api/erp/orders/{oid}/tracking",
        json={"tracking_number": None},
        headers=auth_headers(client, "pedidos"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["tracking_number"] is None


def test_view_role_sat_can_save_tracking(client):
    """El rol SAT (solo VISTA) puede guardar el tracking, como «Marcar
    recogido»."""
    oid = _create_order(client)
    r = client.patch(
        f"/api/erp/orders/{oid}/tracking",
        json={"tracking_number": "SAT-777"},
        headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["tracking_number"] == "SAT-777"


def test_viewer_role_forbidden(client):
    """VIEWER no accede al ERP: 403 también aquí."""
    oid = _create_order(client)
    r = client.patch(
        f"/api/erp/orders/{oid}/tracking",
        json={"tracking_number": "X"},
        headers=auth_headers(client, "viewer"),
    )
    assert r.status_code == 403


def test_tracking_too_long_422(client):
    oid = _create_order(client)
    r = client.patch(
        f"/api/erp/orders/{oid}/tracking",
        json={"tracking_number": "x" * 65},
        headers=auth_headers(client, "pedidos"),
    )
    assert r.status_code == 422
