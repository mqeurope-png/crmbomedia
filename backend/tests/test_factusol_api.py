"""BoHub ERP Fase C PR C-1 — endpoint admin de smoke-test FACTUSOL."""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderLine
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users

_SMOKE = "/api/erp/factusol/smoke-test"


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
def client(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _order_with_lines(s: Session) -> str:
    c = Company(name="Acme SL", tax_id="B12345678", factusol_company_id="55555")
    s.add(c)
    s.commit()
    o = Order(order_number="MAN-2001", company_id=c.id, total_amount=200)
    s.add(o)
    s.flush()
    s.add(OrderLine(order_id=o.id, position=0, product_sku="SKU-1",
                    product_codart="ART1", description="Art 1",
                    quantity=2, unit_price=100, tax_rate=21, line_total=200))
    s.commit()
    return o.id


def test_smoke_requires_admin(client):
    for role in ("pedidos", "sat", "user", "viewer"):
        r = client.post(f"{_SMOKE}?mode=login", headers=auth_headers(client, role))
        assert r.status_code == 403, role


def test_smoke_login_returns_token_seconds_and_role(client):
    fake = MagicMock()
    fake.token_valid_seconds.return_value = 170
    fake.token_claims.return_value = {"role": "AdminUser"}
    with patch("app.erp.api.factusol.FactusolClient.from_settings", return_value=fake):
        r = client.post(f"{_SMOKE}?mode=login", headers=auth_headers(client, "admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["token_valid_seconds"] == 170
    assert body["role"] == "AdminUser"
    fake.authenticate.assert_called_once()


def test_smoke_read_customers_returns_rows(client):
    fake = MagicMock()
    fake.load_table.return_value = [{"CODCLI": "1"}, {"CODCLI": "2"}]
    with patch("app.erp.api.factusol.FactusolClient.from_settings", return_value=fake):
        r = client.post(f"{_SMOKE}?mode=read_customers", headers=auth_headers(client, "admin"))
    assert r.status_code == 200
    assert r.json()["count"] == 2
    fake.load_table.assert_called_once_with("F_CLI", numero_registros=5)


def test_smoke_dry_run_invoice_returns_payload_without_network(client, session_factory):
    with session_factory() as s:
        oid = _order_with_lines(s)
    # dry-run NO debe instanciar el cliente ni salir a red.
    with patch("app.erp.api.factusol.FactusolClient.from_settings") as from_settings:
        r = client.post(f"{_SMOKE}?mode=dry_run_invoice&order_id={oid}",
                        headers=auth_headers(client, "admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["codcli"] == "55555"                 # vinculado
    assert body["cabecera"]["CODFAC"] == "2001"
    assert len(body["lineas"]) == 1
    from_settings.assert_not_called()


def test_smoke_dry_run_requires_order_id(client):
    r = client.post(f"{_SMOKE}?mode=dry_run_invoice", headers=auth_headers(client, "admin"))
    assert r.status_code == 400


def test_smoke_invalid_mode_returns_400(client):
    r = client.post(f"{_SMOKE}?mode=nope", headers=auth_headers(client, "admin"))
    assert r.status_code == 400
