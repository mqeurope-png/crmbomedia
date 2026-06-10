"""Unit tests for migration 0025's backfill helper.

The Python fallback path runs on the CI's SQLite database, so the
helper that parses `external_id` and `metadata` is the right place to
pin the contract — it covers both the historical-backfill row shape
and the live-webhook row shape, plus the corner cases (numeric vs
string ids, missing keys, malformed JSON).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_migration_module():
    path = (
        Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "20260610_0025_activity_events_campaign_brevo_id.py"
    )
    spec = importlib.util.spec_from_file_location("_migration_0025", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


MIGRATION = _load_migration_module()
_resolve = MIGRATION._resolve_campaign_id_py


def test_backfill_external_id_returns_campaign_id():
    assert _resolve("backfill:42:ana@example.com:openers", None) == 42


def test_external_id_without_digit_is_ignored():
    # A non-digit token where the campaign id should be falls back to
    # the metadata pass.
    assert _resolve("backfill:abc:ana@example.com:openers", None) is None


def test_webhook_payload_with_campaign_dash_id():
    metadata = '{"event":"opened","email":"x@y.z","campaign-id":99}'
    assert _resolve("some-message-id", metadata) == 99


def test_webhook_payload_with_underscored_campaign_brevo_id():
    metadata = '{"campaign_brevo_id":"77"}'
    assert _resolve("some-message-id", metadata) == 77


def test_webhook_payload_with_campaign_id_key():
    metadata = '{"campaign_id":55}'
    assert _resolve("some-message-id", metadata) == 55


def test_metadata_without_campaign_returns_none():
    metadata = '{"event":"sent","email":"x@y.z"}'
    assert _resolve("transactional-msg-id", metadata) is None


def test_malformed_metadata_does_not_raise():
    assert _resolve("transactional", "not json") is None
    assert _resolve("transactional", "[1,2,3]") is None


def test_external_id_wins_over_metadata():
    """The backfill column survives a webhook delivery of the same
    event arriving later (different payload could carry a different
    id key); the external_id stays the source of truth."""
    metadata = '{"campaign-id":1}'
    assert _resolve("backfill:42:ana@example.com:openers", metadata) == 42


def test_empty_or_none_inputs():
    assert _resolve(None, None) is None
    assert _resolve("", "") is None
    assert _resolve("backfill:", "{}") is None
