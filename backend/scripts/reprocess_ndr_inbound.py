"""Retroactively process inbound emails that we now recognise as
non-delivery reports.

2.3a shipped with a narrow NDR detector that missed everything from
IONOS / kundenserver / Exim — those bounces ended up persisted as
ordinary inbound messages with a "Mail delivery failed: …" subject
clogging the operator's threads. This script walks recent inbound
rows, re-runs the (now-wider) `_is_ndr` + `_parse_ndr`, attaches a
BOUNCE event to the original outbound when we can find it, and
deletes the NDR row from `email_messages`.

Usage:
    INTEGRATION_SECRETS_KEY=…  python -m scripts.reprocess_ndr_inbound
    INTEGRATION_SECRETS_KEY=…  python -m scripts.reprocess_ndr_inbound \
        --since-days 30 --dry-run

The script commits per row so a partial run still makes forward
progress. `--dry-run` reports what it would do without writing.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.email_tracking.services import record_event
from app.integrations.gmail.service import (
    _find_bounced_message,
    _is_ndr,
    _parse_ndr,
)
from app.models.crm import (
    EmailDirection,
    EmailEventType,
    EmailMessage,
    EmailThread,
)

log = logging.getLogger("reprocess_ndr_inbound")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def _candidate_inbounds(
    session: Session, *, since: datetime
) -> list[EmailMessage]:
    return list(
        session.scalars(
            select(EmailMessage)
            .where(EmailMessage.direction == EmailDirection.INBOUND)
            .where(EmailMessage.sent_at >= since)
            .order_by(EmailMessage.sent_at.asc())
        )
    )


def _looks_like_ndr(message: EmailMessage) -> bool:
    """Synthesise the same `headers` dict the live parser sees so we
    can hand it to `_is_ndr` unchanged. We only have the subject /
    from / body_text on the stored row, which is enough for the
    widened detector."""
    headers: dict[str, str] = {}
    if message.subject:
        headers["subject"] = message.subject
    return _is_ndr(message.from_email or "", headers)


def reprocess(*, since: datetime, dry_run: bool) -> dict[str, int]:
    counts = {"scanned": 0, "matched": 0, "linked": 0, "deleted": 0}
    engine = get_engine()
    with Session(engine) as session:
        candidates = _candidate_inbounds(session, since=since)
        counts["scanned"] = len(candidates)
        for ndr in candidates:
            if not _looks_like_ndr(ndr):
                continue
            counts["matched"] += 1
            info = _parse_ndr(
                {"subject": ndr.subject or ""}, ndr.body_text
            )
            thread = session.get(EmailThread, ndr.thread_id)
            original = _find_bounced_message(
                session,
                user_id=ndr.gmail_account_user_id,
                gmail_thread_id=(
                    thread.gmail_thread_id if thread is not None else ""
                ),
                failed_to=info.get("failed_to"),
            )
            log.info(
                "ndr id=%s subject=%r failed_to=%s original=%s",
                ndr.id,
                (ndr.subject or "")[:80],
                info.get("failed_to"),
                original.id if original else None,
            )
            if dry_run:
                continue
            if original is not None:
                metadata: dict[str, Any] = {
                    **info,
                    "from": ndr.from_email,
                    "subject": ndr.subject,
                    "reprocessed": True,
                }
                record_event(
                    session,
                    message_id=original.id,
                    event_type=EmailEventType.BOUNCE,
                    metadata=metadata,
                )
                counts["linked"] += 1
            # Roll back the thread message_count + drop the row.
            if thread is not None:
                thread.message_count = max(
                    0, (thread.message_count or 1) - 1
                )
            session.delete(ndr)
            counts["deleted"] += 1
            session.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since-days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    since = datetime.now(UTC) - timedelta(days=args.since_days)
    counts = reprocess(since=since, dry_run=args.dry_run)
    log.info(
        "reprocess summary: scanned=%d matched=%d linked=%d deleted=%d "
        "dry_run=%s since=%s",
        counts["scanned"],
        counts["matched"],
        counts["linked"],
        counts["deleted"],
        args.dry_run,
        since.isoformat(),
    )
    # Re-emit as a single line of JSON for parsing-friendliness when
    # piping through `jq` or the deploy log.
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
