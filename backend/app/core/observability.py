"""Sentry initialization + PII scrubbing.

The app calls `setup_sentry()` at startup (see `app/main.py`). When
`SENTRY_DSN` is empty the function is a no-op so development, CI and
self-hosted deployments without an account stay fully offline.

Privacy: `send_default_pii=False` and the `before_send` hook below run
on every captured event. The hook removes any value whose key looks
sensitive (`password`, `token`, `secret`, `api_key`, `authorization`...)
and redacts email addresses found inside string values to the literal
`[REDACTED EMAIL]`. The redaction is recursive so nested request bodies
and breadcrumbs are covered.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import sentry_sdk

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Substring match against a lower-cased key name. Anything containing one of
# these is treated as a secret and replaced wholesale.
SENSITIVE_KEY_NEEDLES = (
    "password",
    "passwd",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "session",
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_REDACTED = "[REDACTED]"
_REDACTED_EMAIL = "[REDACTED EMAIL]"


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(needle in lowered for needle in SENSITIVE_KEY_NEEDLES)


def _redact_emails(value: str) -> str:
    return _EMAIL_RE.sub(_REDACTED_EMAIL, value)


def scrub_pii(node: Any) -> Any:
    """Recursively scrub sensitive keys and email addresses from `node`.

    - Keys whose name matches `SENSITIVE_KEY_NEEDLES` are replaced with
      the literal `[REDACTED]` regardless of value.
    - Email addresses inside any remaining string value are replaced
      with `[REDACTED EMAIL]`.
    - Lists, tuples and dicts are walked recursively.
    - All other types are returned untouched.
    """
    if isinstance(node, dict):
        return {
            key: (_REDACTED if _is_sensitive_key(key) else scrub_pii(value))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [scrub_pii(item) for item in node]
    if isinstance(node, tuple):
        return tuple(scrub_pii(item) for item in node)
    if isinstance(node, str):
        return _redact_emails(node)
    return node


def before_send_filter(event: dict, hint: dict) -> dict | None:
    """Sentry `before_send` hook — sanitizes events in place before they leave the host."""
    _ = hint
    return scrub_pii(event)


def setup_sentry() -> bool:
    """Initialize Sentry if SENTRY_DSN is configured. Returns True on init."""
    settings = get_settings()
    dsn = settings.sentry_dsn
    if not dsn:
        logger.info("Sentry not initialized (SENTRY_DSN not set).")
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.environment,
        release=settings.git_sha or "unknown",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        before_send=before_send_filter,
    )
    logger.info(
        "Sentry initialized: env=%s release=%s traces=%s",
        settings.environment,
        settings.git_sha or "unknown",
        settings.sentry_traces_sample_rate,
    )
    return True
