"""Merge policy for contact fields several systems contribute to:
first origin wins, oldest external creation, newest external update.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.integrations.contact_merge import keep_first_origin, merge_external_dates


def _contact(**kw):
    base = {
        "origin": None,
        "created_at_external": None,
        "updated_at_external": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


# --- origin ---------------------------------------------------------------


def test_origin_set_when_empty():
    c = _contact()
    record = {"origin": "brevo"}
    keep_first_origin(c, record)
    assert c.origin == "brevo"
    assert "origin" not in record  # popped


def test_origin_not_overwritten_once_set():
    c = _contact(origin="agilecrm")
    record = {"origin": "brevo"}
    keep_first_origin(c, record)
    assert c.origin == "agilecrm"
    assert "origin" not in record


# --- external dates -------------------------------------------------------


def test_creation_takes_the_older_date():
    c = _contact(created_at_external=datetime(2025, 9, 25, tzinfo=UTC))
    record = {
        "created_at_external": datetime(2025, 3, 1, tzinfo=UTC),
        "updated_at_external": None,
    }
    merge_external_dates(c, record)
    assert c.created_at_external == datetime(2025, 3, 1, tzinfo=UTC)


def test_creation_keeps_existing_when_new_is_later():
    c = _contact(created_at_external=datetime(2025, 3, 1, tzinfo=UTC))
    record = {
        "created_at_external": datetime(2025, 9, 25, tzinfo=UTC),
        "updated_at_external": None,
    }
    merge_external_dates(c, record)
    assert c.created_at_external == datetime(2025, 3, 1, tzinfo=UTC)


def test_update_takes_the_newer_date():
    c = _contact(updated_at_external=datetime(2025, 1, 1, tzinfo=UTC))
    record = {
        "created_at_external": None,
        "updated_at_external": datetime(2025, 12, 3, tzinfo=UTC),
    }
    merge_external_dates(c, record)
    assert c.updated_at_external == datetime(2025, 12, 3, tzinfo=UTC)


def test_update_keeps_existing_when_new_is_older():
    c = _contact(updated_at_external=datetime(2025, 12, 3, tzinfo=UTC))
    record = {
        "created_at_external": None,
        "updated_at_external": datetime(2025, 6, 1, tzinfo=UTC),
    }
    merge_external_dates(c, record)
    assert c.updated_at_external == datetime(2025, 12, 3, tzinfo=UTC)


def test_first_dates_populate_from_null():
    c = _contact()
    record = {
        "created_at_external": datetime(2025, 4, 4, tzinfo=UTC),
        "updated_at_external": datetime(2025, 5, 5, tzinfo=UTC),
    }
    merge_external_dates(c, record)
    assert c.created_at_external == datetime(2025, 4, 4, tzinfo=UTC)
    assert c.updated_at_external == datetime(2025, 5, 5, tzinfo=UTC)
    assert "created_at_external" not in record
    assert "updated_at_external" not in record


def test_naive_existing_date_is_compared_in_utc():
    # SQLite hands back tz-naive datetimes; the merge must still pick
    # the older one without a tz-compare crash.
    c = _contact(created_at_external=datetime(2025, 9, 25, 9, 37))  # naive
    record = {
        "created_at_external": datetime(2025, 3, 1, 9, 0, tzinfo=UTC),
        "updated_at_external": None,
    }
    merge_external_dates(c, record)
    assert c.created_at_external == datetime(2025, 3, 1, 9, 0, tzinfo=UTC)


def test_none_new_dates_leave_columns_untouched():
    c = _contact(
        created_at_external=datetime(2025, 3, 1, tzinfo=UTC),
        updated_at_external=datetime(2025, 5, 1, tzinfo=UTC),
    )
    record = {"created_at_external": None, "updated_at_external": None}
    merge_external_dates(c, record)
    assert c.created_at_external == datetime(2025, 3, 1, tzinfo=UTC)
    assert c.updated_at_external == datetime(2025, 5, 1, tzinfo=UTC)
