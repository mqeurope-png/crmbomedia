"""Translate Brevo contact payloads ⇄ internal `Contact` shape.

Brevo contact payload (GET /contacts/{id}):

    {
      "id": 42,
      "email": "ana@example.com",
      "emailBlacklisted": false,
      "smsBlacklisted": false,
      "createdAt": "2024-01-05T10:30:00.000+01:00",
      "modifiedAt": "2026-05-01T08:00:00.000+02:00",
      "attributes": {"NOMBRE": "Ana", "APELLIDOS": "García", "SMS": "+34600100100", ...},
      "listIds": [4, 7]
    }

Same defensive posture as the AgileCRM mapper: a malformed email or a
garbage phone never aborts the row — they collapse to `None` plus a
warning carrying the external id so ops can audit.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.integrations.mapper_helpers import (
    EXTERNAL_REFERENCE_FIELD_LIMITS,
    apply_contact_field_limits,
    truncate_safe,
)

logger = logging.getLogger(__name__)

ORIGIN_LABEL = "brevo"

#: Brevo attribute names (upper-case convention) → native Contact
#: columns. Anything not in this map lands in `custom_fields` JSON.
NATIVE_ATTRIBUTE_MAP = {
    "NOMBRE": "first_name",
    "FIRSTNAME": "first_name",
    "APELLIDOS": "last_name",
    "LASTNAME": "last_name",
    "SMS": "phone",
    "PHONE": "phone",
    "ESTADO_COMERCIAL": "commercial_status",
    "PAIS": "address_country",
    "COUNTRY": "address_country",
    "CIUDAD": "address_city",
    "CITY": "address_city",
    "LEAD_SCORE": "lead_score",
}

#: Prefix for the auto-tags that mirror Brevo list membership so the
#: operator can filter by list inside the CRM.
LIST_TAG_PREFIX = "brevo-list:"


def brevo_external_id(payload: dict[str, Any]) -> str:
    value = payload.get("id")
    return str(value) if value is not None else ""


def map_brevo_contact_to_internal(
    payload: dict[str, Any],
    account_id: str,
    *,
    list_names: dict[int, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return `(contact_record, external_ref_extras)` — mirror of the
    AgileCRM mapper contract so the upsert helper stays shared in
    spirit.

    `list_names` maps Brevo list id → list name so membership becomes
    `brevo-list:<name>` auto-tags; pass the cached lookup from the
    sync job to avoid one API call per contact."""
    attributes = payload.get("attributes") or {}
    if not isinstance(attributes, dict):
        attributes = {}

    raw_email = (payload.get("email") or "").strip().lower()
    email = _sanitize_email(raw_email, external_id=payload.get("id"))

    native: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    for key, value in attributes.items():
        target = NATIVE_ATTRIBUTE_MAP.get(str(key).upper())
        if target and value not in (None, ""):
            native[target] = value
        elif value not in (None, ""):
            custom[str(key)] = value

    phone = _sanitize_phone(
        str(native.get("phone")) if native.get("phone") else None,
        external_id=payload.get("id"),
    )

    lead_score = native.get("lead_score")
    if lead_score is not None:
        try:
            lead_score = int(float(lead_score))
        except (TypeError, ValueError):
            lead_score = None

    first_name = str(native.get("first_name") or "").strip()
    # Brevo treats list membership as the opt-in: every contact sitting
    # in any list (i.e. anything we pull) is suscribed unless explicitly
    # blacklisted. The post-deploy reality check (PR #51) imported 18.8k
    # rows and the previous mapping left 17.7k stuck on `unknown`, which
    # then leaked into every consent filter and segment. With Brevo
    # there is no intermediate state to preserve — either the address
    # is opted in or it isn't.
    #
    # `withdrawn` (asked for by the follow-up spec) would need a new
    # value in `ConsentStatus`; the enum across model/filters/segments/
    # IA context/UI defines `unsubscribed` for the exact semantic, so
    # this keeps the same trade-off documented in the previous sprint
    # (`docs/integrations-brevo.md` § "ConsentStatus deviation") rather
    # than rippling a parallel value through every layer.
    is_blacklisted = bool(
        payload.get("emailBlacklisted") or payload.get("smsBlacklisted")
    )

    record: dict[str, Any] = {
        "first_name": first_name or _local_part(email or "") or "Sin nombre",
        "last_name": str(native.get("last_name") or "").strip() or None,
        "email": email,
        "is_email_valid": bool(email) and not payload.get("emailBlacklisted", False),
        "phone": phone,
        "origin": ORIGIN_LABEL,
        "commercial_status": str(native.get("commercial_status") or "new"),
        "marketing_consent": "unsubscribed" if is_blacklisted else "granted",
        "address_country": str(native.get("address_country") or "").strip() or None,
        "address_city": str(native.get("address_city") or "").strip() or None,
        "lead_score": lead_score,
        "custom_fields": json.dumps(custom, default=str) if custom else None,
    }
    # Last-mile safety net: truncate every varchar to the column's
    # declared length so a 240-char name never aborts the whole sync.
    apply_contact_field_limits(
        record, connector="brevo", external_id=payload.get("id")
    )

    # List membership → auto-tags. The job's tag-delta helper removes
    # stale `brevo:<account>`-sourced assignments when a contact leaves
    # a list in Brevo.
    list_ids = payload.get("listIds") or []
    names = list_names or {}
    record["tag_names"] = [
        f"{LIST_TAG_PREFIX}{names.get(int(lid), lid)}"
        for lid in list_ids
        if lid is not None
    ]

    external_created_at = _parse_dt(payload.get("createdAt"))
    external_updated_at = _parse_dt(payload.get("modifiedAt"))
    ref_extras: dict[str, Any] = {
        "external_created_at": external_created_at,
        "external_updated_at": external_updated_at,
        "origin_detail": truncate_safe(
            "brevo",
            EXTERNAL_REFERENCE_FIELD_LIMITS.get("origin_detail"),
            field_name="origin_detail",
            external_id=payload.get("id"),
            connector="brevo",
        ),
        "metadata": {
            "list_ids": list_ids,
            "email_blacklisted": bool(payload.get("emailBlacklisted", False)),
            "sms_blacklisted": bool(payload.get("smsBlacklisted", False)),
        },
    }
    # Promote the source-system dates onto the contact record. The
    # worker merges them (oldest creation, newest update); a payload
    # without `createdAt`/`modifiedAt` yields None and the merge is a
    # no-op for that column.
    record["created_at_external"] = external_created_at
    record["updated_at_external"] = external_updated_at
    return record, ref_extras


