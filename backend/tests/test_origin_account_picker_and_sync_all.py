"""Sprint Reglas-Assign — PR-Db tests.

1. /api/segments/available-origin-accounts emite `value` como compound
   key `system:account_id` (no `account_id` plano), eliminando el
   matcheo cross-system.
2. POST /api/integration-accounts/sync-all encola una sync por cuenta
   habilitada (admin only).
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session

# Importa los módulos de integraciones para registrar OPERATIONS por
# side-effect — `sync_contacts` se registra en el módulo de jobs.
from app.integrations.agilecrm import jobs as _agile_jobs  # noqa: F401
from app.integrations.brevo import jobs as _brevo_jobs  # noqa: F401
from app.main import app
from app.models.crm import (
    Base,
    ExternalSystem,
    SyncLog,
    SyncStatus,
)
from app.models.integration_settings import IntegrationAccount
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        # 2 Agile (1 enabled, 1 disabled) + 1 Brevo enabled + 1 Brevo with
        # the same account_id literal to validate compound keys.
        seed.add_all(
            [
                IntegrationAccount(
                    system=ExternalSystem.AGILECRM,
                    account_id="default",
                    display_name="Agile Boprint",
                    enabled=True,
                ),
                IntegrationAccount(
                    system=ExternalSystem.AGILECRM,
                    account_id="fluxlasers",
                    display_name="Agile Fluxlasers",
                    enabled=False,
                ),
                IntegrationAccount(
                    system=ExternalSystem.BREVO,
                    account_id="default",
                    display_name="Brevo main",
                    enabled=True,
                ),
            ]
        )
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# -- Bug 1: origin accounts picker emits compound keys --------------


def test_available_origin_accounts_uses_compound_keys(
    client: TestClient,
) -> None:
    resp = client.get(
        "/api/segments/available-origin-accounts",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    values = {item["value"] for item in body}
    # Disabled accounts NO aparecen.
    assert values == {"agilecrm:default", "brevo:default"}
    # `system` field se mantiene (UI lo usa para agrupar).
    by_value = {item["value"]: item for item in body}
    assert by_value["agilecrm:default"]["system"] == "agilecrm"
    assert by_value["brevo:default"]["system"] == "brevo"


# -- Bug 3: sync-all endpoint ---------------------------------------


def test_sync_all_admin_enqueues_one_per_enabled_account(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stub out Redis: en este test no hay Redis disponible, así que
    # mockeamos `queue_for` para evitar la conexión real. El SyncLog
    # ya está creado antes del enqueue (que es lo que validamos).
    from unittest.mock import MagicMock  # noqa: PLC0415

    from app.workers import jobs as workers_jobs  # noqa: PLC0415

    def _fake_queue(system: str, operation: str):
        queue = MagicMock()
        queue.enqueue.return_value = MagicMock(id=f"job-{system}-{operation}")
        return queue

    monkeypatch.setattr(workers_jobs, "queue_for", _fake_queue)

    resp = client.post(
        "/api/integration-accounts/_/sync-all",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    # 2 enabled accounts: 1 Agile + 1 Brevo. La 2ª Agile está disabled.
    assert body["enqueued_count"] == 2, body
    enq = {(item["system"], item["account_id"]) for item in body["enqueued"]}
    assert enq == {("agilecrm", "default"), ("brevo", "default")}

    # Persistencia: SyncLog filas creadas en PENDING.
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(SyncLog).where(SyncLog.operation == "sync_contacts")
            )
        )
        assert len(rows) == 2
        assert all(r.status == SyncStatus.PENDING.value for r in rows)


def test_sync_all_requires_admin(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Manager y user no pueden lanzar el sync global."""
    for role in ("manager", "user"):
        resp = client.post(
            "/api/integration-accounts/_/sync-all",
            headers=auth_headers(client, role),
        )
        assert resp.status_code == 403, f"{role}: {resp.text}"
