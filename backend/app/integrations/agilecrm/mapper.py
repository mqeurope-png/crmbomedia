"""Translate AgileCRM contact payloads into our internal shape.

AgileCRM exposes contacts as `{id, properties: [...], tags: [...], ...}`
where each *property* is itself a `{name, value, type, subtype}` dict.
This module reduces that to a pair `(contact_record, external_ref_extras)`
the worker can pass through to SQLAlchemy directly.

The mapping is intentionally conservative:

- Marketing consent is **never** flipped to "granted" from an AgileCRM
  field; AgileCRM doesn't track GDPR consent the way the CRM does, so
  we default to `unknown` and let an operator (or the dedicated GDPR
  workflow) update it.
- Tags become a comma-separated string in `Contact.tags` (a real tags
  table is deferred to Sprint P.1.2). The raw array is preserved in
  `external_ref_extras["metadata"]["tags_raw"]` so nothing is lost.
- Company is captured as a hint (`company_name`) but never resolves to
  a `company_id` automatically — the operator may link companies from
  the UI later.
- Owner info (email, name, AgileCRM user id) is **not** mapped onto our
  `owner_user_id` because AgileCRM users are not our users; it's
  preserved verbatim in the external_reference metadata.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

ORIGIN_LABEL = "agilecrm"


def _properties_index(payload: dict[str, Any]) -> dict[str, Any]:
    """Return `{property_name: property_value}` flattened from the
    `properties: [{name, value, ...}]` array AgileCRM ships."""
    properties = payload.get("properties") or []
    flat: dict[str, Any] = {}
    if not isinstance(properties, list):
        return flat
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        name = prop.get("name") or prop.get("subtype")
        value = prop.get("value")
        if name and value is not None and name not in flat:
            flat[str(name)] = value
    return flat


def _stringify_tags(tags: Any) -> str:
    """AgileCRM ships tags as a list of strings or dicts with `{tag: ...}`.
    Reduce to a CSV string."""
    if not tags:
        return ""
    if isinstance(tags, str):
        return tags
    if not isinstance(tags, list):
        return ""
    flat: list[str] = []
    for tag in tags:
        if isinstance(tag, str):
            flat.append(tag)
        elif isinstance(tag, dict):
            value = tag.get("tag") or tag.get("name")
            if isinstance(value, str):
                flat.append(value)
    cleaned = [t.strip() for t in flat if t and t.strip()]
    return ",".join(sorted(set(cleaned)))


def _raw_tags(tags: Any) -> list[Any] | None:
    """Preserve the original tag shape (list of strings OR list of dicts)
    in the external_reference metadata so a future tags-table feature
    can reconstruct provenance."""
    if isinstance(tags, list):
        return list(tags)
    return None


def _to_datetime(value: Any) -> datetime | None:
    """AgileCRM ships timestamps as Unix epoch in **seconds** (sometimes
    inside JSON as a string). Accept both, return a tz-aware UTC datetime,
    None on failure."""
    if value is None or value in ("", 0, "0"):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_address(raw: Any) -> dict[str, str | None]:
    """AgileCRM packs the address into a JSON string under the
    `address` property: `{"country":"ES","city":"Madrid",...}`. Some
    accounts ship it as a plain dict already. Either way, normalise to
    a 4-field dict; any field can be None when the remote didn't fill
    it."""
    if not raw:
        return {
            "address_country": None,
            "address_country_name": None,
            "address_state": None,
            "address_city": None,
        }
    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
    if not isinstance(parsed, dict):
        return {
            "address_country": None,
            "address_country_name": None,
            "address_state": None,
            "address_city": None,
        }
    return {
        "address_country": _clean_str(parsed.get("country")),
        "address_country_name": _clean_str(parsed.get("countryname")),
        "address_state": _clean_str(parsed.get("state")),
        "address_city": _clean_str(parsed.get("city")),
    }


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _custom_properties(payload: dict[str, Any]) -> dict[str, Any]:
    """Collect every `properties[].type == "CUSTOM"` entry into a dict.
    The original AgileCRM property name is the key. Values stay as the
    remote sent them (strings, numbers, raw JSON-y blobs)."""
    properties = payload.get("properties") or []
    if not isinstance(properties, list):
        return {}
    out: dict[str, Any] = {}
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        if str(prop.get("type") or "").upper() != "CUSTOM":
            continue
        name = prop.get("name")
        if not isinstance(name, str) or not name:
            continue
        value = prop.get("value")
        if value is None:
            continue
        out[name] = value
    return out


def _lead_score(payload: dict[str, Any]) -> int | None:
    """AgileCRM exposes the score at the top level as `lead_score` /
    `star_value`. Some installations also bury it inside a property.
    Try them in that order."""
    for key in ("lead_score", "star_value"):
        raw = payload.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    props = _properties_index(payload)
    for key in ("lead_score", "score"):
        raw = props.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _owner_snapshot(payload: dict[str, Any]) -> dict[str, str] | None:
    """`owner: {id, name, email, ...}` from AgileCRM. We never resolve
    to a User row in our CRM (the AgileCRM operator is not us); we just
    snapshot the trio so the operator can see who owned the contact
    upstream."""
    owner = payload.get("owner")
    if not isinstance(owner, dict):
        return None
    keep = {}
    for key in ("id", "name", "email"):
        value = owner.get(key)
        if isinstance(value, str | int):
            keep[key] = str(value)
    return keep or None


def map_agilecrm_contact_to_internal(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Convert one AgileCRM contact dict into:

    1. `contact_record` — a flat dict ready to feed `Contact(**record)`
       or a SQLAlchemy update. Contains the canonical fields plus the
       parsed address, custom_fields (JSON-encoded) and lead_score.
       Also carries a `company_name` hint that the worker strips before
       persisting (used to set `external_references.account_label`).
    2. `external_ref_extras` — fields the worker copies into the
       matching `external_references` row: external timestamps,
       origin_detail (AgileCRM `source`), and a JSON-serialisable
       `metadata` dict (owner snapshot, raw tags).

    Returning a tuple keeps the caller honest about which fields land
    where, and avoids the contact record growing magic keys that need
    to be stripped before flushing to the ORM.
    """
    props = _properties_index(payload)

    first_name = (props.get("first_name") or "").strip()
    last_name_raw = props.get("last_name")
    last_name = last_name_raw.strip() if isinstance(last_name_raw, str) else None
    email_raw = props.get("email") or ""
    email = email_raw.strip().lower() if isinstance(email_raw, str) else ""

    phone_raw = props.get("phone")
    phone = phone_raw.strip() if isinstance(phone_raw, str) else None

    company_name_raw = props.get("company")
    company_name = company_name_raw.strip() if isinstance(company_name_raw, str) else None

    address_fields = _parse_address(props.get("address"))
    custom_fields = _custom_properties(payload)
    lead_score = _lead_score(payload)

    record: dict[str, Any] = {
        # AgileCRM sometimes omits first_name; the model requires it as
        # NOT NULL, so we fall back to the email local-part or a stable
        # placeholder so the row inserts cleanly.
        "first_name": first_name or _local_part(email) or "Sin nombre",
        "last_name": last_name or None,
        "email": email,
        "phone": phone or None,
        "origin": ORIGIN_LABEL,
        "tags": _stringify_tags(payload.get("tags")),
        "commercial_status": "new",
        "marketing_consent": "unknown",
        **address_fields,
        "lead_score": lead_score,
        "custom_fields": json.dumps(custom_fields, default=str) if custom_fields else None,
    }
    if company_name:
        # Hint stored under `account_label` of the external_reference
        # row downstream — the mapper just returns the name; the worker
        # decides where to put it.
        record["company_name"] = company_name

    # External reference extras.
    metadata: dict[str, Any] = {}
    owner = _owner_snapshot(payload)
    if owner:
        metadata["owner"] = owner
    raw_tags = _raw_tags(payload.get("tags"))
    if raw_tags is not None:
        metadata["tags_raw"] = raw_tags
    source = _clean_str(payload.get("source"))

    extras: dict[str, Any] = {
        "external_created_at": _to_datetime(payload.get("created_time")),
        "external_updated_at": _to_datetime(payload.get("updated_time")),
        "origin_detail": source,
        "metadata": metadata or None,
    }
    return record, extras


def _local_part(email: str) -> str:
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[0]


def agilecrm_external_id(payload: dict[str, Any]) -> str | None:
    """Return the canonical AgileCRM contact id as a string."""
    raw = payload.get("id")
    if raw is None:
        return None
    return str(raw)


def agilecrm_account_label(payload: dict[str, Any]) -> str | None:
    """A short, human-readable description we attach to
    `external_references.account_label` to help an operator scanning
    the audit log. We use AgileCRM's `lead_status` when present and
    fall back to the email."""
    props = _properties_index(payload)
    label_candidate = props.get("lead_status") or props.get("email")
    if isinstance(label_candidate, str):
        return label_candidate.strip() or None
    return None
