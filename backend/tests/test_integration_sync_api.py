"""Tests for the sync trigger + sync-logs + webhook endpoints."""
from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.audit import Action
from app.db.session import get_session
from app.main import app
from app.models.crm import AuditLog, Base, ExternalSystem, SyncLog, SyncStatus
from app.models.integration_settings import IntegrationAccount
from app.workers.jobs import OPERATIONS, SyncOutcome
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def stack() -> Generator[tuple[TestClient, Engine], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(
            IntegrationAccount(
                system=ExternalSystem.AGILECRM,
                account_id="es",
                display_name="AgileCRM España",
            )
        )
        seed.commit()

    def override_session() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client, engine
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(stack) -> TestClient:
    return stack[0]


# ---------------------------------------------------------------------------
# /sync endpoint
# ---------------------------------------------------------------------------


def test_sync_requires_admin(client: TestClient):
    response = client.post(
        "/api/integration-accounts/agilecrm/es/sync",
        json={"operation": "demo"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 403


def test_sync_returns_409_when_operation_unregistered(client: TestClient):
    response = client.post(
        "/api/integration-accounts/agilecrm/es/sync",
        json={"operation": "not_implemented"},
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 409
    assert "not_implemented" in response.text


def test_sync_enqueues_when_operation_registered(client: TestClient, stack):
    OPERATIONS["agilecrm:demo"] = lambda *_a, **_kw: SyncOutcome(records_processed=1)
    try:
        with patch("app.workers.jobs.queue_for") as queue_for_mock:
            queue_for_mock.return_value.enqueue.return_value = SimpleNamespace(id="job-fake")
            response = client.post(
                "/api/integration-accounts/agilecrm/es/sync",
                json={"operation": "demo"},
                headers=auth_headers(client, "admin"),
            )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["operation"] == "demo"
        assert body["status"] == "pending"
        assert body["job_id"] == "job-fake"

        # The sync_log row was persisted.
        engine = stack[1]
        with Session(engine) as session:
            rows = list(session.query(SyncLog).filter_by(operation="demo").all())
            assert len(rows) == 1
            assert rows[0].status == "pending"
            audit = {a.action for a in session.query(AuditLog).all()}
            assert Action.INTEGRATION_SYNC_TRIGGERED in audit
    finally:
        OPERATIONS.pop("agilecrm:demo", None)


def test_sync_404_when_account_missing(client: TestClient):
    response = client.post(
        "/api/integration-accounts/agilecrm/ghost/sync",
        json={"operation": "demo"},
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /sync-logs endpoint
# ---------------------------------------------------------------------------


def test_sync_logs_listing_and_filter(client: TestClient, stack):
    engine = stack[1]
    with Session(engine) as session:
        for status_value in ("success", "failed"):
            session.add(
                SyncLog(
                    system=ExternalSystem.AGILECRM,
                    account_id="es",
                    operation="demo",
                    status=status_value,
                    triggered_by="cron",
                )
            )
        session.commit()

    response = client.get(
        "/api/integration-accounts/agilecrm/es/sync-logs",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    assert response.headers.get("x-total-count") == "2"

    filtered = client.get(
        "/api/integration-accounts/agilecrm/es/sync-logs",
        params={"status": "failed"},
        headers=auth_headers(client, "manager"),
    )
    assert filtered.status_code == 200
    assert all(r["status"] == "failed" for r in filtered.json())


def test_sync_log_detail(client: TestClient, stack):
    engine = stack[1]
    with Session(engine) as session:
        row = SyncLog(
            system=ExternalSystem.AGILECRM,
            account_id="es",
            operation="demo",
            status=SyncStatus.SUCCESS.value,
        )
        session.add(row)
        session.commit()
        row_id = row.id

    response = client.get(
        f"/api/integration-accounts/agilecrm/es/sync-logs/{row_id}",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200
    assert response.json()["id"] == row_id


def test_sync_log_detail_404_when_wrong_account(client: TestClient, stack):
    engine = stack[1]
    with Session(engine) as session:
        row = SyncLog(
            system=ExternalSystem.AGILECRM,
            account_id="es",
            operation="demo",
            status="success",
        )
        session.add(row)
        session.commit()
        row_id = row.id
    response = client.get(
        f"/api/integration-accounts/agilecrm/wrong/sync-logs/{row_id}",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Generic webhook endpoint
# ---------------------------------------------------------------------------


def test_webhook_persists_payload_and_returns_202(client: TestClient, stack):
    response = client.post(
        "/api/webhooks/agilecrm/es",
        json={"event": "contact.updated", "id": 42},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["received"] is True
    assert body["sync_log_id"]

    engine = stack[1]
    with Session(engine) as session:
        rows = list(session.query(SyncLog).filter_by(operation="webhook_received").all())
        assert len(rows) == 1
        assert rows[0].triggered_by == "webhook"
        assert rows[0].records_processed == 1
        assert rows[0].metadata_json
        assert "contact.updated" in rows[0].metadata_json

        audit = {a.action for a in session.query(AuditLog).all()}
        assert Action.INTEGRATION_WEBHOOK_RECEIVED in audit


def test_webhook_404_when_account_missing(client: TestClient):
    response = client.post(
        "/api/webhooks/agilecrm/ghost",
        json={"event": "x"},
    )
    assert response.status_code == 404


def test_webhook_accepts_non_json_payload(client: TestClient):
    response = client.post(
        "/api/webhooks/agilecrm/es",
        content=b"raw=plain-text-data",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 202
