"""Tests for the password-reset email service (Phase A: env-var config)."""
from __future__ import annotations

import logging
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import config as config_module
from app.core.security import hash_password
from app.db.session import get_session
from app.main import app
from app.models.crm import Base, User, UserRole
from app.services import email as email_module
from app.services.email import (
    ConsoleEmailService,
    EmailService,
    SMTPEmailService,
    get_email_service,
)


@pytest.fixture()
def email_capture() -> Generator[ConsoleEmailService, None, None]:
    captured = ConsoleEmailService()
    app.dependency_overrides[get_email_service] = lambda: captured
    yield captured
    app.dependency_overrides.pop(get_email_service, None)


@pytest.fixture()
def client(email_capture: ConsoleEmailService) -> Generator[TestClient, None, None]:
    _ = email_capture  # ensures dependency override is installed
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with testing_session() as seed:
        seed.add(
            User(
                email="viewer@example.com",
                full_name="Viewer User",
                password_hash=hash_password("password123"),
                role=UserRole.VIEWER,
                is_active=True,
            )
        )
        seed.commit()

    def override_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)
    Base.metadata.drop_all(engine)


# ----- ConsoleEmailService unit tests ---------------------------------------


def test_console_service_captures_password_reset(email_capture: ConsoleEmailService):
    email_capture.send_password_reset(
        to_email="x@y.z",
        to_name="Tester",
        token="abc123-token",
    )

    assert len(email_capture.sent) == 1
    sent = email_capture.sent[0]
    assert sent.to_email == "x@y.z"
    assert sent.to_name == "Tester"
    assert "abc123-token" in sent.text_body
    assert "abc123-token" in sent.html_body
    assert "/password-reset?token=abc123-token" in sent.text_body
    assert "/password-reset?token=abc123-token" in sent.html_body
    assert "Recuperación de contraseña" in sent.subject
    assert "Tester" in sent.text_body
    assert "Tester" in sent.html_body


def test_console_service_handles_missing_user_name(email_capture: ConsoleEmailService):
    email_capture.send_password_reset(to_email="anon@y.z", to_name="", token="xyz")

    sent = email_capture.sent[0]
    # Plain "Hola," without name when full_name is empty.
    assert "Hola," in sent.text_body


# ----- End-to-end integration via the API endpoint --------------------------


def test_password_reset_request_sends_email(
    client: TestClient,
    email_capture: ConsoleEmailService,
):
    response = client.post(
        "/api/auth/password-reset/request",
        json={"email": "viewer@example.com"},
    )

    assert response.status_code == 200
    token = response.json()["reset_token"]
    assert token

    assert len(email_capture.sent) == 1
    sent = email_capture.sent[0]
    assert sent.to_email == "viewer@example.com"
    assert sent.to_name == "Viewer User"
    assert token in sent.text_body
    assert f"/password-reset?token={token}" in sent.text_body


def test_password_reset_request_unknown_email_does_not_send(
    client: TestClient,
    email_capture: ConsoleEmailService,
):
    response = client.post(
        "/api/auth/password-reset/request",
        json={"email": "ghost@example.com"},
    )

    assert response.status_code == 200
    # No user → no token created → no email sent.
    assert email_capture.sent == []


# ----- Production fallback: SMTP failure must not change the response -------


class _FailingEmailService(EmailService):
    """Always raises on send, used to simulate SMTP outages."""

    def send_password_reset(self, *, to_email: str, to_name: str, token: str) -> None:
        _ = (to_email, to_name, token)
        raise RuntimeError("simulated smtp failure")


def test_production_returns_202_when_smtp_fails(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
):
    """In production an email-delivery failure must NOT leak account existence
    nor break the request: still 202 + neutral body, only a WARNING in the log.
    """
    failing = _FailingEmailService()
    base = config_module.get_settings()
    overridden = base.model_copy(update={"environment": "production"})

    app.dependency_overrides[get_email_service] = lambda: failing
    app.dependency_overrides[config_module.get_settings] = lambda: overridden

    try:
        with caplog.at_level(logging.WARNING):
            response = client.post(
                "/api/auth/password-reset/request",
                json={"email": "viewer@example.com"},
            )
    finally:
        app.dependency_overrides.pop(config_module.get_settings, None)
        # Restore the email_capture override that the fixture installed.
        # The client fixture's teardown will run after this test completes.

    assert response.status_code == 202
    assert response.json() == {"message": "If the email exists, a reset link has been sent."}
    assert any(
        "could not be delivered" in record.message.lower()
        or "smtp" in record.message.lower()
        for record in caplog.records
    )


# ----- Factory selection by environment -------------------------------------


def _reset_factory_caches() -> None:
    config_module.get_settings.cache_clear()
    email_module.get_email_service.cache_clear()


def test_factory_uses_smtp_when_production_and_host_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    _reset_factory_caches()
    try:
        service = email_module.get_email_service()
        assert isinstance(service, SMTPEmailService)
        assert service.host == "smtp.example.com"
        assert service.port == 587
    finally:
        _reset_factory_caches()


def test_factory_falls_back_to_console_when_production_missing_host(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    _reset_factory_caches()
    try:
        with caplog.at_level(logging.WARNING):
            service = email_module.get_email_service()
        assert isinstance(service, ConsoleEmailService)
        assert any(
            "SMTP_HOST is not set" in record.message for record in caplog.records
        )
    finally:
        _reset_factory_caches()


def test_factory_uses_console_in_development_even_when_host_set(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    _reset_factory_caches()
    try:
        service = email_module.get_email_service()
        assert isinstance(service, ConsoleEmailService)
    finally:
        _reset_factory_caches()


# ----- aiosmtplib parameter mapping -----------------------------------------


def test_smtp_service_maps_starttls_for_port_587():
    """SMTP_USE_TLS=true → start_tls=True, use_tls=False (STARTTLS upgrade)."""
    base = config_module.get_settings()
    overridden = base.model_copy(
        update={
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "user",
            "smtp_password": "pw",
            "smtp_from": "user@example.com",
            "smtp_use_tls": True,
            "smtp_use_ssl": False,
        }
    )
    service = SMTPEmailService.from_settings(overridden)
    assert service.use_tls is True
    assert service.use_ssl is False


def test_smtp_service_maps_implicit_ssl_for_port_465():
    base = config_module.get_settings()
    overridden = base.model_copy(
        update={
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_use_tls": False,
            "smtp_use_ssl": True,
        }
    )
    service = SMTPEmailService.from_settings(overridden)
    assert service.use_ssl is True
    assert service.use_tls is False
