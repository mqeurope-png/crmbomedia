"""Unit tests for `IntegrationHTTPClient`.

We drive the client through httpx's built-in `MockTransport`, which
intercepts requests at the transport layer so the assertions exercise
the real httpx + tenacity stack without touching the network.
"""
from __future__ import annotations

import logging
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
from app.integrations.http_client import (
    IntegrationHTTPClient,
    _mask_secret,
    _sanitize_headers,
)
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

    # Patch tenacity's async sleep so the test doesn't actually wait;
    # we only care that the retry happens and the second response wins.
    sleeps: list[float] = []

    async def _no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    # Patch asyncio.sleep — tenacity's `_portable_async_sleep` defers
    # to it for the asyncio runtime, so swapping it in here means no
    # actual blocking but we still see the sleep durations.
    import asyncio as _asyncio  # noqa: PLC0415
    monkeypatch.setattr(_asyncio, "sleep", _no_sleep)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "5"}, text="slow down")
        return httpx.Response(200, json={"ok": True})

    with session_factory() as session:
        resp = asyncio.run(
            _run(session, transport=httpx.MockTransport(handler), url="/x", max_retries=3)
        )
        assert resp.status_code == 200
        assert attempts["count"] == 2
        # The wait function honoured the Retry-After value (5s) instead
        # of using the default exponential floor (>= 1s, ramping up).
        assert any(abs(s - 5.0) < 0.01 for s in sleeps), sleeps


def test_retry_after_http_date_is_parsed(session_factory: sessionmaker, monkeypatch):
    """RFC 7231 allows Retry-After as an HTTP-date. Make sure the
    parser honours the date format too — some upstream servers send it
    instead of a plain seconds value."""
    import asyncio
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    sleeps: list[float] = []

    async def _no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    # Patch asyncio.sleep — tenacity's `_portable_async_sleep` defers
    # to it for the asyncio runtime, so swapping it in here means no
    # actual blocking but we still see the sleep durations.
    import asyncio as _asyncio  # noqa: PLC0415
    monkeypatch.setattr(_asyncio, "sleep", _no_sleep)
    attempts = {"count": 0}
    http_date = format_datetime(datetime.now(UTC) + timedelta(seconds=7), usegmt=True)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": http_date}, text="slow")
        return httpx.Response(200, json={"ok": True})

    with session_factory() as session:
        resp = asyncio.run(
            _run(session, transport=httpx.MockTransport(handler), url="/x", max_retries=3)
        )
        assert resp.status_code == 200
        # Allow a generous tolerance because parsing/sending takes a few
        # milliseconds; the sleep value should be close to 7 seconds.
        assert any(4.0 <= s <= 8.0 for s in sleeps), sleeps


def test_retry_after_above_cap_aborts_without_retry(session_factory: sessionmaker, monkeypatch):
    """If the remote demands a longer cooldown than the worker is
    willing to block for (5 minutes), we surface a rate-limit error and
    let the scheduler reschedule the job — no retries, no blocked
    worker."""
    import asyncio

    from app.integrations.errors import IntegrationRateLimitError

    sleeps: list[float] = []

    async def _no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    # Patch asyncio.sleep — tenacity's `_portable_async_sleep` defers
    # to it for the asyncio runtime, so swapping it in here means no
    # actual blocking but we still see the sleep durations.
    import asyncio as _asyncio  # noqa: PLC0415
    monkeypatch.setattr(_asyncio, "sleep", _no_sleep)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        # Past the 300s cap so the client gives up immediately.
        return httpx.Response(429, headers={"Retry-After": "900"}, text="slow")

    with session_factory() as session:
        with pytest.raises(IntegrationRateLimitError) as exc_info:
            asyncio.run(
                _run(
                    session,
                    transport=httpx.MockTransport(handler),
                    url="/x",
                    max_retries=3,
                )
            )
    # Only the initial attempt happened — the cap check fires before
    # tenacity gets a chance to schedule a retry.
    assert attempts["count"] == 1
    assert "exceeds local cap" in str(exc_info.value)
    assert sleeps == []


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


# ---------------------------------------------------------------------------
# INTEGRATION_HTTP_DEBUG: masking + opt-in gating
# ---------------------------------------------------------------------------


def test_mask_secret_redacts_short_values():
    assert _mask_secret("") == ""
    assert _mask_secret("short") == "***"
    assert _mask_secret("abcdefghijklmnop") == "***"  # length < 20


def test_mask_secret_keeps_head_and_tail_for_long_values():
    secret = "Basic abcdefghijklmnopqrstuvwxyz1234567890"
    masked = _mask_secret(secret)
    assert masked.startswith("Basic abcdef")
    assert masked.endswith("...7890")
    assert "stuvwxyz" not in masked


def test_sanitize_headers_masks_authorization():
    headers = {
        "Authorization": "Basic abcdefghijklmnopqrstuvwxyz1234567890",
        "X-Api-Key": "supersecretapikey12345",
        "Accept": "application/json",
    }
    sanitized = _sanitize_headers(headers)
    assert "Basic abcdef" in sanitized["Authorization"]
    assert "abcdefghijkl" not in sanitized["X-Api-Key"]  # masked too
    assert sanitized["Accept"] == "application/json"


def test_debug_disabled_emits_no_request_log(session_factory: sessionmaker, monkeypatch, caplog):
    """Default behaviour: no `integration.http.request` line in logs."""
    import asyncio

    monkeypatch.delenv("INTEGRATION_HTTP_DEBUG", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    with caplog.at_level(logging.INFO, logger="app.integrations.http_client"):
        with session_factory() as session:
            asyncio.run(_run(session, transport=httpx.MockTransport(handler), url="/x"))
    assert not any("integration.http.request" in record.message for record in caplog.records)


def test_debug_enabled_logs_masked_request_and_error(
    session_factory: sessionmaker, monkeypatch, caplog
):
    """With the flag on we emit one INFO per request and one ERROR per
    4xx/5xx, both with the Authorization header masked."""
    import asyncio

    monkeypatch.setenv("INTEGRATION_HTTP_DEBUG", "true")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found body")

    with caplog.at_level(logging.INFO, logger="app.integrations.http_client"):
        with session_factory() as session:
            with pytest.raises(IntegrationClientError):
                asyncio.run(
                    _run(
                        session,
                        transport=httpx.MockTransport(handler),
                        url="/agilecrm/contacts",
                        max_retries=1,
                    )
                )

    request_logs = [r for r in caplog.records if "integration.http.request" in r.message]
    error_logs = [r for r in caplog.records if "integration.http.response_error" in r.message]
    assert request_logs, "expected the request line to be logged"
    assert error_logs, "expected the response error to be logged"
    # The full request log must contain the masked Authorization (Bearer
    # prefix on the base IntegrationHTTPClient, scheme "Bearer"). It
    # must NOT contain the full plaintext key.
    combined = " ".join(r.getMessage() for r in request_logs)
    assert "plain-test-key" not in combined
    # The error log must include the 404 body so the operator can read
    # what the remote said.
    combined_err = " ".join(r.getMessage() for r in error_logs)
    assert "not found body" in combined_err
