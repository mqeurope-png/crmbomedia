"""Brevo mapper — payload → internal Contact record and back."""
from __future__ import annotations

from app.integrations.brevo.mapper import (
    map_brevo_contact_to_internal,
    map_internal_contact_to_brevo,
)


def _payload(**overrides):
    base = {
        "id": 42,
        "email": "Ana@Example.com",
        "emailBlacklisted": False,
        "smsBlacklisted": False,
        "createdAt": "2026-01-05T10:30:00.000+01:00",
        "modifiedAt": "2026-05-01T08:00:00.000+02:00",
        "attributes": {
            "NOMBRE": "Ana",
            "APELLIDOS": "García",
            "SMS": "+34600100100",
        },
        "listIds": [4, 7],
    }
    base.update(overrides)
    return base


def test_well_formed_contact_maps_native_fields():
    record, extras = map_brevo_contact_to_internal(
        _payload(), "main", list_names={4: "Newsletter", 7: "VIP"}
    )
    assert record["first_name"] == "Ana"
    assert record["last_name"] == "García"
    assert record["email"] == "ana@example.com"
    assert record["phone"] == "+34600100100"
    assert record["is_email_valid"] is True
    assert record["origin"] == "brevo"
    assert record["marketing_consent"] == "unknown"
    assert sorted(record["tag_names"]) == [
        "brevo-list:Newsletter",
        "brevo-list:VIP",
    ]
    assert extras["external_created_at"] is not None
    assert extras["metadata"]["list_ids"] == [4, 7]


def test_malformed_email_collapses_to_none_with_warning(caplog):
    with caplog.at_level("WARNING"):
        record, _ = map_brevo_contact_to_internal(
            _payload(email="emete@emete@emete.cat"), "main"
        )
    assert record["email"] is None
    assert record["is_email_valid"] is False
    assert any("email malformed" in rec.message for rec in caplog.records)


def test_garbage_phone_is_dropped(caplog):
    with caplog.at_level("WARNING"):
        record, _ = map_brevo_contact_to_internal(
            _payload(attributes={"SMS": "call me whenever you can ok"}),
            "main",
        )
    assert record["phone"] is None
    assert any("phone looks malformed" in rec.message for rec in caplog.records)


def test_unknown_attributes_land_in_custom_fields():
    record, _ = map_brevo_contact_to_internal(
        _payload(
            attributes={
                "NOMBRE": "Ana",
                "EMPRESA_SECTOR": "impresión UV",
                "MAQUINA": "MBO 3050",
            }
        ),
        "main",
    )
    assert record["custom_fields"] is not None
    assert "EMPRESA_SECTOR" in record["custom_fields"]
    assert "MAQUINA" in record["custom_fields"]
    # Native field is NOT duplicated into custom.
    assert "NOMBRE" not in record["custom_fields"]


def test_blacklisted_email_marks_unsubscribed():
    record, _ = map_brevo_contact_to_internal(
        _payload(emailBlacklisted=True), "main"
    )
    assert record["marketing_consent"] == "unsubscribed"
    assert record["is_email_valid"] is False


def test_list_ids_without_names_fall_back_to_id():
    record, _ = map_brevo_contact_to_internal(_payload(), "main")
    assert sorted(record["tag_names"]) == ["brevo-list:4", "brevo-list:7"]


def test_inverse_mapping_builds_attributes():
    class FakeContact:
        first_name = "Ana"
        last_name = "García"
        email = "ana@example.com"
        phone = "+34 600 100 100"
        commercial_status = "qualified"
        address_country = "ES"
        lead_score = 80

    payload = map_internal_contact_to_brevo(FakeContact())
    assert payload["email"] == "ana@example.com"
    assert payload["attributes"]["NOMBRE"] == "Ana"
    assert payload["attributes"]["APELLIDOS"] == "García"
    assert payload["attributes"]["SMS"] == "+34 600 100 100"
    assert payload["attributes"]["LEAD_SCORE"] == 80
