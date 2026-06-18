"""Shared helpers for the tracking router + the send wrapper.

Responsibilities split cleanly:

- `generate_token` produces the 32-byte URL-safe random ids we hand
  out to the open / click / unsubscribe endpoints.
- `wrap_links_for_tracking` rewrites every same-message `<a href>` into
  a click-redirect URL, skipping the things that don't make sense to
  track (mailto:, tel:, anchors, the unsubscribe link itself).
- `inject_open_pixel` appends the 1x1 GIF to the bottom of the body.
- `build_unsubscribe_block` returns the HTML footer + the headers
  Gmail / Outlook need for the One-Click button.
- `dedupe_event` keeps repeat opens / clicks within a small window
  from inflating the counts when a preview pane fires the pixel a
  handful of times.

Everything here is pure-ish (mostly): the DB hits are isolated in
`dedupe_event` and the token persistence helpers, so the rest can be
unit-tested without spinning up a session.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import (
    EmailEventType,
    EmailMessage,
    EmailMessageEvent,
    EmailMessageToken,
    EmailUnsubscribe,
    EmailUnsubscribeScope,
)

# 1x1 transparent GIF — 43 bytes. Embedded so the open pixel response
# never has to touch the filesystem.
TRANSPARENT_GIF: bytes = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)

# When the same pixel / click fires this often within DEDUP_WINDOW we
# treat the later hits as duplicates (preview-pane spam).
DEDUP_WINDOW = timedelta(seconds=60)

# PR-Aperturas-Falsas. Window after `EmailMessage.sent_at` during which
# OPEN hits are discarded. Covers the Gmail-Sent prefetch the sender's
# own client fires immediately after the message lands in their Sent
# folder. Overridable via `EMAIL_OPEN_GRACE_PERIOD_SEC` so prod can tune
# without a redeploy. Only applied to OPEN — CLICK / BOUNCE / UNSUB are
# unaffected because their false-positive cost is high and a real click
# never happens 30 seconds after sending.
OPEN_TRACKING_GRACE_PERIOD_SEC = 30

# CRM tracking pixel emitted by `inject_open_pixel`. Matches `<img>`
# tags whose `src` points at our own `/api/email-track/open/{token}`
# endpoint — third-party pixels (Mailchimp / Sendgrid / etc.) intentionally
# do NOT match so quoted-reply HTML keeps rendering correctly.
_TRACKING_PIXEL_RE = re.compile(
    r"""<img[^>]*src=["'][^"']*?/api/email-track/open/[^"']*?["'][^>]*?/?>""",
    re.IGNORECASE,
)

# Don't rewrite links to: mailto/tel/sms protocol, in-page anchors,
# or the unsubscribe redirect itself (the caller passes its URL in via
# `extra_skip`).
_SKIP_PREFIXES = ("mailto:", "tel:", "sms:", "javascript:", "#")

_HREF_RE = re.compile(
    # Match `<a ... href="X" ... >` capturing the URL inside double
    # quotes. We keep it simple: emails author hrefs in double quotes,
    # which is also what TinyMCE serialises.
    r'href\s*=\s*"([^"]*)"',
    re.IGNORECASE,
)


def generate_token() -> str:
    """32-byte URL-safe random — ~43 chars after stripping `=` padding.

    `secrets.token_urlsafe` already uses base64-url; we slice to a
    deterministic 43-char width so the DB column stays predictable."""
    return secrets.token_urlsafe(32)[:43]


