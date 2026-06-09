"""Tests for the AgileCRM → internal contact mapper."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.integrations.agilecrm.mapper import (
    agilecrm_account_label,
    agilecrm_external_id,
    map_agilecrm_contact_to_internal,
)


def _payload(**overrides: object) -> dict[str, object]:
    """Standard AgileCRM contact shape; tests override only the bits
    they need."""
    base: dict[str, object] = {
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


# ---------------------------------------------------------------------------
# Canonical fields (regression coverage from PR-2)
# ---------------------------------------------------------------------------


def test_maps_full_payload():
    record, _ = map_agilecrm_contact_to_internal(_payload())
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
    record, _ = map_agilecrm_contact_to_internal(
        _payload(properties=[{"name": "email", "value": "ghost@example.com"}])
    )
    assert record["first_name"] == "ghost"
    assert record["last_name"] is None


def test_missing_first_name_and_email_uses_placeholder():
    record, _ = map_agilecrm_contact_to_internal(_payload(properties=[]))
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
    record, _ = map_agilecrm_contact_to_internal(payload)
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


# ---------------------------------------------------------------------------
# PR-2 extras: timestamps, address, custom properties, score, owner, source
# ---------------------------------------------------------------------------


def test_minimal_payload_does_not_raise_and_extras_are_empty():
    """A bare-bones payload (just id + email) must not crash and the
    extras dict must be safe to consume even when nothing was set."""
    record, extras = map_agilecrm_contact_to_internal(
        {
            "id": 1,
            "properties": [{"name": "email", "value": "alone@example.com"}],
        }
    )
    assert record["email"] == "alone@example.com"
    assert record["lead_score"] is None
    assert record["custom_fields"] is None
    assert record["address_country"] is None
    assert extras["external_created_at"] is None
    assert extras["external_updated_at"] is None
    assert extras["origin_detail"] is None
    assert extras["metadata"] is None


def test_external_timestamps_parsed_from_unix_seconds():
    record, extras = map_agilecrm_contact_to_internal(
        _payload(
            created_time=1700000000,  # 2023-11-14T22:13:20Z
            updated_time="1750000000",  # accept the string flavour too
        )
    )
    _ = record
    assert extras["external_created_at"] == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    assert extras["external_updated_at"] == datetime(2025, 6, 15, 15, 6, 40, tzinfo=UTC)


def test_address_parsed_from_json_string():
    payload = _payload(
        properties=[
            {"name": "email", "value": "ana@example.com"},
            {
                "name": "address",
                "value": json.dumps(
                    {
                        "country": "ES",
                        "countryname": "España",
                        "state": "Madrid",
                        "city": "Madrid",
                    }
                ),
            },
        ],
    )
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert record["address_country"] == "ES"
    assert record["address_country_name"] == "España"
    assert record["address_state"] == "Madrid"
    assert record["address_city"] == "Madrid"


def test_address_accepts_dict_directly():
    payload = _payload(
        properties=[
            {"name": "email", "value": "ana@example.com"},
            {
                "name": "address",
                "value": {"city": "Barcelona", "state": "CT"},
            },
        ],
    )
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert record["address_city"] == "Barcelona"
    assert record["address_state"] == "CT"
    assert record["address_country"] is None


def test_address_garbage_string_does_not_crash():
    payload = _payload(
        properties=[
            {"name": "email", "value": "ana@example.com"},
            {"name": "address", "value": "{this is not json"},
        ],
    )
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert record["address_city"] is None


def test_custom_properties_collected_into_json_blob():
    payload = _payload(
        properties=[
            {"name": "first_name", "value": "Ana"},
            {"name": "email", "value": "ana@example.com"},
            {"name": "industry", "type": "CUSTOM", "value": "Marine"},
            {"name": "boat_length", "type": "CUSTOM", "value": "12m"},
            # Non-custom properties stay out of the custom_fields bag.
            {"name": "title", "type": "SYSTEM", "value": "Skipper"},
        ],
    )
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert record["custom_fields"] is not None
    decoded = json.loads(record["custom_fields"])
    assert decoded == {"industry": "Marine", "boat_length": "12m"}


def test_lead_score_picked_from_top_level_then_property():
    record, _ = map_agilecrm_contact_to_internal(_payload(lead_score=87))
    assert record["lead_score"] == 87

    # Falls back to star_value when lead_score is missing.
    record, _ = map_agilecrm_contact_to_internal(_payload(star_value="42"))
    assert record["lead_score"] == 42

    # And finally to the property bag.
    record, _ = map_agilecrm_contact_to_internal(
        _payload(properties=[{"name": "score", "value": 13}])
    )
    assert record["lead_score"] == 13


def test_lead_score_ignores_garbage():
    record, _ = map_agilecrm_contact_to_internal(_payload(lead_score="not-a-number"))
    assert record["lead_score"] is None


def test_owner_snapshot_in_metadata():
    payload = _payload(
        owner={
            "id": 999,
            "name": "Ops Operator",
            "email": "ops@example.com",
        }
    )
    _, extras = map_agilecrm_contact_to_internal(payload)
    assert extras["metadata"] is not None
    assert extras["metadata"]["owner"] == {
        "id": "999",
        "name": "Ops Operator",
        "email": "ops@example.com",
    }


def test_owner_field_with_unexpected_shape_is_skipped():
    """If AgileCRM ever ships `owner` as anything other than a dict
    (e.g. a plain string id), we silently skip it instead of
    propagating garbage into the metadata."""
    _, extras = map_agilecrm_contact_to_internal(_payload(owner="just-an-id"))
    if extras["metadata"] is not None:
        assert "owner" not in extras["metadata"]


def test_source_lands_in_origin_detail():
    _, extras = map_agilecrm_contact_to_internal(_payload(source="agilecrm-import"))
    assert extras["origin_detail"] == "agilecrm-import"


def test_raw_tags_preserved_in_metadata():
    payload = _payload(tags=[{"tag": "VIP"}, "lead"])
    _, extras = map_agilecrm_contact_to_internal(payload)
    assert extras["metadata"] is not None
    assert extras["metadata"]["tags_raw"] == [{"tag": "VIP"}, "lead"]
