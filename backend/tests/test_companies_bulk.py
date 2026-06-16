"""Sprint Filtros & Listas — PR-F backend tests.

Bulk dispatch para empresas. La migración de `/companies` al stack
nuevo añade activate / deactivate / change_sector como acciones
masivas — antes la pantalla no tenía bulk en absoluto.
"""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base, Company
from tests._test_helpers import auth_headers, seed_test_users


@dataclass
class _Fixture:
    engine: Engine
    factory: sessionmaker


@pytest.fixture()
def db() -> Generator[_Fixture, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield _Fixture(engine=engine, factory=factory)
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db: _Fixture) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with db.factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        test_client._factory = db.factory  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()


def _seed_companies(factory: sessionmaker, n: int = 3) -> list[str]:
    ids: list[str] = []
    with factory() as session:
        for i in range(n):
            row = Company(name=f"Co {i}", source="manual", is_active=True)
            session.add(row)
            session.flush()
            ids.append(row.id)
        session.commit()
    return ids


def test_bulk_deactivate_flips_is_active(client: TestClient) -> None:
    factory = client._factory  # type: ignore[attr-defined]
    ids = _seed_companies(factory, 3)
    headers = auth_headers(client, "user")
    res = client.post(
        "/api/companies/bulk-action",
        json={"company_ids": ids, "action": "deactivate"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["action"] == "deactivate"
    assert body["affected_count"] == 3
    with factory() as session:
        rows = list(session.scalars(select(Company)))
    assert all(not r.is_active for r in rows)


def test_bulk_activate_idempotent(client: TestClient) -> None:
    """`affected_count` cuenta solo los que cambian — re-aplicar la
    misma acción no es un error pero el contador queda en 0."""
    factory = client._factory  # type: ignore[attr-defined]
    ids = _seed_companies(factory, 2)
    headers = auth_headers(client, "user")
    res = client.post(
        "/api/companies/bulk-action",
        json={"company_ids": ids, "action": "activate"},
        headers=headers,
    )
    assert res.status_code == 200
    # ya estaban activas → cero cambios reales.
    assert res.json()["affected_count"] == 0


def test_bulk_change_sector_requires_payload(client: TestClient) -> None:
    factory = client._factory  # type: ignore[attr-defined]
    ids = _seed_companies(factory, 2)
    headers = auth_headers(client, "user")
    # Sin payload.sector → 400.
    res = client.post(
        "/api/companies/bulk-action",
        json={"company_ids": ids, "action": "change_sector"},
        headers=headers,
    )
    assert res.status_code == 400

    # Con payload.sector → aplica.
    res = client.post(
        "/api/companies/bulk-action",
        json={
            "company_ids": ids,
            "action": "change_sector",
            "payload": {"sector": "Automoción"},
        },
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["affected_count"] == 2
    with factory() as session:
        rows = list(session.scalars(select(Company)))
    assert all(r.sector == "Automoción" for r in rows)


def test_bulk_action_viewer_forbidden(client: TestClient) -> None:
    """`require_user` excluye a viewers (rol read-only)."""
    factory = client._factory  # type: ignore[attr-defined]
    ids = _seed_companies(factory, 1)
    headers = auth_headers(client, "viewer")
    res = client.post(
        "/api/companies/bulk-action",
        json={"company_ids": ids, "action": "deactivate"},
        headers=headers,
    )
    assert res.status_code == 403


def test_bulk_action_unknown_action_returns_422(client: TestClient) -> None:
    factory = client._factory  # type: ignore[attr-defined]
    ids = _seed_companies(factory, 1)
    headers = auth_headers(client, "user")
    res = client.post(
        "/api/companies/bulk-action",
        json={"company_ids": ids, "action": "purge_with_fire"},
        headers=headers,
    )
    # Pydantic Literal rechaza valores no listados → 422.
    assert res.status_code == 422
