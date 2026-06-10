"""Brevo webhook receiver + event materialisation."""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.integrations.brevo.webhooks import (
    process_brevo_webhook_event,
)
from app.main import app
from app.models.crm import ActivityEvent, AuditLog, Contact, ExternalSystem
from app.models.integration_settings import IntegrationAccount
from tests._test_helpers import seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as session:
        session.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="main",
                display_name="Brevo",
                enabled=True,
            )
        )
        session.add(
            Contact(
                first_name="Ana",
                email="ana@example.com",
                marketing_consent="granted",
            )
        )
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory) -> Generator[TestClient, None, None]:
    with session_factory() as seed:
        seed_test_users(seed)

    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _event(name: str, **overrides):
    base = {
        "event": name,
        "email": "ana@example.com",
        "id": 117,
        "message-id": "<msg-1@brevo>",
        "date": "2026-06-10 12:00:00",
        "subject": "Oferta verano",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Processor unit tests
# ---------------------------------------------------------------------------


def test_opened_event_creates_activity_event(session_factory):
    with session_factory() as session:
        status = process_brevo_webhook_event(
            session, _event("opened"), account_id="main"
        )
        session.commit()
        assert status == "processed"
        event = session.scalar(select(ActivityEvent))
        assert event.event_type == "email.opened"
        assert event.system == "brevo"
        assert event.subject == "Oferta verano"
        assert event.occurred_at is not None


def test_click_event_stores_url(session_factory):
    with session_factory() as session:
        process_brevo_webhook_event(
            session,
            _event("click", link="https://mbolasers.com/promo"),
            account_id="main",
        )
        session.commit()
        event = session.scalar(select(ActivityEvent))
        assert event.event_type == "email.clicked"
        assert event.body == "https://mbolasers.com/promo"


def test_unsubscribe_flips_marketing_consent(session_factory):
    with session_factory() as session:
        process_brevo_webhook_event(
            session, _event("unsubscribe"), account_id="main"
        )
        session.commit()
        contact = session.scalar(select(Contact))
        assert contact.marketing_consent == "unsubscribed"
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "contact.consent_changed_by_webhook"
            )
        )
        assert audit is not None


def test_hard_bounce_invalidates_email(session_factory):
    with session_factory() as session:
        process_brevo_webhook_event(
            session, _event("hard_bounce"), account_id="main"
        )
        session.commit()
        contact = session.scalar(select(Contact))
        assert contact.is_email_valid is False
        # Consent untouched by a bounce.
        assert contact.marketing_consent == "granted"


def test_spam_flips_both(session_factory):
    with session_factory() as session:
        process_brevo_webhook_event(session, _event("spam"), account_id="main")
        session.commit()
        contact = session.scalar(select(Contact))
        assert contact.marketing_consent == "unsubscribed"
        assert contact.is_email_valid is False


def test_unknown_email_is_logged_and_discarded(session_factory, caplog):
    with session_factory() as session:
        with caplog.at_level("WARNING"):
            status = process_brevo_webhook_event(
                session,
                _event("opened", email="stranger@nowhere.invalid"),
                account_id="main",
            )
        session.commit()
        assert status == "unknown_contact"
        assert session.scalar(select(ActivityEvent)) is None
        assert session.scalar(
            select(Contact).where(Contact.email == "stranger@nowhere.invalid")
        ) is None
        assert any("no CRM contact" in rec.message for rec in caplog.records)


def test_duplicate_event_processed_once(session_factory):
    with session_factory() as session:
        first = process_brevo_webhook_event(
            session, _event("delivered"), account_id="main"
        )
        second = process_brevo_webhook_event(
            session, _event("delivered"), account_id="main"
        )
        session.commit()
        assert first == "processed"
        assert second == "duplicate"
        events = list(session.scalars(select(ActivityEvent)))
        assert len(events) == 1


def test_unsupported_event_is_ignored(session_factory):
    with session_factory() as session:
        status = process_brevo_webhook_event(
            session, _event("proxy_open"), account_id="main"
        )
        assert status == "unknown_event"


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------


def test_route_accepts_and_enqueues(client: TestClient, monkeypatch):
    monkeypatch.delenv("BREVO_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    captured: dict = {}

    def fake_enqueue(events, account_id):
        captured["events"] = events
        captured["account_id"] = account_id

    with patch("app.api.webhooks._enqueue_brevo_events", fake_enqueue):
        response = client.post("/api/webhooks/brevo", json=_event("delivered"))
    assert response.status_code == 200, response.text
    assert response.json()["events"] == 1
    assert captured["account_id"] == "main"


def test_route_accepts_event_arrays(client: TestClient, monkeypatch):
    monkeypatch.delenv("BREVO_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    captured: dict = {}

    with patch(
        "app.api.webhooks._enqueue_brevo_events",
        lambda events, account_id: captured.update(events=events),
    ):
        response = client.post(
            "/api/webhooks/brevo",
            json=[_event("delivered"), _event("opened")],
        )
    assert response.status_code == 200
    assert len(captured["events"]) == 2


def test_route_rejects_bad_signature_when_secret_set(
    client: TestClient, monkeypatch
):
    monkeypatch.setenv("BREVO_WEBHOOK_SECRET", "super-secret")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    response = client.post(
        "/api/webhooks/brevo",
        json=_event("delivered"),
        headers={"brevo-signature-token": "wrong"},
    )
    assert response.status_code == 401


def test_route_accepts_valid_signature(client: TestClient, monkeypatch):
    monkeypatch.setenv("BREVO_WEBHOOK_SECRET", "super-secret")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    with patch("app.api.webhooks._enqueue_brevo_events", lambda *a: None):
        response = client.post(
            "/api/webhooks/brevo",
            json=_event("delivered"),
            headers={"brevo-signature-token": "super-secret"},
        )
    assert response.status_code == 200


def test_route_warns_but_accepts_without_secret(
    client: TestClient, monkeypatch, caplog
):
    monkeypatch.delenv("BREVO_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    with (
        patch("app.api.webhooks._enqueue_brevo_events", lambda *a: None),
        caplog.at_level("WARNING"),
    ):
        response = client.post("/api/webhooks/brevo", json=_event("delivered"))
    assert response.status_code == 200
    assert any(
        "WITHOUT signature validation" in rec.message for rec in caplog.records
    )


def test_route_rejects_non_json(client: TestClient, monkeypatch):
    monkeypatch.delenv("BREVO_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    response = client.post(
        "/api/webhooks/brevo",
        content=b"not json",
        headers={"content-type": "text/plain"},
    )
    assert response.status_code == 400
