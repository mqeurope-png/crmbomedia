"""Backwards-compat shim for v2.4c's snooze worker module.

Sprint Email v2.4e renamed `app.email_snooze` → `app.email_scheduled_sweep`
and switched the handler logic from "unsnooze due threads" to "send
pending scheduled messages". The renaming was correct code-side
but broke the deploy: RQ persists scheduled jobs in Redis using
the original Python import path. The first heartbeat job armed
under v2.4c was queued as `app.email_snooze._sweep_and_rearm`, so
the worker crashes on dequeue:

    ValueError: Invalid attribute name: _sweep_and_rearm
    AttributeError: module 'app' has no attribute 'email_snooze'

This file forwards the legacy callable to the new module so the
stale Redis job runs cleanly. The forwarded handler arms the
NEXT tick using the new module path, so after one successful
sweep every queued job in Redis points at the new location and
this shim falls dormant. We leave it on disk anyway — it's tiny
and there's no scheduled future where we'd want to break the
forward path again.
"""
from __future__ import annotations

from app.email_scheduled_sweep import _sweep_and_rearm as _new_sweep_and_rearm
from app.email_scheduled_sweep import schedule_sweep


def _sweep_and_rearm() -> None:
    """Legacy entry point preserved for the stale RQ job. Delegates
    to the renamed handler which itself re-arms the next tick
    against the new module path."""
    _new_sweep_and_rearm()


__all__ = ["_sweep_and_rearm", "schedule_sweep"]
