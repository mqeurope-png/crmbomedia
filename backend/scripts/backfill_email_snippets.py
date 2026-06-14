"""Recompute stored email snippets that pre-date the HTML-strip fix.

Sends authored in TinyMCE ship with `body_text=null` and a `<style>`
reset block inline. Until the snippet helpers learned to strip block
contents, the stored `EmailMessage.snippet` (and the mirrored
`ActivityEvent.body` / `metadata.snippet`) captured raw CSS source
instead of the operator's first sentence. The widget + thread fixes
only affect FUTURE sends; this script repairs the rows already in the
database.

What it touches:
- `email_messages.snippet` for every outbound message — recomputed
  from `body_html` / `body_text` via the (fixed) extractor.
- `activity_events` of type `email.sent_from_crm` — `body` and the
  `snippet` key inside `metadata_json`, matched to the message via
  `metadata.message_id`.

Usage:
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_email_snippets
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_email_snippets --dry-run

Commits in batches so a partial run still makes progress. A row whose
snippet already looks clean (no `<` and no `{...}` CSS signature) is
left untouched so re-running is cheap and idempotent.
"""
from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.email_templates.services import extract_text_from_html
from app.models.crm import ActivityEvent, EmailDirection, EmailMessage

log = logging.getLogger("backfill_email_snippets")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def _looks_dirty(snippet: str | None) -> bool:
    """True when the snippet still carries HTML / CSS we should clean.
    Conservative: a `<` or a `{…}` CSS-rule signature is enough."""
    if not snippet:
        return False
    if "<" in snippet:
        return True
    return "{" in snippet and "}" in snippet


def _clean_snippet(message: EmailMessage) -> str | None:
    if message.body_text and message.body_text.strip():
        flat = " ".join(message.body_text.split()).strip()
        return flat[:200] or None
    if message.body_html:
        clean = extract_text_from_html(message.body_html)
        if clean:
            return clean[:200]
    return None


def backfill(*, dry_run: bool, batch: int = 200) -> dict[str, int]:
    counts = {"messages_scanned": 0, "messages_fixed": 0, "events_fixed": 0}
    engine = get_engine()
    with Session(engine) as session:
        messages = list(
            session.scalars(
                select(EmailMessage).where(
                    EmailMessage.direction == EmailDirection.OUTBOUND
                )
            )
        )
        counts["messages_scanned"] = len(messages)
        # message_id -> fresh snippet, for the activity-event pass.
        fresh_by_message: dict[str, str | None] = {}
        pending = 0
        for msg in messages:
            new_snippet = _clean_snippet(msg)
            fresh_by_message[msg.id] = new_snippet
            if not _looks_dirty(msg.snippet):
                continue
            if new_snippet == msg.snippet:
                continue
            log.info(
                "msg %s: %r -> %r",
                msg.id,
                (msg.snippet or "")[:48],
                (new_snippet or "")[:48],
            )
            if not dry_run:
                msg.snippet = new_snippet
            counts["messages_fixed"] += 1
            pending += 1
            if not dry_run and pending >= batch:
                session.commit()
                pending = 0
        if not dry_run and pending:
            session.commit()

        # Activity-event mirror.
        events = list(
            session.scalars(
                select(ActivityEvent).where(
                    ActivityEvent.event_type == "email.sent_from_crm"
                )
            )
        )
        pending = 0
        for event in events:
            try:
                meta: dict[str, Any] = json.loads(event.metadata_json or "{}")
            except (TypeError, ValueError):
                meta = {}
            message_id = meta.get("message_id")
            if not message_id or message_id not in fresh_by_message:
                continue
            fresh = fresh_by_message[message_id]
            body_dirty = _looks_dirty(event.body)
            meta_dirty = _looks_dirty(meta.get("snippet"))
            if not body_dirty and not meta_dirty:
                continue
            if not dry_run:
                if body_dirty:
                    event.body = fresh
                if meta_dirty:
                    meta["snippet"] = fresh or ""
                    event.metadata_json = json.dumps(meta, default=str)
            counts["events_fixed"] += 1
            pending += 1
            if not dry_run and pending >= batch:
                session.commit()
                pending = 0
        if not dry_run and pending:
            session.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    counts = backfill(dry_run=args.dry_run)
    log.info(
        "backfill summary: messages_scanned=%d messages_fixed=%d "
        "events_fixed=%d dry_run=%s",
        counts["messages_scanned"],
        counts["messages_fixed"],
        counts["events_fixed"],
        args.dry_run,
    )
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