def map_internal_contact_to_brevo(contact: Any) -> dict[str, Any]:
    """Inverse direction for the push engine. Only fields Brevo can
    store land in `attributes`; the email is the upsert key."""
    attributes: dict[str, Any] = {}
    if contact.first_name:
        attributes["NOMBRE"] = contact.first_name
    if contact.last_name:
        attributes["APELLIDOS"] = contact.last_name
    if contact.phone:
        attributes["SMS"] = contact.phone
    if contact.commercial_status:
        attributes["ESTADO_COMERCIAL"] = contact.commercial_status
    if contact.address_country:
        attributes["PAIS"] = contact.address_country
    if contact.lead_score is not None:
        attributes["LEAD_SCORE"] = contact.lead_score
    return {
        "email": contact.email,
        "attributes": attributes,
        "updateEnabled": False,
    }


# ---------------------------------------------------------------------------
# sanitisers (same contract as the AgileCRM mapper)
# ---------------------------------------------------------------------------


def _sanitize_email(raw: str, *, external_id: object) -> str | None:
    if not raw:
        return None
    try:
        from email_validator import (  # noqa: PLC0415
            EmailNotValidError,
            validate_email,
        )
    except ImportError:  # pragma: no cover - ships with pydantic[email]
        return raw
    try:
        return validate_email(raw, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        logger.warning(
            "brevo.mapper email malformed; storing None: external_id=%r raw=%r reason=%s",
            external_id,
            raw,
            exc,
        )
        return None


def _sanitize_phone(raw: str | None, *, external_id: object) -> str | None:
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    if len(candidate) > 30 or not any(ch.isdigit() for ch in candidate):
        logger.warning(
            "brevo.mapper phone looks malformed; storing None: external_id=%r raw=%r",
            external_id,
            candidate,
        )
        return None
    return candidate


def _local_part(email: str) -> str:
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[0]


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
