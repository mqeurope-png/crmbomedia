"""Merge policy for contact fields that several systems contribute to.

A contact can be linked to AgileCRM *and* Brevo (consolidated by
email). The naive `setattr` upsert overwrites `origin` and the
external dates on every sync, so the last connector to run wins and
the operator sees a single, churning origin. These helpers encode the
intended policy instead:

- `origin` is the FIRST system that imported the contact — never
  overwritten once set. (The full per-system list lives in
  `external_references`; `contacts.origin` stays as the legacy
  single-value marker.)
- `created_at_external` keeps the OLDEST date — the earliest system
  is the contact's real birth.
- `updated_at_external` keeps the NEWEST date — the most recent touch
  in any system.

Both helpers POP their keys from the record dict so the caller's
generic `setattr` loop never sees them.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _as_utc(value: Any) -> datetime | None:
    """Normalise to a tz-aware UTC datetime so comparisons survive
    SQLite's tz-naive round-trip. Non-datetimes (and None) return
    None."""
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def keep_first_origin(contact: Any, record: dict[str, Any]) -> None:
    """Set `contact.origin` only if it isn't set yet, then drop the key
    from `record` so the generic update loop can't overwrite it."""
    new_origin = record.pop("origin", None)
    if new_origin and not contact.origin:
        contact.origin = new_origin


def merge_external_dates(contact: Any, record: dict[str, Any]) -> None:
    """Apply the oldest-creation / newest-update policy, popping both
    keys from `record`."""
    created = _as_utc(record.pop("created_at_external", None))
    updated = _as_utc(record.pop("updated_at_external", None))
    if created is not None:
        current = _as_utc(contact.created_at_external)
        if current is None or created < current:
            contact.created_at_external = created
    if updated is not None:
        current = _as_utc(contact.updated_at_external)
        if current is None or updated > current:
            contact.updated_at_external = updated
