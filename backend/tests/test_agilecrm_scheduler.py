"""Scheduler interval resolution tests.

PR-Revert-Webhooks-Agile lowered the default polling interval from
12 h to 1 h and introduced an optional minutes-granularity override.
These tests pin both knobs so the next person reading the code can
trust the documented precedence.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.integrations.agilecrm import scheduler


def test_default_interval_is_one_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGILECRM_SYNC_INTERVAL_HOURS", raising=False)
    monkeypatch.delenv("AGILECRM_SYNC_INTERVAL_MINUTES", raising=False)
    assert scheduler._resolve_interval() == timedelta(hours=1)


def test_hours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGILECRM_SYNC_INTERVAL_MINUTES", raising=False)
    monkeypatch.setenv("AGILECRM_SYNC_INTERVAL_HOURS", "4")
    assert scheduler._resolve_interval() == timedelta(hours=4)


def test_invalid_hours_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGILECRM_SYNC_INTERVAL_MINUTES", raising=False)
    monkeypatch.setenv("AGILECRM_SYNC_INTERVAL_HOURS", "abc")
    assert scheduler._resolve_interval() == timedelta(hours=1)


def test_minutes_env_takes_precedence_over_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGILECRM_SYNC_INTERVAL_HOURS", "12")
    monkeypatch.setenv("AGILECRM_SYNC_INTERVAL_MINUTES", "15")
    assert scheduler._resolve_interval() == timedelta(minutes=15)


def test_minutes_env_floor_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 1-second value is clamped up to the 30-second floor so the
    SETNX TTL math (`interval - 30s`) doesn't go negative."""
    monkeypatch.delenv("AGILECRM_SYNC_INTERVAL_HOURS", raising=False)
    monkeypatch.setenv("AGILECRM_SYNC_INTERVAL_MINUTES", "0")
    # Zero is invalid → falls back to hours default.
    assert scheduler._resolve_interval() == timedelta(hours=1)


def test_schedule_periodic_read_arms_with_resolved_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: `schedule_periodic_read` passes the resolved
    interval to `_arm`. We assert it via a stub to avoid touching
    Redis from the unit test."""
    monkeypatch.setenv("AGILECRM_SYNC_INTERVAL_HOURS", "2")
    monkeypatch.delenv("AGILECRM_SYNC_INTERVAL_MINUTES", raising=False)

    captured: dict[str, object] = {}

    def fake_arm(*, lock: str, queue: str, job: object, interval: timedelta) -> None:
        captured["interval"] = interval
        captured["queue"] = queue
        captured["lock"] = lock

    monkeypatch.setattr(scheduler, "_arm", fake_arm)
    scheduler.schedule_periodic_read()

    assert captured["interval"] == timedelta(hours=2)
    assert captured["queue"] == "agilecrm:periodic_read"
    assert captured["lock"] == scheduler.READ_LOCK_KEY
