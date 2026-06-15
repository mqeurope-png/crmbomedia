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
import logging
from datetime import UTC, datetime
from typing import Any

from app.integrations.country_codes import normalize_country
from app.integrations.mapper_helpers import apply_contact_field_limits

logger = logging.getLogger(__name__)

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


def _tag_names(tags: Any) -> list[str]:
    """Return a list of deduplicated, stripped, case-insensitive tag
    names from the AgileCRM payload — the feed for the M:N
    `contact_tags` upserter. Original casing is preserved (so the
    first occurrence of "VIP" is what we display) but a follow-up
    "vip" in the same payload collapses to a single entry."""
    if not tags:
        return []
    if isinstance(tags, str):
        candidates: list[str] = [t for t in tags.split(",")]
    elif isinstance(tags, list):
        candidates = []
        for raw in tags:
            if isinstance(raw, str):
                candidates.append(raw)
            elif isinstance(raw, dict):
                value = raw.get("tag") or raw.get("name")
                if isinstance(value, str):
                    candidates.append(value)
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = candidate.strip()
        if not cleaned:
            continue
        normalized = cleaned.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(cleaned)
    return out


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


_EMPTY_ADDRESS: dict[str, str | None] = {
    "address_country": None,
    "address_country_name": None,
    "address_state": None,
    "address_city": None,
    "address_line": None,
    "address_postal_code": None,
}


def extract_agilecrm_secondary_channels(
    payload: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, str]]:
    """Sprint Empresas — sub-PR 3/4. Walk every `properties[]` entry
    and gather:

    - Secondary phones: `name=='phone'` with subtypes `work`,
      `home`, `mobile`, `main`, `home-fax`, `work-fax`, `other`
      (8 AgileCRM variants).
    - Secondary emails: `name=='email'` with subtypes `personal`,
      `work`, plus any non-default.
    - Socials: `twitter`, `facebook`, plus the niche set
      (`skype`, `xing`, `blog`, `googleplus`, `flickr`, `github`,
      `youtube`) — returned as a flat `{label: url}` dict for the
      `social_profiles_json` bucket.

    The contact's canonical `phone` / `email` (the property whose
    subtype is `default` or empty) is NOT included in the
    secondary lists — those still go through the main record.
    """
    properties = payload.get("properties") or []
    if not isinstance(properties, list):
        return [], [], {}

    phones: list[dict[str, str]] = []
    seen_phones: set[str] = set()
    emails: list[dict[str, str]] = []
    seen_emails: set[str] = set()
    socials: dict[str, str] = {}

    default_phone_taken = False
    default_email_taken = False

    for prop in properties:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name") or "").lower()
        subtype = str(prop.get("subtype") or "").lower()
        raw_value = prop.get("value")
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if not value:
            continue

        if name == "phone":
            digits = "".join(c for c in value if c.isdigit() or c == "+")
            # The first "default" / empty-subtype phone we see goes
            # to the canonical Contact.phone column — every later
            # variant becomes a secondary row.
            if subtype in ("", "default") and not default_phone_taken:
                default_phone_taken = True
                continue
            if not digits or digits in seen_phones:
                continue
            seen_phones.add(digits)
            phones.append(
                {
                    "label": subtype or "other",
                    "number": value,
                    "source": "agilecrm",
                }
            )
        elif name == "email":
            text = value.lower()
            if subtype in ("", "default") and not default_email_taken:
                default_email_taken = True
                continue
            if "@" not in text or text in seen_emails:
                continue
            seen_emails.add(text)
            emails.append(
                {
                    "label": subtype or "other",
                    "email": text,
                    "source": "agilecrm",
                }
            )
        elif name in (
            "twitter",
            "facebook",
            "skype",
            "xing",
            "blog",
            "googleplus",
            "google+",
            "flickr",
            "github",
            "youtube",
            "instagram",
        ):
            socials[name] = value

    return phones, emails, socials


