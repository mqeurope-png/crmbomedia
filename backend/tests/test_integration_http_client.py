"""Unit tests for `IntegrationHTTPClient`.

We drive the client through httpx's built-in `MockTransport`, which
intercepts requests at the transport layer so the assertions exercise
the real httpx + tenacity stack without touching the network.
"""
from __future__ import annotations

from collections.abc import Generator

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.core.audit import Action
from app.integrations.errors import (
    IntegrationAuthError,
    IntegrationClientError,
    IntegrationNetworkError,
    IntegrationServerError,
)
from app.integrations.http_client import IntegrationHTTPClient
from app.models.crm import AuditLog, Base, ExternalSystem
from app.models.integration_settings import IntegrationAccount


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
                system=ExternalSystem.AGILECRM,
                account_id="es",
                display_name="AgileCRM España",
                api_base_url="https://es.agilecrm.example",
                api_key_encrypted=crypto.encrypt("plain-test-key"),
                credential_status="configured",
            )
        )
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)


async def _run(session, *, transport: httpx.MockTransport, **overrides):
    client = IntegrationHTTPClient(
        session,
        "agilecrm",
        "es",
        max_retries=overrides.pop("max_retries", 3),
    )
    async with client:
        # Swap the auto-built httpx.AsyncClient for one wired to the mock.
        client._client = httpx.AsyncClient(
            base_url=client.base_url,
            timeout=client.timeout,
            transport=transport,
        )
        return await client.request(overrides.get("method", "GET"), overrides.get("url", "/x"))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_get_200_records_audit_and_returns_body(session_factory: sessionmaker):
    import asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    with session_factory() as session:
        resp = asyncio.run(
            _run(session, transport=httpx.MockTransport(handler), url="/api/contacts")
        )
        assert resp.status_code == 200
        assert resp.json == {"ok": True}

        audit_actions = {row.action for row in session.query(AuditLog).all()}
        assert Action.INTEGRATION_API_CALL in audit_actions


# ---------------------------------------------------------------------------
# Retry on 5xx
# ---------------------------------------------------------------------------


def test_retries_on_500_then_succeeds(session_factory: sessionmaker):
    import asyncio

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 2:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"ok": True})

    with session_factory() as session:
        resp = asyncio.run(
            _run(session, transport=httpx.MockTransport(handler), url="/x", max_retries=3)
        )
        assert resp.status_code == 200
        assert attempts["count"] == 2


def test_gives_up_on_persistent_500(session_factory: sessionmaker):
    import asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="still broken")

    with session_factory() as session:
        with pytest.raises(IntegrationServerError):
            asyncio.run(
                _run(
                    session,
                    transport=httpx.MockTransport(handler),
                    url="/x",
                    max_retries=2,
                )
            )


# ---------------------------------------------------------------------------
# Network errors retry; final failure surfaces as IntegrationNetworkError
# ---------------------------------------------------------------------------


def test_network_error_retries_and_then_raises(session_factory: sessionmaker):
    import asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with session_factory() as session:
        with pytest.raises(IntegrationNetworkError):
            asyncio.run(
                _run(
                    session,
                    transport=httpx.MockTransport(handler),
                    url="/x",
                    max_retries=2,
                )
            )


# ---------------------------------------------------------------------------
# 429 + Retry-After
# ---------------------------------------------------------------------------


def test_respects_retry_after_and_succeeds(session_factory: sessionmaker, monkeypatch):
    import asyncio

    # Avoid actually sleeping; we just want to confirm the retry happens
    # and the second response is honoured.
    monkeypatch.setattr("app.integrations.http_client.time.sleep", lambda *_a, **_kw: None)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, text="slow down")
        return httpx.Response(200, json={"ok": True})

    with session_factory() as session:
        resp = asyncio.run(
            _run(session, transport=httpx.MockTransport(handler), url="/x", max_retries=3)
        )
        assert resp.status_code == 200
        assert attempts["count"] == 2


# ---------------------------------------------------------------------------
# 401 / 403 mark the credential_status and audit
# ---------------------------------------------------------------------------


def test_401_marks_credential_error_and_audits(session_factory: sessionmaker):
    import asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad token")

    with session_factory() as session:
        with pytest.raises(IntegrationAuthError):
            asyncio.run(
                _run(session, transport=httpx.MockTransport(handler), url="/x", max_retries=1)
            )
        # The same session_factory is shared; flushed by the client.
        with session_factory() as check:
            account = check.query(IntegrationAccount).filter_by(account_id="es").one()
            assert account.credential_status == "error"
            audit = {row.action for row in check.query(AuditLog).all()}
            assert Action.INTEGRATION_AUTH_FAILED in audit


# ---------------------------------------------------------------------------
# 4xx (non-auth) does not retry and surfaces IntegrationClientError
# ---------------------------------------------------------------------------


def test_404_does_not_retry(session_factory: sessionmaker):
    import asyncio

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(404, text="not found")

    with session_factory() as session:
        with pytest.raises(IntegrationClientError):
            asyncio.run(
                _run(
                    session,
                    transport=httpx.MockTransport(handler),
                    url="/x",
                    max_retries=3,
                )
            )
        assert attempts["count"] == 1
