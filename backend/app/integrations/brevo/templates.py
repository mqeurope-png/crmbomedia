"""Brevo email-template cache maintenance.

The `/marketing/templates` UI reads from `brevo_templates_cache` so
the grid renders instantly; this module owns the cache lifecycle:

- `refresh_templates_cache` — pull the list from Brevo and upsert
  rows (html_content is NOT fetched here; the list endpoint of Brevo
  doesn't return it and the UI lazy-loads it per template).
- `ensure_template_html` — fetch + persist the HTML of one template
  on first detail open.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.brevo.client import BrevoClient
from app.models.brevo import BrevoTemplateCache

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def upsert_template_row(
    session: Session,
    *,
    account_id: str,
    payload: dict[str, Any],
    html_content: str | None = None,
) -> BrevoTemplateCache:
    """Insert/update one cache row from a Brevo template payload."""
    template_id = int(payload.get("id"))
    row = session.scalar(
        select(BrevoTemplateCache).where(
            BrevoTemplateCache.brevo_account_id == account_id,
            BrevoTemplateCache.brevo_template_id == template_id,
        )
    )
    sender = payload.get("sender") or {}
    if row is None:
        row = BrevoTemplateCache(
            brevo_account_id=account_id,
            brevo_template_id=template_id,
            name=str(payload.get("name") or payload.get("templateName") or ""),
            cached_at=datetime.now(UTC),
        )
        session.add(row)
    row.name = str(payload.get("name") or payload.get("templateName") or row.name)
    row.subject = payload.get("subject")
    row.is_active = bool(payload.get("isActive", True))
    row.tag = payload.get("tag") or None
    row.sender_name = sender.get("name")
    row.sender_email = sender.get("email")
    row.created_at_brevo = _parse_dt(payload.get("createdAt"))
    row.modified_at_brevo = _parse_dt(payload.get("modifiedAt"))
    row.cached_at = datetime.now(UTC)
    if html_content is not None:
        row.html_content = html_content
    elif payload.get("htmlContent"):
        row.html_content = str(payload["htmlContent"])
    session.flush()
    return row


async def refresh_templates_cache(
    session: Session, account_id: str
) -> int:
    """Pull every template page from Brevo and upsert the cache.
    Returns the number of rows touched. Rows that disappeared from
    Brevo are deleted locally (the cache mirrors, never owns)."""
    seen_ids: set[int] = set()
    touched = 0
    async with BrevoClient(session, account_id) as client:
        offset = 0
        while True:
            body = await client.list_email_templates(limit=50, offset=offset)
            templates = body.get("templates") or []
            if not templates:
                break
            for payload in templates:
                if payload.get("id") is None:
                    continue
                upsert_template_row(
                    session, account_id=account_id, payload=payload
                )
                seen_ids.add(int(payload["id"]))
                touched += 1
            if len(templates) < 50:
                break
            offset += 50

    stale = session.scalars(
        select(BrevoTemplateCache).where(
            BrevoTemplateCache.brevo_account_id == account_id,
            ~BrevoTemplateCache.brevo_template_id.in_(seen_ids)
            if seen_ids
            else True,  # no templates remotely → everything local is stale
        )
    )
    for row in stale:
        session.delete(row)
    session.flush()
    return touched


async def ensure_template_html(
    session: Session, row: BrevoTemplateCache
) -> BrevoTemplateCache:
    """Lazy-load the HTML body on first detail open."""
    if row.html_content is not None:
        return row
    async with BrevoClient(session, row.brevo_account_id) as client:
        payload = await client.get_email_template(row.brevo_template_id)
    upsert_template_row(
        session,
        account_id=row.brevo_account_id,
        payload=payload,
        html_content=str(payload.get("htmlContent") or ""),
    )
    session.refresh(row)
    return row
