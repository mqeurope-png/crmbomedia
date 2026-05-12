"""Translate AgileCRM contact payloads into our internal shape.

AgileCRM exposes contacts as `{id, properties: [...], tags: [...], ...}`
where each *property* is itself a `{name, value, type, subtype}` dict.
This module reduces that to a flat dict that the worker can pass to
SQLAlchemy directly.

The mapping is intentionally conservative:

- Marketing consent is **never** flipped to "granted" from an AgileCRM
  field; AgileCRM doesn't track GDPR consent the way the CRM does, so
  we default to `unknown` and let an operator (or the dedicated GDPR
  workflow) update it.
- Tags become a comma-separated string in `Contact.tags` (a real tags
  table is deferred to Sprint P.1).
- Company is captured as a hint (`company_name`) but never resolves to
  a `company_id` automatically — the operator may link companies from
  the UI later.
"""
from __future__ import annotations

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


def map_agilecrm_contact_to_internal(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert one AgileCRM contact dict into a flat record ready to
    feed `Contact(**record)` or a SQLAlchemy update.

    Returns a dict — *not* a Pydantic model — so the worker can both
    insert and update without round-tripping through validation that
    would reject empty values. The caller picks the fields it wants.
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
    }
    if company_name:
        # Hint stored under `account_label` of the external_reference
        # row downstream — the mapper just returns the name; the worker
        # decides where to put it.
        record["company_name"] = company_name
    return record


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
