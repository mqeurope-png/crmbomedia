"""High-level Gmail operations.

The route layer + worker layer call these. Each function takes a
SQLAlchemy session and is responsible for its own flushes; the
caller decides when to commit.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.gmail.client import GmailClient
from app.integrations.google_calendar import service as google_service
from app.integrations.google_calendar.client import GoogleAuthExpiredError
from app.models.crm import (
    Contact,
    EmailDirection,
    EmailMessage,
    EmailThread,
    GmailPubsubWatch,
)

logger = logging.getLogger(__name__)


class GmailNotConnectedError(RuntimeError):
    """Raised when the operator tries to act on Gmail before
    granting the gmail.send scope."""


class GmailScopeMissingError(RuntimeError):
    """Raised when the integration row exists but lacks a required
    scope — typically because the user is still on the Fase 2
    scopes."""


def _has_gmail_send(scopes: str) -> bool:
    return "https://www.googleapis.com/auth/gmail.send" in scopes.split()


def _client_for(session: Session, user_id: str) -> GmailClient:
    integration = google_service.get_integration(session, user_id)
    if integration is None:
        raise GmailNotConnectedError("Gmail no está conectado para este usuario.")
    if not _has_gmail_send(integration.scopes or ""):
        raise GmailScopeMissingError(
            "Falta el permiso gmail.send. Vuelve a autorizar Google en /account."
        )
    return GmailClient(session, integration)


def list_aliases(session: Session, user_id: str) -> list[dict[str, Any]]:
    """Wrap `client.list_send_as_aliases` with the error mapping the
    API layer expects."""
    return _client_for(session, user_id).list_send_as_aliases()


def send_email(
    session: Session,
    *,
    sender_user_id: str,
    from_alias: str,
    from_name: str | None,
    to: list[str],
    cc: list[str] | None,
    bcc: list[str] | None,
    subject: str,
    body_html: str | None,
    body_text: str | None,
    contact_id: str | None,
    in_reply_to_message_id: str | None = None,
) -> EmailMessage:
    """Send a new outbound email and persist the thread + message rows.

    `in_reply_to_message_id` is OUR `EmailMessage.id`; when set we
    look up the upstream Gmail thread + headers so the recipient's
    client recognises the reply.
    """
    client = _client_for(session, sender_user_id)

    in_reply_to_header: str | None = None
    references_header: list[str] | None = None
    thread_id: str | None = None
    existing_thread: EmailThread | None = None

    if in_reply_to_message_id:
        existing = session.get(EmailMessage, in_reply_to_message_id)
        if existing is not None:
            in_reply_to_header = existing.gmail_message_id
            references_header = [existing.gmail_message_id]
            existing_thread = existing.thread
            thread_id = existing_thread.gmail_thread_id

    response = client.send_message(
        from_alias=from_alias,
        from_name=from_name,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        in_reply_to_message_id=in_reply_to_header,
        references=references_header,
        thread_id=thread_id,
    )

    gmail_message_id = response["id"]
    gmail_thread_id = response["threadId"]
    now = datetime.now(UTC)

    thread = existing_thread or _get_or_create_thread(
        session,
        gmail_account_user_id=sender_user_id,
        gmail_thread_id=gmail_thread_id,
        initiated_by_user_id=sender_user_id,
        contact_id=contact_id,
        subject=subject,
        first_message_at=now,
        participants=[*to, *(cc or []), from_alias],
    )

    message = EmailMessage(
        thread_id=thread.id,
        gmail_message_id=gmail_message_id,
        gmail_account_user_id=sender_user_id,
        direction=EmailDirection.OUTBOUND,
        from_email=from_alias,
        from_name=from_name,
        to_emails_json=json.dumps(to),
        cc_emails_json=json.dumps(cc) if cc else None,
        bcc_emails_json=json.dumps(bcc) if bcc else None,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        snippet=_snippet(body_text, body_html),
        sent_at=now,
        contact_id=contact_id,
        created_by_user_id=sender_user_id,
    )
    session.add(message)
    thread.message_count = (thread.message_count or 0) + 1
    thread.last_message_at = now
    session.flush()
    return message


def process_history(
    session: Session,
    *,
    user_id: str,
    new_history_id: int,
) -> int:
    """Fetch the upstream history slice and import inbound messages
    that land in a CRM-initiated thread. Returns the number of
    messages persisted.
    """
    watch = session.scalar(
        select(GmailPubsubWatch).where(GmailPubsubWatch.user_id == user_id)
    )
    if watch is None:
        logger.warning("gmail.process_history.no_watch user_id=%s", user_id)
        return 0

    client = _client_for(session, user_id)
    try:
        history = client.list_history(watch.history_id)
    except GoogleAuthExpiredError:
        logger.warning("gmail.process_history.auth_expired user_id=%s", user_id)
        return 0

    crm_thread_ids = {
        t.gmail_thread_id
        for t in session.scalars(
            select(EmailThread).where(
                EmailThread.gmail_account_user_id == user_id
            )
        )
    }
    seen_messages = {
        m.gmail_message_id
        for m in session.scalars(
            select(EmailMessage).where(
                EmailMessage.gmail_account_user_id == user_id
            )
        )
    }

    # Late import: googleapiclient is heavy and tests sometimes
    # patch the whole gmail client out, so importing at module top
    # would create an import-order dependency.
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    imported = 0
    for entry in history.get("history", []):
        for added in entry.get("messagesAdded", []):
            msg_meta = added.get("message", {})
            mid = msg_meta.get("id")
            tid = msg_meta.get("threadId")
            if not mid or not tid or tid not in crm_thread_ids:
                continue
            if mid in seen_messages:
                continue
            try:
                full = client.get_message(mid)
                _persist_inbound(
                    session,
                    user_id=user_id,
                    raw=full,
                    gmail_thread_id=tid,
                )
                imported += 1
            except HttpError as exc:
                gone_status = (
                    getattr(exc, "status_code", None)
                    or getattr(exc.resp, "status", None)
                )
                if gone_status in (404, 410):
                    # Message was deleted between Gmail's history.list
                    # and our get_message call — common with drafts,
                    # spam moves, Trash retention. Log and carry on;
                    # leaving the whole batch un-advanced because of
                    # one ghost message used to trap the watch on the
                    # same range forever.
                    logger.info(
                        "gmail.process_history.message_gone "
                        "user_id=%s msg=%s status=%s",
                        user_id,
                        mid,
                        gone_status,
                    )
                    continue
                logger.warning(
                    "gmail.process_history.fetch_failed "
                    "user_id=%s msg=%s status=%s",
                    user_id,
                    mid,
                    gone_status,
                    exc_info=True,
                )
                continue
            except Exception:  # noqa: BLE001
                logger.warning(
                    "gmail.process_history.persist_failed user_id=%s msg=%s",
                    user_id,
                    mid,
                    exc_info=True,
                )
                continue

    # Always advance the watch — even when every message in the
    # range failed individually. Otherwise a single ghost message
    # would trap us reprocessing the same history forever.
    watch.history_id = new_history_id
    session.flush()
    return imported


def _persist_inbound(
    session: Session,
    *,
    user_id: str,
    raw: dict[str, Any],
    gmail_thread_id: str,
) -> EmailMessage:
    headers = _index_headers(raw.get("payload", {}).get("headers", []))
    from_header = headers.get("from") or ""
    to_header = headers.get("to") or ""
    cc_header = headers.get("cc")
    subject = headers.get("subject")
    sent_at = _parse_date(headers.get("date")) or datetime.now(UTC)

    from_addresses = getaddresses([from_header])
    from_name = from_addresses[0][0] if from_addresses else None
    from_email = from_addresses[0][1] if from_addresses else ""
    to_emails = [addr for _, addr in getaddresses([to_header]) if addr]
    cc_emails = [addr for _, addr in getaddresses([cc_header])] if cc_header else None
    body_text, body_html = _extract_bodies(raw.get("payload", {}))

    contact = session.scalar(
        select(Contact).where(Contact.email == from_email)
    )

    thread = session.scalar(
        select(EmailThread).where(
            EmailThread.gmail_account_user_id == user_id,
            EmailThread.gmail_thread_id == gmail_thread_id,
        )
    )
    if thread is None:
        # Should not happen — process_history filters by known
        # threads — but stay defensive.
        thread = _get_or_create_thread(
            session,
            gmail_account_user_id=user_id,
            gmail_thread_id=gmail_thread_id,
            initiated_by_user_id=user_id,
            contact_id=contact.id if contact else None,
            subject=subject,
            first_message_at=sent_at,
            participants=[from_email, *to_emails],
        )

    message = EmailMessage(
        thread_id=thread.id,
        gmail_message_id=raw["id"],
        gmail_account_user_id=user_id,
        direction=EmailDirection.INBOUND,
        from_email=from_email,
        from_name=from_name,
        to_emails_json=json.dumps(to_emails),
        cc_emails_json=json.dumps(cc_emails) if cc_emails else None,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        snippet=raw.get("snippet"),
        sent_at=sent_at,
        contact_id=contact.id if contact else None,
    )
    session.add(message)
    thread.last_message_at = sent_at
    thread.message_count = (thread.message_count or 0) + 1
    thread.has_unread_replies = True
    session.flush()
    return message


def _get_or_create_thread(
    session: Session,
    *,
    gmail_account_user_id: str,
    gmail_thread_id: str,
    initiated_by_user_id: str,
    contact_id: str | None,
    subject: str | None,
    first_message_at: datetime,
    participants: list[str],
) -> EmailThread:
    existing = session.scalar(
        select(EmailThread).where(
            EmailThread.gmail_account_user_id == gmail_account_user_id,
            EmailThread.gmail_thread_id == gmail_thread_id,
        )
    )
    if existing is not None:
        return existing
    thread = EmailThread(
        contact_id=contact_id,
        initiated_by_user_id=initiated_by_user_id,
        gmail_thread_id=gmail_thread_id,
        gmail_account_user_id=gmail_account_user_id,
        subject=subject,
        participants_json=json.dumps(sorted(set(participants))),
        first_message_at=first_message_at,
        last_message_at=first_message_at,
        message_count=0,
    )
    session.add(thread)
    session.flush()
    return thread


def register_watch(session: Session, *, user_id: str) -> GmailPubsubWatch:
    """Register a Gmail Push Notifications watch + persist the
    bookkeeping row. Idempotent — re-registering updates the
    expiry."""
    settings = get_settings()
    if not settings.gmail_pubsub_topic:
        raise RuntimeError(
            "GMAIL_PUBSUB_TOPIC not configured — set it in .env to enable Gmail"
            " push notifications."
        )
    client = _client_for(session, user_id)
    response = client.watch_mailbox(settings.gmail_pubsub_topic)
    history_id = int(response.get("historyId", 0))
    expiration_ms = int(response.get("expiration", 0))
    expires_at = datetime.fromtimestamp(expiration_ms / 1000, tz=UTC)
    now = datetime.now(UTC)
    watch = session.scalar(
        select(GmailPubsubWatch).where(GmailPubsubWatch.user_id == user_id)
    )
    if watch is None:
        watch = GmailPubsubWatch(
            user_id=user_id,
            history_id=history_id,
            watch_expires_at=expires_at,
            last_renewed_at=now,
            topic_name=settings.gmail_pubsub_topic,
        )
        session.add(watch)
    else:
        watch.history_id = history_id
        watch.watch_expires_at = expires_at
        watch.last_renewed_at = now
        watch.topic_name = settings.gmail_pubsub_topic
    session.flush()
    return watch


# ---------------------------------------------------------------------------
# Helpers

def _snippet(text: str | None, html: str | None, max_chars: int = 200) -> str | None:
    base = (text or html or "").strip()
    if not base:
        return None
    flat = " ".join(base.split())
    return flat[:max_chars]


def _index_headers(headers: list[dict[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in headers:
        name = h.get("name", "").lower()
        if name and "value" in h:
            out[name] = h["value"]
    return out


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Walk the MIME payload tree, prefer text/plain + text/html."""
    text: str | None = None
    html: str | None = None
    queue: list[dict[str, Any]] = [payload]
    while queue:
        part = queue.pop()
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            decoded = _b64decode(data)
            if mime == "text/plain" and text is None:
                text = decoded
            elif mime == "text/html" and html is None:
                html = decoded
        for child in part.get("parts", []) or []:
            queue.append(child)
    return text, html


def _b64decode(data: str) -> str:
    import base64  # noqa: PLC0415

    try:
        return base64.urlsafe_b64decode(data.encode()).decode(errors="replace")
    except Exception:  # noqa: BLE001
        return ""
