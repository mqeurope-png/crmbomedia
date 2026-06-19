"""Sprint Webhooks Agile Real-Time — intake + worker tests.

Covers the full path the route exercises:

- 401 on bad token.
- 200 + skipped on unknown / disabled / no-secret account.
- 202 + webhook_events row on valid intake.
- Worker upsert: add_contact → contact created + assignment rule fires;
  update_contact → existing contact updated; delete_contact →
  Contact.is_active=False + external_status=deleted_in_origin.
- Idempotency: two add_contact webhooks → one Contact row.
- Audit log records the lifecycle.
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.integrations.agilecrm.webhook_intake import generate_webhook_secret
from app.integrations.agilecrm.webhooks import (
    process_agilecrm_webhook_job,
)
from app.main import app
from app.models.crm import (
    AuditLog,
    Base,
    Contact,
    ExternalReference,
    ExternalSystem,
)
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from app.models.webhook_events import WebhookEvent, WebhookEventStatus
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
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    # Patch the worker's own session opener so it reuses the in-memory
    # sqlite — otherwise the RQ entrypoint would build a fresh engine
    # backed by the real DATABASE_URL.
    from app.integrations.agilecrm import webhooks as _wh

    monkeypatch.setattr(
        _wh,
        "Session",
        lambda *_a, **_kw: session_factory(),
    )
    monkeypatch.setattr(_wh, "get_engine", lambda: None)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


SECRET = "test-secret-fixed-for-deterministic-asserts-43c"


def _seed_account(
    session_factory: sessionmaker,
    *,
    account_id: str = "artisjet-europe",
    enabled: bool = True,
    webhook_secret: str | None = SECRET,
) -> str:
    """Create an AgileCRM integration_account; return its row id."""
    with session_factory() as session:
        account = IntegrationAccount(
            system=ExternalSystem.AGILECRM,
            account_id=account_id,
            display_name=account_id.title(),
            enabled=enabled,
            mode=IntegrationMode.LIVE,
            status=IntegrationStatus.CONFIGURED,
            credential_status="configured",
            sync_priority=100,
            webhook_secret=webhook_secret,
        )
        session.add(account)
        session.commit()
        return account.id


def _agile_payload(
    *,
    event: str,
    external_id: str = "agile-1001",
    email: str = "lead@example.com",
    first_name: str = "Lead",
    last_name: str = "Tester",
) -> dict[str, object]:
    """AgileCRM contact webhook body. Properties is the array shape
    documented in the vendor docs; the mapper flattens it."""
    return {
        "event": event,
        "contact": {
            "id": external_id,
            "properties": [
                {"name": "first_name", "value": first_name},
                {"name": "last_name", "value": last_name},
                {"name": "email", "value": email},
            ],
            "tags": [],
        },
    }


def _intake_url(account_id: str, *, token: str = SECRET) -> str:
    return f"/api/webhooks/agilecrm/{account_id}/incoming?token={token}"


# Disable the Redis rate limiter for every test so we don't need a
# fake Redis just to validate intake behaviour. Patch BOTH the module
# the helper lives in and the name FastAPI's route binds at import
# time (`from ... import webhook_rate_limit_exceeded` in
# app/api/webhooks.py snapshots the symbol).
@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import webhooks as _webhooks_router
    from app.integrations.agilecrm import webhook_intake

    monkeypatch.setattr(
        webhook_intake, "webhook_rate_limit_exceeded", lambda *, ip: False
    )
    monkeypatch.setattr(
        _webhooks_router,
        "webhook_rate_limit_exceeded",
        lambda *, ip: False,
    )


@pytest.fixture(autouse=True)
def _inline_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the RQ enqueue helper to run the worker inline so tests
    can assert the post-processing state without a real Redis."""
    from app.api import webhooks as _webhooks_router
    from app.integrations.agilecrm import webhook_intake

    def _inline(webhook_event_id: str) -> None:
        from app.integrations.agilecrm.webhooks import (
            process_agilecrm_webhook_job,
        )

        process_agilecrm_webhook_job(webhook_event_id)

    monkeypatch.setattr(
        webhook_intake, "enqueue_agilecrm_webhook_job", _inline
    )
    monkeypatch.setattr(
        _webhooks_router, "enqueue_agilecrm_webhook_job", _inline
    )


# ---------------------------------------------------------------------
# Intake endpoint
# ---------------------------------------------------------------------