def b64url_encode(value: str) -> str:
    return (
        base64.urlsafe_b64encode(value.encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )


def b64url_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("Invalid base64 click payload") from exc


def wrap_links_for_tracking(
    html: str,
    *,
    token: str,
    base_url: str,
    extra_skip: set[str] | None = None,
) -> str:
    """Replace every `href="X"` with a click-redirect URL pointing back
    at /api/email-track/click/{token}?d={base64(X)}.

    Skips mailto/tel/sms/in-page anchors and anything explicitly listed
    in `extra_skip` — the unsubscribe footer link is the main use of
    that argument. Returns the input untouched when the body has no
    HTML to rewrite.
    """
    if not html:
        return html
    skip = set(extra_skip or ())

    def _replace(match: re.Match[str]) -> str:
        url = match.group(1)
        if not url:
            return match.group(0)
        if url in skip:
            return match.group(0)
        lowered = url.lower()
        if any(lowered.startswith(p) for p in _SKIP_PREFIXES):
            return match.group(0)
        encoded = b64url_encode(url)
        redirect = (
            f"{base_url.rstrip('/')}/api/email-track/click/{token}?d={encoded}"
        )
        return f'href="{redirect}"'

    return _HREF_RE.sub(_replace, html)


def inject_open_pixel(html: str, *, token: str, base_url: str) -> str:
    """Append (or, when a `</body>` exists, splice in before) a hidden
    1x1 image whose request URL we treat as an open event."""
    pixel = (
        f'<img src="{base_url.rstrip("/")}/api/email-track/open/{token}" '
        'width="1" height="1" alt="" '
        'style="display:none;max-height:0;overflow:hidden" />'
    )
    if "</body>" in html.lower():
        # Use a case-insensitive replace on the first match only.
        return re.sub(
            r"</body>", pixel + "</body>", html, count=1, flags=re.IGNORECASE,
        )
    return html + pixel


def build_unsubscribe_block(
    *, token: str, base_url: str
) -> tuple[str, dict[str, str], str]:
    """Returns (html_block, extra_headers, unsubscribe_url).

    `extra_headers` carries the RFC 8058 pair Gmail / Outlook need to
    render the native "Anular suscripción" button at the top of the
    message; the HTML block is the visible footer link."""
    unsubscribe_url = (
        f"{base_url.rstrip('/')}/api/unsubscribe/{token}"
    )
    html = (
        '<div style="font-size:11px;color:#888;margin-top:24px;'
        'text-align:center">'
        f'<a href="{unsubscribe_url}">Anular suscripción</a>'
        "</div>"
    )
    headers = {
        "List-Unsubscribe": f"<{unsubscribe_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    return html, headers, unsubscribe_url


def _open_grace_period() -> timedelta:
    """Read `EMAIL_OPEN_GRACE_PERIOD_SEC` at call time so an operator
    flipping the env var doesn't need a worker restart. Falls back to
    `OPEN_TRACKING_GRACE_PERIOD_SEC` on any parse error."""
    raw = os.getenv("EMAIL_OPEN_GRACE_PERIOD_SEC")
    if raw is not None:
        try:
            value = int(raw)
        except ValueError:
            value = -1
        if value >= 0:
            return timedelta(seconds=value)
    return timedelta(seconds=OPEN_TRACKING_GRACE_PERIOD_SEC)


def within_open_grace_period(
    sent_at: datetime | None,
    *,
    now: datetime | None = None,
) -> bool:
    """True when `now` is inside the post-send grace window — the
    caller should swallow the pixel hit without writing an event row.

    Returns False when `sent_at` is None (no anchor to compare against;
    we'd rather count a real apertura than discard it because the row
    isn't fully populated yet)."""
    if sent_at is None:
        return False
    now = now or datetime.now(UTC)
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    return (now - sent_at) < _open_grace_period()


def strip_tracking_pixel(html: str | None) -> str | None:
    """Remove every `<img>` whose `src` matches the CRM's own open
    tracking endpoint so previewing a saved email in /emails never
    inflates the count.

    Idempotent. NULL / empty input returns input unchanged. Pixels
    served by other systems (Mailchimp, Sendgrid, the recipient's own
    tracker) are preserved by design — they let the operator see the
    quoted reply chain the way the recipient sent it."""
    if not html:
        return html
    return _TRACKING_PIXEL_RE.sub("", html)


def dedupe_event(
    session: Session,
    *,
    message_id: str,
    event_type: EmailEventType,
    ip: str | None,
    now: datetime | None = None,
    window: timedelta = DEDUP_WINDOW,
) -> bool:
    """True when a near-identical event was just recorded for the same
    message and IP. Caller skips creating the new row when this returns
    True. We dedupe on (message, type, ip) — a different recipient on
    the same conversation gets their own count."""
    now = now or datetime.now(UTC)
    cutoff = now - window
    existing = session.scalar(
        select(EmailMessageEvent.id)
        .where(EmailMessageEvent.message_id == message_id)
        .where(EmailMessageEvent.event_type == event_type)
        .where(EmailMessageEvent.ip == ip)
        .where(EmailMessageEvent.occurred_at >= cutoff)
        .limit(1)
    )
    return existing is not None


def record_event(
    session: Session,
    *,
    message_id: str,
    event_type: EmailEventType,
    ip: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> EmailMessageEvent:
    """Append a new event row. The caller is responsible for the
    dedup check; here we just persist what we're told."""
    now = now or datetime.now(UTC)
    event = EmailMessageEvent(
        message_id=message_id,
        event_type=event_type,
        occurred_at=now,
        ip=ip,
        user_agent=user_agent[:500] if user_agent else None,
        metadata_json=json.dumps(metadata) if metadata is not None else None,
    )
    session.add(event)
    session.flush()
    return event


def persist_tracking_token(
    session: Session, *, message_id: str, token: str
) -> EmailMessageToken:
    """One row per outbound message — both the open pixel and the
    click redirect look up the message through this token."""
    row = EmailMessageToken(token=token, message_id=message_id)
    session.add(row)
    session.flush()
    return row


def lookup_message_by_token(
    session: Session, token: str
) -> EmailMessage | None:
    row = session.scalar(
        select(EmailMessageToken).where(EmailMessageToken.token == token)
    )
    if row is None:
        return None
    return session.get(EmailMessage, row.message_id)


def lookup_unsubscribe_by_token(
    session: Session, token: str
) -> EmailUnsubscribe | None:
    return session.scalar(
        select(EmailUnsubscribe).where(EmailUnsubscribe.token == token)
    )


def contact_is_unsubscribed(
    session: Session,
    contact_id: str,
    *,
    scope: EmailUnsubscribeScope = EmailUnsubscribeScope.MARKETING,
) -> EmailUnsubscribe | None:
    """A contact is considered opted-out of a scope when there's an
    explicit row for that scope OR an `ALL` row that wipes them
    completely."""
    return session.scalar(
        select(EmailUnsubscribe)
        .where(EmailUnsubscribe.contact_id == contact_id)
        .where(
            EmailUnsubscribe.scope.in_(
                {scope.value, EmailUnsubscribeScope.ALL.value}
            )
        )
    )
