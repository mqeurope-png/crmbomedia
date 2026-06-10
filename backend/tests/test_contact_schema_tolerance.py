"""Read-path tolerance for `ContactRead.email` and `.phone`.

`ContactCreate` keeps `EmailStr` so new contacts can't be inserted
with garbage. `ContactRead` overrides both fields so a malformed
historical row coming out of the DB doesn't blow up the `/api/contacts`
endpoint with HTTP 500. Tests pin that the surfaced value becomes
`None` and that a warning gets logged for auditing.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.schemas.crm import ContactRead


def _row(**overrides):
    base = {
        "id": "contact-1",
        "first_name": "Ana",
        "last_name": None,
        "email": "ana@example.com",
        "phone": None,
        "origin": None,
        "tags": "",
        "commercial_status": "new",
        "marketing_consent": "unknown",
        "company_id": None,
        "address_country": None,
        "address_country_name": None,
        "address_state": None,
        "address_city": None,
        "lead_score": None,
        "is_email_valid": True,
        "is_active": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "tag_objects": [],
    }
    base.update(overrides)
    return base


def test_valid_email_is_normalised():
    read = ContactRead(**_row(email="ANA@Example.COM"))
    assert read.email == "ana@example.com"


def test_malformed_email_collapses_to_none(caplog):
    """The exact production payload that took the list endpoint down
    used to raise pydantic ValidationError — now it surfaces None."""
    with caplog.at_level("WARNING"):
        read = ContactRead(**_row(email="emete@emete@emete.cat"))
    assert read.email is None
    assert any("malformed" in rec.message for rec in caplog.records)


def test_empty_email_is_none():
    read = ContactRead(**_row(email=""))
    assert read.email is None


def test_phone_with_zero_digits_is_dropped(caplog):
    with caplog.at_level("WARNING"):
        read = ContactRead(**_row(phone="see notes please"))
    assert read.phone is None


def test_phone_too_long_is_dropped():
    read = ContactRead(**_row(phone="+34 600 100 100 — extension 1234 (only mornings)"))
    assert read.phone is None


def test_phone_reasonable_value_is_kept():
    read = ContactRead(**_row(phone="+34 600 100 100"))
    assert read.phone == "+34 600 100 100"


def test_create_schema_still_rejects_malformed_email():
    """The tolerance lives only on the READ path; new inserts must
    keep refusing garbage so the DB doesn't accumulate junk after the
    backfill."""
    from pydantic import ValidationError

    from app.schemas.crm import ContactCreate

    with pytest.raises(ValidationError):
        ContactCreate(
            first_name="Ana",
            email="emete@emete@emete.cat",
        )