def test_intake_unknown_account_returns_200_skipped(
    client: TestClient,
) -> None:
    res = client.post(
        _intake_url("does-not-exist"),
        json=_agile_payload(event="add_contact"),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "skipped"


def test_intake_disabled_account_returns_200_skipped(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_account(session_factory, enabled=False)
    res = client.post(
        _intake_url("artisjet-europe"),
        json=_agile_payload(event="add_contact"),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "skipped"


def test_intake_account_without_secret_returns_200_skipped(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_account(session_factory, webhook_secret=None)
    res = client.post(
        _intake_url("artisjet-europe"),
        json=_agile_payload(event="add_contact"),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "skipped"


def test_intake_invalid_token_returns_401(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_account(session_factory)
    res = client.post(
        _intake_url("artisjet-europe", token="wrong"),
        json=_agile_payload(event="add_contact"),
    )
    assert res.status_code == 401


def test_intake_valid_payload_returns_202_and_persists_event(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_account(session_factory)
    res = client.post(
        _intake_url("artisjet-europe"),
        json=_agile_payload(event="add_contact"),
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "queued"
    webhook_event_id = body["webhook_event_id"]

    with session_factory() as session:
        rows = list(session.scalars(select(WebhookEvent)))
        assert len(rows) == 1
        assert rows[0].id == webhook_event_id
        # Worker ran inline so the row is already processed.
        assert rows[0].status == WebhookEventStatus.PROCESSED
        assert rows[0].event_type == "add_contact"
        assert rows[0].account_id == "artisjet-europe"


def test_intake_rate_limit_returns_429(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_account(session_factory)
    from app.api import webhooks as _webhooks_router
    from app.integrations.agilecrm import webhook_intake

    monkeypatch.setattr(
        webhook_intake,
        "webhook_rate_limit_exceeded",
        lambda *, ip: True,
    )
    monkeypatch.setattr(
        _webhooks_router,
        "webhook_rate_limit_exceeded",
        lambda *, ip: True,
    )
    res = client.post(
        _intake_url("artisjet-europe"),
        json=_agile_payload(event="add_contact"),
    )
    assert res.status_code == 429


# ---------------------------------------------------------------------
# Worker side-effects
# ---------------------------------------------------------------------


def test_add_contact_creates_contact_and_audit(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_account(session_factory)
    client.post(
        _intake_url("artisjet-europe"),
        json=_agile_payload(event="add_contact", external_id="42"),
    )
    with session_factory() as session:
        contact = session.scalar(
            select(Contact).where(Contact.email == "lead@example.com")
        )
        assert contact is not None
        ref = session.scalar(
            select(ExternalReference).where(
                ExternalReference.system == ExternalSystem.AGILECRM,
                ExternalReference.account_id == "artisjet-europe",
                ExternalReference.external_id == "42",
            )
        )
        assert ref is not None
        assert ref.contact_id == contact.id
        # Audit recorded "processed" for the event.
        actions = {
            row.action
            for row in session.scalars(select(AuditLog))
        }
        assert "integration.webhook_received" in actions
        assert "integration.webhook_processed" in actions


def test_add_contact_idempotency_one_contact_per_external_id(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Two identical add_contact webhooks → one Contact row.

    The upserter keys off `(system, account_id, external_id)`, so the
    second hit is treated as an update."""
    _seed_account(session_factory)
    payload = _agile_payload(event="add_contact", external_id="dup-1")
    client.post(_intake_url("artisjet-europe"), json=payload)
    client.post(_intake_url("artisjet-europe"), json=payload)
    with session_factory() as session:
        contacts = list(
            session.scalars(
                select(Contact).where(
                    Contact.email == "lead@example.com"
                )
            )
        )
        assert len(contacts) == 1
        events = list(session.scalars(select(WebhookEvent)))
        assert len(events) == 2
        assert all(
            e.status == WebhookEventStatus.PROCESSED for e in events
        )


def test_update_contact_updates_existing_row(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_account(session_factory)
    client.post(
        _intake_url("artisjet-europe"),
        json=_agile_payload(
            event="add_contact",
            external_id="upd-1",
            first_name="Original",
        ),
    )
    client.post(
        _intake_url("artisjet-europe"),
        json=_agile_payload(
            event="update_contact",
            external_id="upd-1",
            first_name="Renamed",
        ),
    )
    with session_factory() as session:
        contact = session.scalar(
            select(Contact).where(Contact.email == "lead@example.com")
        )
        assert contact is not None
        assert contact.first_name == "Renamed"


def test_delete_contact_marks_inactive_and_external_status(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_account(session_factory)
    client.post(
        _intake_url("artisjet-europe"),
        json=_agile_payload(event="add_contact", external_id="del-1"),
    )
    client.post(
        _intake_url("artisjet-europe"),
        json=_agile_payload(event="delete_contact", external_id="del-1"),
    )
    with session_factory() as session:
        contact = session.scalar(
            select(Contact).where(Contact.email == "lead@example.com")
        )
        assert contact is not None
        assert contact.is_active is False
        ref = session.scalar(
            select(ExternalReference).where(
                ExternalReference.system == ExternalSystem.AGILECRM,
                ExternalReference.account_id == "artisjet-europe",
                ExternalReference.external_id == "del-1",
            )
        )
        assert ref is not None
        assert ref.external_status == "deleted_in_origin"


def test_unsupported_event_marks_skipped(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Deal / task / note / unknown events answer 202 (the row is
    persisted for audit) but the worker leaves them as skipped."""
    _seed_account(session_factory)
    res = client.post(
        _intake_url("artisjet-europe"),
        json={"event": "add_deal", "deal": {"id": "x"}},
    )
    assert res.status_code == 202
    with session_factory() as session:
        events = list(session.scalars(select(WebhookEvent)))
        assert len(events) == 1
        assert events[0].status == WebhookEventStatus.SKIPPED


# ---------------------------------------------------------------------
# Worker error path (no contact body)
# ---------------------------------------------------------------------


def test_worker_handles_missing_contact_body(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct worker invocation with a payload that names a supported
    event but has no contact body. Must mark skipped, not crash."""
    from app.integrations.agilecrm import webhooks as _wh

    monkeypatch.setattr(
        _wh,
        "Session",
        lambda *_a, **_kw: session_factory(),
    )
    monkeypatch.setattr(_wh, "get_engine", lambda: None)

    _seed_account(session_factory)
    with session_factory() as session:
        event = WebhookEvent(
            system="agilecrm",
            account_id="artisjet-europe",
            event_type="add_contact",
            payload_json=json.dumps({"event": "add_contact"}),
            status=WebhookEventStatus.RECEIVED,
            received_at=__import__(
                "datetime"
            ).datetime.now(__import__("datetime").UTC),
        )
        session.add(event)
        session.commit()
        webhook_event_id = event.id

    result = process_agilecrm_webhook_job(webhook_event_id)
    assert result == "skipped"
    with session_factory() as session:
        stored = session.get(WebhookEvent, webhook_event_id)
        assert stored is not None
        assert stored.status == WebhookEventStatus.SKIPPED


# ---------------------------------------------------------------------
# Admin endpoints (secret management + stats)
# ---------------------------------------------------------------------


def test_admin_generate_webhook_secret(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_account(session_factory, webhook_secret=None)
    headers = auth_headers(client, "admin")
    res = client.post(
        "/api/integration-accounts/agilecrm/artisjet-europe/webhook-secret/generate",
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["secret"]
    assert "/api/webhooks/agilecrm/artisjet-europe/incoming?token=" in body["url"]

    # Calling generate again → 409 (use /regenerate to rotate).
    res2 = client.post(
        "/api/integration-accounts/agilecrm/artisjet-europe/webhook-secret/generate",
        headers=headers,
    )
    assert res2.status_code == 409


def test_admin_regenerate_webhook_secret_rotates(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_account(session_factory, webhook_secret="old-secret")
    headers = auth_headers(client, "admin")
    res = client.post(
        "/api/integration-accounts/agilecrm/artisjet-europe/webhook-secret/regenerate",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["secret"] != "old-secret"
    # The old secret no longer works.
    bad = client.post(
        _intake_url("artisjet-europe", token="old-secret"),
        json=_agile_payload(event="add_contact"),
    )
    assert bad.status_code == 401
    # The new one does.
    good = client.post(
        _intake_url("artisjet-europe", token=body["secret"]),
        json=_agile_payload(event="add_contact"),
    )
    assert good.status_code == 202


def test_admin_webhook_stats(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_account(session_factory)
    # Drive a couple of intakes so the counters have something to
    # report.
    client.post(
        _intake_url("artisjet-europe"),
        json=_agile_payload(event="add_contact", external_id="s-1"),
    )
    client.post(
        _intake_url("artisjet-europe"),
        json={"event": "add_deal"},
    )

    headers = auth_headers(client, "manager")
    res = client.get(
        "/api/integration-accounts/agilecrm/artisjet-europe/webhook-stats",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["received_total"] == 2
    assert body["received_today"] == 2
    assert body["received_last_24h"] == 2
    assert body["processed_last_24h"] == 1
    assert body["last_received_at"] is not None
    assert body["has_secret"] is True


def test_admin_endpoints_reject_non_agilecrm_systems(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """The current intake is AgileCRM-only; the admin routes guard
    against generating a secret for a system whose receiver wouldn't
    use it (Brevo, etc.)."""
    headers = auth_headers(client, "admin")
    res = client.post(
        "/api/integration-accounts/brevo/whatever/webhook-secret/generate",
        headers=headers,
    )
    assert res.status_code == 400


def test_secret_generator_produces_url_safe_token() -> None:
    secret = generate_webhook_secret()
    assert len(secret) == 43
    # token_urlsafe alphabet: A-Z a-z 0-9 - _
    assert all(c.isalnum() or c in "-_" for c in secret)
