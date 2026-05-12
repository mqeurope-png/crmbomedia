"""Tests for the AgileCRM → internal contact mapper."""
from __future__ import annotations

from app.integrations.agilecrm.mapper import (
    agilecrm_account_label,
    agilecrm_external_id,
    map_agilecrm_contact_to_internal,
)


def _payload(**overrides: object) -> dict[str, object]:
    """Standard AgileCRM contact shape; tests override only the bits
    they need."""
    base = {
        "id": 4242,
        "tags": ["Lead", "Newsletter"],
        "properties": [
            {"name": "first_name", "value": "Ana"},
            {"name": "last_name", "value": "Pérez"},
            {"name": "email", "value": "Ana@Example.COM"},
            {"name": "phone", "value": "+34 600 000 000"},
            {"name": "company", "value": "Acme S.L."},
        ],
    }
    base.update(overrides)
    return base


def test_maps_full_payload():
    record = map_agilecrm_contact_to_internal(_payload())
    assert record["first_name"] == "Ana"
    assert record["last_name"] == "Pérez"
    # Email normalises to lowercase + stripped.
    assert record["email"] == "ana@example.com"
    assert record["phone"] == "+34 600 000 000"
    assert record["origin"] == "agilecrm"
    assert record["tags"] == "Lead,Newsletter"
    assert record["marketing_consent"] == "unknown"
    assert record["commercial_status"] == "new"
    assert record["company_name"] == "Acme S.L."


def test_missing_first_name_falls_back_to_email_local_part():
    record = map_agilecrm_contact_to_internal(
        _payload(properties=[{"name": "email", "value": "ghost@example.com"}])
    )
    assert record["first_name"] == "ghost"
    assert record["last_name"] is None


def test_missing_first_name_and_email_uses_placeholder():
    record = map_agilecrm_contact_to_internal(_payload(properties=[]))
    assert record["first_name"] == "Sin nombre"
    assert record["email"] == ""


def test_tags_accept_dict_shape_and_dedup_and_sort():
    payload = _payload(
        tags=[
            {"tag": "VIP"},
            {"tag": "lead"},
            "Newsletter",
            "Newsletter",
            "  ",
            42,  # garbage discarded silently
        ]
    )
    record = map_agilecrm_contact_to_internal(payload)
    assert record["tags"] == "Newsletter,VIP,lead"


def test_external_id_is_stringified():
    assert agilecrm_external_id({"id": 42}) == "42"
    assert agilecrm_external_id({"id": "abc"}) == "abc"
    assert agilecrm_external_id({}) is None


def test_account_label_prefers_lead_status_then_email():
    assert (
        agilecrm_account_label(
            {"properties": [{"name": "lead_status", "value": "Cliente"}]}
        )
        == "Cliente"
    )
    assert (
        agilecrm_account_label(
            {"properties": [{"name": "email", "value": "user@x.com"}]}
        )
        == "user@x.com"
    )
    assert agilecrm_account_label({}) is None
