"""Lote 2 · PR-2 — Ajustes ERP: `GET /api/erp/settings/next-references`.

Cada ajuste enseña al lado lo que va a pasar: el siguiente nº de pedido
manual y, por tienda Woo, el prefijo efectivo (metadata de la cuenta →
configurado en Ajustes → derivado del nº de pedido) con la siguiente
referencia de ejemplo (último nº conocido de esa tienda + 1, o 000001).
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderSource
from app.main import app
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationAccount, IntegrationMode
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
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _store(session: Session, slug: str, *, metadata: dict | None = None) -> IntegrationAccount:
    store = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug,
        display_name=slug, enabled=True, mode=IntegrationMode.LIVE,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    session.add(store)
    session.flush()
    return store


def _order(session: Session, number: str, *, store_id: str | None = None,
           source: OrderSource = OrderSource.WOOCOMMERCE) -> Order:
    order = Order(
        external_source=source, order_number=number, store_id=store_id,
        total_amount=10, currency="EUR",
    )
    session.add(order)
    session.flush()
    return order


def test_next_references_without_orders(http, session_factory) -> None:
    """Sin pedidos: el manual empieza en 000001 y la tienda enseña
    PREFIJO-000001 con el prefijo derivado del nº de pedido."""
    with session_factory() as s:
        _store(s, "boprint")
        s.commit()
    r = http.get("/api/erp/settings/next-references", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["manual_next"] == "MANUAL-000001"
    assert body["stores"] == [{
        "slug": "boprint", "label": "boprint", "prefix": "BOP",
        "prefix_source": "derivado", "next_number": 1,
        "next_number_known": False, "example_ref": "BOP-000001",
    }]


def test_next_references_uses_last_known_number_and_prefix_precedence(
    http, session_factory,
) -> None:
    """Prefijo: metadata de la cuenta → configurado en Ajustes → derivado. El
    ejemplo usa el último nº conocido de ESA tienda + 1; el manual, el mayor
    secuencial + 1."""
    with session_factory() as s:
        boprint = _store(s, "boprint")
        flux = _store(s, "fluxlasers", metadata={"factusol_ref_prefix": "FLE"})
        _store(s, "artisjet")
        _order(s, "BOPRIN-2479", store_id=boprint.id)
        _order(s, "BOPRIN-2470", store_id=boprint.id)
        _order(s, "FLUXLA-5789", store_id=flux.id)
        _order(s, "MANUAL-000123", source=OrderSource.MANUAL)
        _order(s, "MANUAL-000007", source=OrderSource.MANUAL)
        s.commit()
    admin = auth_headers(http, "admin")
    # boprint: prefijo configurado en Ajustes (manda sobre el derivado «BOP»).
    r0 = http.patch("/api/erp/settings", json={
        "factusol_ref_prefix_by_store": {"boprint": "BP", "fluxlasers": "FLX"},
    }, headers=admin)
    assert r0.status_code == 200, r0.text

    r = http.get("/api/erp/settings/next-references", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["manual_next"] == "MANUAL-000124"
    by_slug = {s["slug"]: s for s in body["stores"]}
    assert by_slug["boprint"]["prefix"] == "BP"
    assert by_slug["boprint"]["prefix_source"] == "ajustes"
    assert by_slug["boprint"]["next_number"] == 2480
    assert by_slug["boprint"]["next_number_known"] is True
    assert by_slug["boprint"]["example_ref"] == "BP-002480"
    # fluxlasers: el metadata de la cuenta manda sobre el configurado «FLX».
    assert by_slug["fluxlasers"]["prefix"] == "FLE"
    assert by_slug["fluxlasers"]["prefix_source"] == "cuenta"
    assert by_slug["fluxlasers"]["example_ref"] == "FLE-005790"
    # artisjet: sin pedidos ni configuración → derivado y 000001.
    assert by_slug["artisjet"]["prefix"] == "ART"
    assert by_slug["artisjet"]["example_ref"] == "ART-000001"
    assert by_slug["artisjet"]["next_number_known"] is False


def test_settings_exposes_can_edit_by_role(http, session_factory) -> None:
    """`can_edit` dice si el usuario puede guardar (solo ADMIN), para que la
    UI desactive «Guardar cambios» con el motivo en vez de fallar al pulsar."""
    _ = session_factory
    r_admin = http.get("/api/erp/settings", headers=auth_headers(http, "admin"))
    assert r_admin.status_code == 200
    assert r_admin.json()["can_edit"] is True
    r_pedidos = http.get("/api/erp/settings", headers=auth_headers(http, "pedidos"))
    assert r_pedidos.status_code == 200
    assert r_pedidos.json()["can_edit"] is False
    # El PATCH devuelve lo mismo (siempre admin, así que true).
    r_patch = http.patch("/api/erp/settings", json={}, headers=auth_headers(http, "admin"))
    assert r_patch.json()["can_edit"] is True
