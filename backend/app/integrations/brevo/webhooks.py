"""Brevo webhook processing.

The HTTP route (`POST /api/webhooks/brevo`) validates the signature,
dedupes by event id and enqueues to the `brevo:webhook_process` RQ
queue; this module owns the actual materialisation:

- every supported event becomes an `activity_events` row on the
  contact resolved by email;
- `unsubscribe` flips `marketing_consent` → `unsubscribed`;
- `hard_bounce` flips `is_email_valid` → False;
- `spam` does both;
- an email with no matching CRM contact is logged + discarded —
  webhooks NEVER create contacts (transactional sends to strangers
  must not pollute the base).

Deviation from the sprint text: it asks for consent `withdrawn`, but
the `ConsentStatus` enum (model + filters + segments + frontend)
defines `unsubscribed` for exactly this semantic. Introducing a new
enum value would ripple through every layer for zero behavioural
gain, so `unsubscribed` is used.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.models.brevo import WebhookEventSeen
from app.models.crm import ActivityEvent, Contact

logger = logging.getLogger(__name__)

#: Brevo event name → internal activity_events.event_type.
EVENT_TYPE_MAP = {
    "sent": "email.sent",
    "delivered": "email.delivered",
    "opened": "email.opened",
    "unique_opened": "email.opened",
    "click": "email.clicked",
    "clicked": "email.clicked",
    "hard_bounce": "email.bounced_hard",
    "soft_bounce": "email.bounced_soft",
    "unsubscribe": "email.unsubscribed",
    "unsubscribed": "email.unsubscribed",
    "spam": "email.spam_complaint",
    "complaint": "email.spam_complaint",
    "request": "email.queued",
}

DEDUPE_TTL_DAYS = 30


def event_dedupe_key(event: dict[str, Any]) -> str:
    """Stable identity for one delivery. Prefer Brevo's message id +
    event + email (the same message can legitimately produce several
    different events); fall back to a payload hash."""
    message_id = event.get("message-id") or event.get("id")
    email = (event.get("email") or "").lower()
    name = str(event.get("event") or "")
    if message_id:
        base = f"{message_id}:{name}:{email}"
        # `opened`/`click` legitimately fire repeatedly; their `date`
        # disambiguates real re-opens from redelivered duplicates.
        if name in {"opened", "unique_opened", "click", "clicked"}:
            base += f":{event.get('date') or event.get('ts_event') or ''}"
        return base[:255]
    digest = hashlib.sha256(
        json.dumps(event, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f"hash:{digest}"


def mark_event_seen(session: Session, event_key: str) -> bool:
    """Record the event id; returns False when it was already seen
    (duplicate delivery → caller skips processing)."""
    existing = session.scalar(
        select(WebhookEventSeen).where(
            WebhookEventSeen.system == "brevo",
            WebhookEventSeen.event_key == event_key,
        )
    )
    if existing is not None:
        return False
    session.add(
        WebhookEventSeen(
            system="brevo", event_key=event_key, seen_at=datetime.now(UTC)
        )
    )
    session.flush()
    return True


def prune_seen_events(session: Session) -> int:
    """Opportunistic 30-day TTL cleanup, invoked by the worker after
    each processed batch. Cheap: one indexed DELETE."""
    boundary = datetime.now(UTC) - timedelta(days=DEDUPE_TTL_DAYS)
    result = session.execute(
        sa_delete(WebhookEventSeen).where(WebhookEventSeen.seen_at < boundary)
    )
    return int(result.rowcount or 0)


def _parse_event_date(event: dict[str, Any]) -> datetime:
    for key in ("date", "date_event"):
        raw = event.get(key)
        if isinstance(raw, str) and raw:
            for parser in (
                lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")),
                lambda v: datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC),
            ):
                try:
                    parsed = parser(raw)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    return parsed
                except ValueError:
                    continue
    for key in ("ts_event", "ts", "ts_epoch"):
        raw = event.get(key)
        if isinstance(raw, (int, float)) and raw:
            # ts_epoch arrives in milliseconds; the others in seconds.
            seconds = raw / 1000 if raw > 10_000_000_000 else raw
            return datetime.fromtimestamp(seconds, tz=UTC)
    return datetime.now(UTC)


def process_brevo_webhook_event(
    session: Session, event: dict[str, Any], *, account_id: str
) -> str:
    """Materialise one event. Returns a short status string for logs:
    `processed`, `duplicate`, `unknown_event`, `unknown_contact`."""
    raw_name = str(event.get("event") or "").lower()
    event_type = EVENT_TYPE_MAP.get(raw_name)
    if event_type is None:
        logger.info("brevo.webhook ignoring unsupported event %r", raw_name)
        return "unknown_event"

    key = event_dedupe_key(event)
    if not mark_event_seen(session, key):
        logger.info("brevo.webhook duplicate delivery skipped key=%s", key)
        return "duplicate"

    email = (event.get("email") or "").strip().lower()
    contact = (
        session.scalar(select(Contact).where(func.lower(Contact.email) == email))
        if email
        else None
    )
    if contact is None:
        # NEVER create contacts from webhooks.
        logger.warning(
            "brevo.webhook no CRM contact for email=%r account=%s event=%s — discarded",
            email,
            account_id,
            raw_name,
        )
        return "unknown_contact"

    occurred_at = _parse_event_date(event)
    subject = event.get("subject")
    url = event.get("link") or event.get("URL")
    session.add(
        ActivityEvent(
            contact_id=contact.id,
            system="brevo",
            account_id=account_id,
            external_id=key,
            event_type=event_type,
            subject=str(subject) if subject else None,
            body=str(url) if url else None,
            metadata_json=json.dumps(event, default=str),
            occurred_at=occurred_at,
            synced_at=datetime.now(UTC),
        )
    )

    # Reactive mutations.
    if event_type == "email.unsubscribed":
        _flip_consent(session, contact, account_id, raw_name)
    elif event_type == "email.bounced_hard":
        _invalidate_email(session, contact, account_id, raw_name)
    elif event_type == "email.spam_complaint":
        _flip_consent(session, contact, account_id, raw_name)
        _invalidate_email(session, contact, account_id, raw_name)

    session.flush()
    return "processed"


def _flip_consent(
    session: Session, contact: Contact, account_id: str, event_name: str
) -> None:
    if contact.marketing_consent == "unsubscribed":
        return
    previous = contact.marketing_consent
    contact.marketing_consent = "unsubscribed"
    record_event(
        session,
        action=Action.CONTACT_CONSENT_CHANGED_BY_WEBHOOK,
        target_type="contact",
        target_id=contact.id,
        metadata={
            "from": str(previous),
            "to": "unsubscribed",
            "source": f"brevo:{account_id}",
            "event": event_name,
        },
    )


def _invalidate_email(
    session: Session, contact: Contact, account_id: str, event_name: str
) -> None:
    if not contact.is_email_valid:
        return
    contact.is_email_valid = False
    record_event(
        session,
        action=Action.CONTACT_EMAIL_INVALIDATED_BY_WEBHOOK,
        target_type="contact",
        target_id=contact.id,
        metadata={
            "source": f"brevo:{account_id}",
            "event": event_name,
        },
    )


def process_brevo_webhook_batch(events: list[dict[str, Any]], account_id: str) -> None:
    """RQ entrypoint enqueued by the webhook route. Opens its own
    session (the request session is long gone by the time the worker
    picks this up)."""
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        for event in events:
            try:
                process_brevo_webhook_event(session, event, account_id=account_id)
            except Exception:  # noqa: BLE001 - one bad event ≠ batch failure
                logger.exception(
                    "brevo.webhook event processing failed: %s",
                    json.dumps(event, default=str)[:500],
                )
        prune_seen_events(session)
        session.commit()
