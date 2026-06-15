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
    # Brevo treats list membership as the opt-in: anything we pull
    # that isn't blacklisted is granted.
    assert record["marketing_consent"] == "granted"
    assert sorted(record["tag_names"]) == [
        "brevo-list:Newsletter",
        "brevo-list:VIP",
    ]
    assert extras["external_created_at"] is not None
    assert extras["metadata"]["list_ids"] == [4, 7]
    # Source-system dates are promoted onto the record for the
    # contact-level merge. ISO 8601 with offset parsed to UTC-aware.
    assert record["created_at_external"] == extras["external_created_at"]
    assert record["updated_at_external"] == extras["external_updated_at"]


def test_external_dates_none_when_brevo_payload_omits_them():
    record, _ = map_brevo_contact_to_internal(
        _payload(createdAt=None, modifiedAt=None), "main"
    )
    assert record["created_at_external"] is None
    assert record["updated_at_external"] is None


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


def test_only_whitelisted_attributes_land_in_custom_fields():
    """Sprint Empresas — sub-PR 2 fix. The mapper used to copy every
    unknown attribute into `custom_fields`, which surfaced internal
    Brevo housekeeping (sib_contact_owner, EXT_ID, TELEFONO_2..,
    ETIQUETA, EMAILABLE_UNSUBSCRIBED) on the contact's ficha.
    Now only the business-curated whitelist makes it through."""
    record, _ = map_brevo_contact_to_internal(
        _payload(
            attributes={
                # Native — routed to a column, never custom.
                "NOMBRE": "Ana",
                # Whitelisted business attrs.
                "GRADO_DE_INTERES": "alto",
                "INTERESADO_EN_DEMO": True,
                # Out-of-whitelist noise — must NOT show up.
                "ETIQUETA": "newsletter",
                "TELEFONO_2": "+34111222333",
                "sib_contact_owner": "ops@bomedia.net",
                "EXT_ID": "X-123",
                "EMPRESA_SECTOR": "impresión UV",
                "MAQUINA": "MBO 3050",
            }
        ),
        "main",
    )
    import json as _json  # noqa: PLC0415

    custom = _json.loads(record["custom_fields"])
    assert custom == {
        "GRADO_DE_INTERES": "alto",
        "INTERESADO_EN_DEMO": True,
    }


def test_blacklisted_email_marks_unsubscribed():
    record, _ = map_brevo_contact_to_internal(
        _payload(emailBlacklisted=True), "main"
    )
    assert record["marketing_consent"] == "unsubscribed"
    assert record["is_email_valid"] is False


def test_sms_blacklisted_also_marks_unsubscribed():
    """`smsBlacklisted` is a second opt-out signal Brevo carries; if
    either flag is set the contact must not be mailed/SMS'd."""
    record, _ = map_brevo_contact_to_internal(
        _payload(emailBlacklisted=False, smsBlacklisted=True), "main"
    )
    assert record["marketing_consent"] == "unsubscribed"


def test_clean_contact_defaults_to_granted_consent():
    """Regression for the post-deploy bug: 17.7k Brevo contacts were
    stuck on `unknown` because the mapper only flipped on blacklist."""
    record, _ = map_brevo_contact_to_internal(
        _payload(emailBlacklisted=False, smsBlacklisted=False), "main"
    )
    assert record["marketing_consent"] == "granted"


def test_long_first_name_is_truncated_with_warning(caplog):
    """Real production payload: a Brevo contact with 240 chars of
    company+department+name in first_name. The old mapper let the
    240-char value reach the ORM and the INSERT failed with
    `Data too long for column 'first_name'`. Now we truncate."""
    very_long = "Ana " * 80  # 320 chars
    with caplog.at_level("WARNING"):
        record, _ = map_brevo_contact_to_internal(
            _payload(attributes={"NOMBRE": very_long, "APELLIDOS": "García"}),
            "main",
        )
    # Resolved from the model — Contact.first_name is String(120).
    from app.integrations.mapper_helpers import CONTACT_FIELD_LIMITS

    expected_max = CONTACT_FIELD_LIMITS["first_name"]
    assert expected_max is not None
    assert len(record["first_name"]) == expected_max
    assert record["first_name"].endswith("…")
    assert any("truncated first_name" in rec.message for rec in caplog.records)


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
