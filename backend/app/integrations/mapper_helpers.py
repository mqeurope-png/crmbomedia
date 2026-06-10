"""Shared mapper utilities for all integration connectors.

`truncate_safe(value, max_len, field_name, external_id)` is the
canonical safety net for varchar columns: the moment a payload from a
remote system carries a 240-char "first_name" (real production case
from a Brevo contact whose owner stuffed company + department + name +
suffix into the field), the bulk sync used to fail the entire
transaction with `Data too long for column 'first_name'`. Truncating
in the mapper keeps the row going while logging the offender so ops
can audit.
"""
from __future__ import annotations

import logging
from typing import Any

from app.models.crm import (
    Company,
    Contact,
    ExternalReference,
)

logger = logging.getLogger(__name__)

#: Truncation marker. Single-codepoint so it fits in the last
#: character of any String(n) column without an off-by-one.
TRUNCATION_SUFFIX = "…"


def _column_max_length(model: Any, attr: str) -> int | None:
    """Read the declared `String(n).length` from the ORM model.

    The mapper resolves max lengths dynamically from the model so a
    future migration that widens (or shrinks) a column keeps the
    truncate logic in lock-step automatically — no constant to update
    in two places."""
    column = getattr(model, attr, None)
    if column is None:
        return None
    type_obj = getattr(column.property.columns[0].type, "length", None)
    return int(type_obj) if type_obj else None


#: Pre-computed per-field max lengths. Resolved once at import-time;
#: the mapper helpers below read from this table.
CONTACT_FIELD_LIMITS: dict[str, int | None] = {
    "first_name": _column_max_length(Contact, "first_name"),
    "last_name": _column_max_length(Contact, "last_name"),
    "email": _column_max_length(Contact, "email"),
    "phone": _column_max_length(Contact, "phone"),
    "origin": _column_max_length(Contact, "origin"),
    "commercial_status": _column_max_length(Contact, "commercial_status"),
    "address_country": _column_max_length(Contact, "address_country"),
    "address_country_name": _column_max_length(Contact, "address_country_name"),
    "address_state": _column_max_length(Contact, "address_state"),
    "address_city": _column_max_length(Contact, "address_city"),
}

EXTERNAL_REFERENCE_FIELD_LIMITS: dict[str, int | None] = {
    "external_id": _column_max_length(ExternalReference, "external_id"),
    "account_label": _column_max_length(ExternalReference, "account_label"),
    "origin_detail": _column_max_length(ExternalReference, "origin_detail"),
}

COMPANY_FIELD_LIMITS: dict[str, int | None] = {
    "name": _column_max_length(Company, "name"),
    "tax_id": _column_max_length(Company, "tax_id"),
    "website": _column_max_length(Company, "website"),
}


def truncate_safe(
    value: Any,
    max_len: int | None,
    *,
    field_name: str,
    external_id: object,
    connector: str,
) -> Any:
    """Return `value` unless it's a too-long string, in which case
    truncate to `max_len - 1` and append `…`. None/empty/non-str
    pass through. Every truncation emits a warning carrying the
    connector + offending external id so an operator can audit."""
    if value is None or not isinstance(value, str) or not max_len:
        return value
    if len(value) <= max_len:
        return value
    truncated = value[: max_len - 1] + TRUNCATION_SUFFIX
    logger.warning(
        "%s.mapper truncated %s for external_id=%r from %d to %d chars",
        connector,
        field_name,
        external_id,
        len(value),
        max_len,
    )
    return truncated


def apply_contact_field_limits(
    record: dict[str, Any], *, connector: str, external_id: object
) -> None:
    """In-place pass over a Contact record dict — every string column
    referenced in `CONTACT_FIELD_LIMITS` gets bounded. Idempotent."""
    for key, limit in CONTACT_FIELD_LIMITS.items():
        if key in record:
            record[key] = truncate_safe(
                record[key],
                limit,
                field_name=key,
                external_id=external_id,
                connector=connector,
            )