def _parse_address(raw: Any) -> dict[str, str | None]:
    """AgileCRM packs the address into a JSON string under the
    `address` property: `{"country":"ES","city":"Madrid",...}`. Some
    accounts ship it as a plain dict already. Either way, normalise to
    a 6-field dict (Sprint Empresas v2 added `address_line` +
    `address_postal_code` to the surface); any field can be None
    when the remote didn't fill it."""
    if not raw:
        return dict(_EMPTY_ADDRESS)
    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
    if not isinstance(parsed, dict):
        return dict(_EMPTY_ADDRESS)
    # AgileCRM ships either ISO Alpha-2 ("ES") or a localised name
    # ("España" / "Spain") under `country`. Normalise both into our
    # canonical pair (ISO under `address_country`, display name under
    # `address_country_name`).
    raw_country = _clean_str(parsed.get("country"))
    raw_country_name = _clean_str(parsed.get("countryname"))
    iso, display = normalize_country(raw_country or raw_country_name)
    return {
        "address_country": iso,
        "address_country_name": display or raw_country_name,
        "address_state": _clean_str(parsed.get("state")),
        "address_city": _clean_str(parsed.get("city")),
        # Sprint Empresas — sub-PR 2/4. AgileCRM's address dict
        # carries the street line under `address` and the postal
        # code under `zip` (occasionally `postcode`).
        "address_line": _clean_str(parsed.get("address")),
        "address_postal_code": _clean_str(
            parsed.get("zip") or parsed.get("postcode")
        ),
    }


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _custom_properties(payload: dict[str, Any]) -> dict[str, Any]:
    """Collect every `properties[].type == "CUSTOM"` entry into a dict.
    The original AgileCRM property name is the key. Values stay as the
    remote sent them (strings, numbers, raw JSON-y blobs).

    Sprint Empresas — sub-PR 2 fix: only the business-curated
    whitelist (see `brevo.mapper.CUSTOM_FIELDS_WHITELIST`) lands in
    the contact's `custom_fields` JSON. Everything else is dropped
    on import — Agile installations carry the same housekeeping
    noise the Brevo accounts do (sib_contact_owner, ETIQUETA,
    secondary phones), and we don't want it leaking into the ficha.
    """
    from app.integrations.brevo.mapper import (  # noqa: PLC0415
        CUSTOM_FIELDS_WHITELIST,
    )

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
        if name.upper() not in CUSTOM_FIELDS_WHITELIST:
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
    raw_email = email_raw.strip().lower() if isinstance(email_raw, str) else ""
    email = _sanitize_email(
        raw_email, external_id=payload.get("id"), payload=payload
    )

    phone_raw = props.get("phone")
    phone_candidate = phone_raw.strip() if isinstance(phone_raw, str) else None
    phone = _sanitize_phone(phone_candidate, external_id=payload.get("id"))

    company_name_raw = props.get("company")
    company_name = company_name_raw.strip() if isinstance(company_name_raw, str) else None

    address_fields = _parse_address(props.get("address"))
    custom_fields = _custom_properties(payload)
    lead_score = _lead_score(payload)

    # Sprint Empresas — sub-PR 2/4. Lift AgileCRM's professional
    # attributes off the flat properties index. The property names
    # are the AgileCRM defaults; if an account renamed them they
    # land in custom_fields just like any other unknown column.
    def _prop(*keys: str) -> str | None:
        for k in keys:
            raw = props.get(k)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return None

    job_title = _prop("title", "Title", "job_title", "Job Title")
    linkedin_url = _prop("linkedin", "Linkedin", "LinkedIn", "linkedin_url")
    personal_website = _prop("website", "Website", "url", "URL")
    # The properties index also has `address` as the JSON dict we
    # already parsed; only the plain-text `street` / `street_address`
    # variants are relevant here as a fallback when the dict was
    # empty.
    if address_fields.get("address_line") is None:
        address_fields["address_line"] = _prop(
            "street", "street_address", "address_line"
        )

    # Sprint Empresas — sub-PR 3/4. Pull socials off the
    # properties list; twitter + facebook are pinned to columns,
    # everything else lands in the JSON bucket.
    _, _, socials_map = extract_agilecrm_secondary_channels(payload)
    twitter_url = socials_map.pop("twitter", None)
    facebook_url = socials_map.pop("facebook", None)
    social_profiles_json = (
        json.dumps(socials_map, default=str) if socials_map else None
    )

    record: dict[str, Any] = {
        # AgileCRM sometimes omits first_name; the model requires it as
        # NOT NULL, so we fall back to the email local-part or a stable
        # placeholder so the row inserts cleanly.
        "first_name": first_name or _local_part(email or "") or "Sin nombre",
        "last_name": last_name or None,
        "email": email,
        # `is_email_valid` doubles as the audit flag for rows that
        # carry a usable address vs. those we nulled because the
        # validator rejected them.
        "is_email_valid": bool(email),
        "phone": phone or None,
        "origin": ORIGIN_LABEL,
        "commercial_status": "new",
        "marketing_consent": "unknown",
        **address_fields,
        "job_title": job_title,
        "linkedin_url": linkedin_url,
        "personal_website": personal_website,
        "twitter_url": twitter_url,
        "facebook_url": facebook_url,
        "social_profiles_json": social_profiles_json,
        "lead_score": lead_score,
        "custom_fields": json.dumps(custom_fields, default=str) if custom_fields else None,
    }
    # The mapper writes tags into the M:N `tags` table via the job,
    # NOT into the legacy `contacts.tags` CSV column (Sprint P.1
    # ampliado). `tag_names` is a magic key the worker strips before
    # `Contact(**record)` and feeds to the M:N delta helper.
    record["tag_names"] = _tag_names(payload.get("tags"))
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

    external_created_at = _to_datetime(payload.get("created_time"))
    external_updated_at = _to_datetime(payload.get("updated_time"))
    extras: dict[str, Any] = {
        "external_created_at": external_created_at,
        "external_updated_at": external_updated_at,
        "origin_detail": source,
        "metadata": metadata or None,
    }
    # Truncate every varchar to the column's declared length so a
    # 240-char first_name (real case from a Brevo-then-imported-to-
    # AgileCRM contact) never aborts the bulk sync transaction.
    apply_contact_field_limits(
        record, connector="agilecrm", external_id=payload.get("id")
    )
    # Promote the source-system dates onto the contact record too. The
    # worker applies the oldest-creation / newest-update merge policy
    # (see `contact_merge`); when AgileCRM has no `created_time` the
    # value is None and the merge leaves the column untouched.
    record["created_at_external"] = external_created_at
    record["updated_at_external"] = external_updated_at
    return record, extras


