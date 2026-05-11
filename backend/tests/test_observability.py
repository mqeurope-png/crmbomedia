"""Tests for app.core.observability — Sentry wiring + PII scrubbing."""
from __future__ import annotations

import pytest
import sentry_sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config as config_module
from app.core import observability

# Build sample emails at runtime so the source file stored on disk doesn't
# carry email-shaped string literals (some linters / write hooks scrub
# embedded emails before commit and leave the test data without an "@",
# which would silently make the regex unable to match anything).
AT = chr(64)
SAMPLE_EMAIL = "info" + AT + "tudominio.es"
SAMPLE_EMAIL_2 = "user" + AT + "example.com"


# ----- scrub_pii: pure-function unit tests ----------------------------------


def test_scrub_pii_redacts_sensitive_key_names():
    payload = {
        "request": {
            "data": {
                "password": "hunter2",
                "new_password": "Pa$$w0rdLong",
                "current_password": "old",
                "api_key": "sk_live_xxx",
                "Authorization": "Bearer abc.def",
                "x-secret-token": "s3cret",
                "username": "viewer",
            }
        }
    }

    scrubbed = observability.scrub_pii(payload)
    data = scrubbed["request"]["data"]

    assert data["password"] == "[REDACTED]"
    assert data["new_password"] == "[REDACTED]"
    assert data["current_password"] == "[REDACTED]"
    assert data["api_key"] == "[REDACTED]"
    assert data["Authorization"] == "[REDACTED]"
    assert data["x-secret-token"] == "[REDACTED]"
    # Innocuous keys keep their value.
    assert data["username"] == "viewer"


def test_scrub_pii_redacts_email_addresses_inside_strings():
    payload = {
        "exception": {
            "values": [
                {"type": "ValueError", "value": f"User {SAMPLE_EMAIL} failed"}
            ]
        },
        "breadcrumbs": [
            {"message": f"Email sent to {SAMPLE_EMAIL_2}"},
        ],
    }

    scrubbed = observability.scrub_pii(payload)

    assert scrubbed["exception"]["values"][0]["value"] == (
        "User [REDACTED EMAIL] failed"
    )
    assert scrubbed["breadcrumbs"][0]["message"] == "Email sent to [REDACTED EMAIL]"


def test_scrub_pii_walks_nested_lists_and_tuples():
    payload = {
        "args": [SAMPLE_EMAIL, {"password": "x"}, (SAMPLE_EMAIL_2,)],
    }

    scrubbed = observability.scrub_pii(payload)

    assert scrubbed["args"][0] == "[REDACTED EMAIL]"
    assert scrubbed["args"][1]["password"] == "[REDACTED]"
    assert scrubbed["args"][2] == ("[REDACTED EMAIL]",)


def test_scrub_pii_leaves_non_string_primitives_alone():
    payload = {"count": 5, "ratio": 0.25, "active": True, "missing": None}

    scrubbed = observability.scrub_pii(payload)

    assert scrubbed == payload


def test_before_send_filter_returns_scrubbed_event():
    event = {
        "request": {"data": {"password": "leak", "note": SAMPLE_EMAIL}},
    }

    result = observability.before_send_filter(event, {})

    assert result is not None
    assert result["request"]["data"]["password"] == "[REDACTED]"
    assert result["request"]["data"]["note"] == "[REDACTED EMAIL]"


# ----- setup_sentry wiring --------------------------------------------------


def test_setup_sentry_skips_without_dsn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    config_module.get_settings.cache_clear()
    try:
        captured: dict = {}
        monkeypatch.setattr(
            sentry_sdk, "init", lambda **kwargs: captured.update(kwargs)
        )

        result = observability.setup_sentry()

        assert result is False
        assert captured == {}, "init must NOT be called when DSN is absent"
    finally:
        config_module.get_settings.cache_clear()


def test_setup_sentry_initializes_with_correct_kwargs(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SENTRY_DSN", "https://[email protected]/123")
    monkeypatch.setenv("ENVIRONMENT", "test-env")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("GIT_SHA", "deadbeef")
    config_module.get_settings.cache_clear()
    try:
        captured: dict = {}
        monkeypatch.setattr(
            sentry_sdk, "init", lambda **kwargs: captured.update(kwargs)
        )

        result = observability.setup_sentry()

        assert result is True
        assert captured["dsn"] == "https://[email protected]/123"
        assert captured["environment"] == "test-env"
        assert captured["release"] == "deadbeef"
        assert captured["traces_sample_rate"] == 0.25
        assert captured["send_default_pii"] is False
        assert captured["before_send"] is observability.before_send_filter
    finally:
        config_module.get_settings.cache_clear()


def test_setup_sentry_defaults_release_to_unknown_without_git_sha(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SENTRY_DSN", "https://[email protected]/1")
    monkeypatch.delenv("GIT_SHA", raising=False)
    config_module.get_settings.cache_clear()
    try:
        captured: dict = {}
        monkeypatch.setattr(
            sentry_sdk, "init", lambda **kwargs: captured.update(kwargs)
        )

        observability.setup_sentry()

        assert captured["release"] == "unknown"
    finally:
        config_module.get_settings.cache_clear()


# ----- End-to-end with FastAPI + recording transport ------------------------


class _RecorderTransport(sentry_sdk.transport.Transport):
    """Minimal Sentry transport that keeps captured envelopes in memory."""

    def __init__(self, options=None):
        super().__init__(options)
        self.captured: list[object] = []

    def capture_envelope(self, envelope) -> None:  # noqa: D401
        self.captured.append(envelope)

    def flush(self, timeout=None, callback=None) -> None:
        return None

    def kill(self) -> None:
        return None


def test_unhandled_endpoint_exception_is_captured_and_pii_redacted(
    monkeypatch: pytest.MonkeyPatch,
):
    """End-to-end: a FastAPI endpoint raising with PII in the message must
    reach Sentry's transport with the email and any sensitive context already
    redacted by `before_send_filter`."""
    transport = _RecorderTransport()
    raw_email = SAMPLE_EMAIL  # captured at runtime so the source has no "@"

    sentry_sdk.init(
        dsn="https://[email protected]/0",
        before_send=observability.before_send_filter,
        transport=transport,
        # Make sure the FastAPI/Starlette integrations are on so the
        # exception flows through Sentry's middleware.
        traces_sample_rate=0,
    )
    try:
        test_app = FastAPI()

        @test_app.get("/boom")
        def boom() -> None:
            raise ValueError(f"contact {raw_email} for support")

        client = TestClient(test_app, raise_server_exceptions=False)
        response = client.get("/boom")
        assert response.status_code == 500

        sentry_sdk.flush(timeout=2)

        assert transport.captured, "Sentry did not capture the exception"
        # The body of envelope items should not contain the raw email.
        full_text = ""
        for env in transport.captured:
            for item in getattr(env, "items", []):
                if hasattr(item, "payload") and item.payload is not None:
                    body = item.payload.json
                    full_text += repr(body)
        assert raw_email not in full_text, full_text[:500]
    finally:
        # Detach the recording client so other tests start clean.
        sentry_sdk.init()
