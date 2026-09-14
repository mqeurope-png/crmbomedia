"""Fase 3 (ficha de empresa) — la bandeja filtra por empresa (`?company_id=`)
para la «actividad reciente» de la ficha, con el mismo bloque `workflow`.
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
from app.erp.models import Order, OrderSource
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def http() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as s:
        seed_test_users(s)
        s.add_all([
            Company(id="a", name="Acme SL", country="ES", factusol_company_id="1"),
            Company(id="b", name="Beta SL", country="ES"),
        ])
        for oid, number, company in (("o1", "A-1", "a"), ("o2", "A-2", "a"), ("o3", "B-1", "b")):
            s.add(Order(
                id=oid, order_number=number, company_id=company,
                external_source=OrderSource.MANUAL, total_amount=10.0, currency="EUR",
                payment_status="paid", preparation_status="pending_review",
            ))
        s.commit()

    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def test_bandeja_filtra_por_empresa_con_su_workflow(http) -> None:
    h = auth_headers(http, "pedidos")
    body = http.get("/api/erp/orders", params={"company_id": "a", "limit": 5}, headers=h).json()
    assert sorted(i["order_number"] for i in body["items"]) == ["A-1", "A-2"]
    assert all(i["workflow"]["queue"] == "por_revisar" for i in body["items"])
    body = http.get("/api/erp/orders", params={"company_id": "b"}, headers=h).json()
    assert [i["order_number"] for i in body["items"]] == ["B-1"]
    # Sin filtro, todo (como siempre).
    body = http.get("/api/erp/orders", headers=h).json()
    assert len(body["items"]) == 3