def _sanitize_email(
    raw: str, *, external_id: object, payload: dict[str, Any]
) -> str | None:
    """Return a normalised email or `None` if the raw value can't be
    parsed. Logs a warning with the AgileCRM external id + account
    label so the operator can audit later. Never raises — a junk
    email must not stop the rest of the contact mapping."""
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
            "agilecrm.mapper email malformed; storing None: external_id=%r raw=%r reason=%s",
            external_id,
            raw,
            exc,
        )
        return None


def _sanitize_phone(raw: str | None, *, external_id: object) -> str | None:
    """Drop obvious garbage. AgileCRM's phone column is free-form, so
    operators have shoved emails, dates and even short paragraphs in
    there over the years. Past 30 characters or with zero digits the
    value is functionally unusable downstream — we surface None and
    log the row id so the audit trail catches it."""
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    if len(candidate) > 30 or not any(ch.isdigit() for ch in candidate):
        logger.warning(
            "agilecrm.mapper phone looks malformed; storing None: external_id=%r raw=%r",
            external_id,
            candidate,
        )
        return None
    return candidate


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


# ---------------------------------------------------------------------------
# Notes / Tasks / Activities
# ---------------------------------------------------------------------------
#
# All three mappers return *plain dicts* shaped like the SQLAlchemy
# constructors of `Note`, `Task` and `ActivityEvent`. The job layer
# decides what to do with them; that keeps the mapper pure (no session
# coupling) and the tests cheap.


def map_agilecrm_note_to_internal(
    payload: dict[str, Any],
    *,
    contact_id: str,
    account_id: str,
) -> dict[str, Any] | None:
    """Translate one AgileCRM note dict into a `Note(**record)` payload.

    AgileCRM notes typically look like::

        {"id": 123, "subject": "Llamada", "description": "Habló de X",
         "created_time": 1750000000,
         "domainOwner": {"name": "Operador", "email": "ag@x.com", "pic": "..."}}

    Real-tenant payloads carry the author under `domainOwner` (the
    AgileCRM user that wrote the note). Older fixtures used `owner` —
    we accept both so existing tests keep passing while production
    rows pick up the real author.

    We collapse subject + description into one body so the existing
    `Note.body` column carries everything; the original parts are kept
    on `metadata` via the worker. Returns `None` when the payload has no
    usable text (so we never persist an empty note)."""
    if not isinstance(payload, dict):
        return None
    subject = _clean_str(payload.get("subject"))
    description = _clean_str(payload.get("description") or payload.get("note"))
    body_parts = [part for part in (subject, description) if part]
    if not body_parts:
        return None
    body = "\n\n".join(body_parts)
    # `domainOwner` is the production shape; fall back to `owner` for
    # legacy fixtures. Either way we keep `author_user_id=None` because
    # an AgileCRM user is not one of our `users` rows — the operator
    # who triggered the sync is recorded on the audit event, not on
    # the imported note.
    raw_owner = payload.get("domainOwner") or payload.get("owner")
    owner = raw_owner if isinstance(raw_owner, dict) else {}
    return {
        "contact_id": contact_id,
        "body": body,
        "external_system": ORIGIN_LABEL,
        "external_account_id": account_id,
        "external_id": _external_id(payload),
        "external_author_email": _clean_str(owner.get("email")) if owner else None,
        "external_author_name": _clean_str(owner.get("name")) if owner else None,
        "external_created_at": _to_datetime(payload.get("created_time")),
    }


def map_agilecrm_task_to_internal(
    payload: dict[str, Any],
    *,
    contact_id: str,
    account_id: str,
) -> dict[str, Any] | None:
    """Translate one AgileCRM task dict into a `Task(**record)` payload.

    AgileCRM tasks ship `subject` as the title, `status ∈ {YET_TO_START,
    IN_PROGRESS, COMPLETED}` and `due` as Unix seconds (the wider epoch
    in millis for some installations — `_to_datetime` accepts both via
    its float conversion).

    Status maps: COMPLETED → done; everything else → open (we never
    flip a remote-in-progress task to "cancelled" automatically). The
    sync job upserts by `(system, account_id, external_id)` so a remote
    status change on re-sync overwrites the local row."""
    if not isinstance(payload, dict):
        return None
    title_raw = payload.get("subject") or payload.get("name") or payload.get("title")
    title = _clean_str(title_raw)
    if not title:
        return None
    status_raw = str(payload.get("status") or "").upper()
    if status_raw in {"COMPLETED", "DONE"}:
        status = "done"
    elif status_raw == "IN_PROGRESS":
        status = "in_progress"
    else:
        status = "pending"
    return {
        "contact_id": contact_id,
        "title": title,
        "status": status,
        "due_at": _to_datetime(payload.get("due")),
        "external_system": ORIGIN_LABEL,
        "external_account_id": account_id,
        "external_id": _external_id(payload),
        "external_created_at": _to_datetime(payload.get("created_time")),
        "external_updated_at": _to_datetime(
            payload.get("updated_time") or payload.get("entity_updated_time")
        ),
    }


def map_agilecrm_event_to_internal(
    payload: dict[str, Any],
    *,
    contact_id: str,
    account_id: str,
) -> dict[str, Any] | None:
    """Translate one AgileCRM contact event (timeline row) dict into an
    `ActivityEvent(**record)` payload.

    AgileCRM's `/contacts/{id}/events` ships rows with `type` /
    `subject` / `body` / `created_time` plus connector-specific extras
    (campaign_id, email_id, …). The mapper surfaces the type uppercased,
    promotes `subject` to subject, `body` to body, and stuffs every
    other field into the JSON `metadata` blob so the operator can drill
    in without a re-sync.

    Backwards-compat: an older iteration shipped the same shape under
    the keys `activity_type` / `label` / `description` from the
    (broken) `/activities/contact/{id}` endpoint; both spellings are
    accepted so tests and fixtures keep working.

    Returns `None` when the payload has neither `time` nor
    `created_time` — the `activity_events.occurred_at` column is NOT
    NULL."""
    if not isinstance(payload, dict):
        return None
    occurred = _to_datetime(payload.get("time") or payload.get("created_time"))
    if occurred is None:
        return None
    event_type = (
        _clean_str(payload.get("type"))
        or _clean_str(payload.get("activity_type"))
        or "UNKNOWN"
    ).upper()
    subject = _clean_str(payload.get("subject") or payload.get("label"))
    body = _clean_str(payload.get("body") or payload.get("description"))
    metadata = {
        key: payload.get(key)
        for key in payload
        if key
        not in {
            "id",
            "time",
            "created_time",
            "type",
            "activity_type",
            "subject",
            "label",
            "body",
            "description",
        }
        and payload.get(key) is not None
    }
    return {
        "contact_id": contact_id,
        "system": ORIGIN_LABEL,
        "account_id": account_id,
        "external_id": _external_id(payload),
        "event_type": event_type,
        "subject": subject,
        "body": body,
        "occurred_at": occurred,
        "metadata_json": (
            json.dumps(metadata, default=str) if metadata else None
        ),
    }


# Backwards-compat alias: the function was previously named
# `map_agilecrm_activity_to_internal`. Existing imports keep working
# until every caller has switched to the new name (CI gate enforces
# the rename within this repo).
map_agilecrm_activity_to_internal = map_agilecrm_event_to_internal


def _external_id(payload: dict[str, Any]) -> str | None:
    raw = payload.get("id")
    if raw is None:
        return None
    return str(raw)
