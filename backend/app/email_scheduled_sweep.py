"""Scheduled-send worker — every N minutes, ship messages whose
`scheduled_for` has arrived.

Sprint Email v2.4e. Replaces the snooze sweep (v2.4c). The RQ
queue name stays `emails:snooze_sweep` to keep prod's worker
container + redis state untouched across the deploy — only the
handler logic + module path change.

The job:
1. Selects every `EmailMessage` with `scheduled_status='pending'`
   whose `scheduled_for <= now`.
2. Calls `gmail_service.send_email` for each — which performs the
   Gmail API call, mints the real `gmail_message_id` /
   `gmail_thread_id`, persists tracking tokens and the activity
   timeline event.
3. Copies the new ids onto the pending row, flips
   `scheduled_status='sent'`, stamps `sent_at`. The stub
   EmailMessage `gmail_send_email` minted gets discarded since
   the pending row is the operator-visible canonical one.
4. Re-arms itself on `EMAIL_SCHEDULED_SWEEP_MINUTES` (default 1).

Any send failure flips the pending row to `scheduled_status='failed'`
so the UI surfaces the error without retry storms.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models.crm import (
    EmailMessage,
    EmailScheduledStatus,
    EmailThread,
    User,
)
from app.workers.queues import queue_name, redis_connection

logger = logging.getLogger(__name__)

DEFAULT_SWEEP_MINUTES = 1
# Queue + lock keys are intentionally still the snooze ones so the
# existing prod worker container picks the job up without a queue
# rename roundtrip.
SCHEDULED_LOCK_KEY = "email:snooze_sweep:heartbeat"
SCHEDULED_QUEUE = queue_name("emails", "snooze_sweep")


def _interval_minutes() -> int:
    raw = os.environ.get("EMAIL_SCHEDULED_SWEEP_MINUTES")
    if not raw:
        return DEFAULT_SWEEP_MINUTES
    try:
        value = int(raw)
        return value if value > 0 else DEFAULT_SWEEP_MINUTES
    except ValueError:
        return DEFAULT_SWEEP_MINUTES


def _send_one(session: Session, msg: EmailMessage, now: datetime) -> None:
    """Hand a pending EmailMessage to Gmail, then mutate the row to
    its `sent` state. Raises whatever Gmail raises so the caller
    can flip the row to `failed`."""
    from app.integrations.gmail.service import (  # noqa: PLC0415
        send_email as gmail_send_email,
    )

    to = json.loads(msg.to_emails_json) if msg.to_emails_json else []
    cc = json.loads(msg.cc_emails_json) if msg.cc_emails_json else None
    bcc = json.loads(msg.bcc_emails_json) if msg.bcc_emails_json else None

    # Re-derive `include_unsubscribe` from the owner's preference at
    # SEND time — if they toggled the default between schedule and
    # send, the newer preference should win.
    user = (
        session.get(User, msg.created_by_user_id)
        if msg.created_by_user_id
        else None
    )
    include_unsubscribe = (
        bool(user.email_include_unsubscribe_default) if user else False
    )

    # Threading: when the pending message lives in a thread that
    # already has at least one real Gmail message (a sent outbound
    # or imported inbound), pick the most recent of those as the
    # reply target so `send_email`'s threading lookup can wire up
    # In-Reply-To / References. A fresh scheduled compose lives in
    # a sentinel thread with no other rows, so the parent stays
    # None and we just create a brand-new conversation.
    parent = session.scalar(
        select(EmailMessage)
        .where(
            EmailMessage.thread_id == msg.thread_id,
            EmailMessage.id != msg.id,
            EmailMessage.gmail_message_id.is_not(None),
        )
        .order_by(EmailMessage.sent_at.desc())
        .limit(1)
    )
    parent_message_id = parent.id if parent is not None else None

    new_message = gmail_send_email(
        session,
        sender_user_id=msg.created_by_user_id,
        from_alias=msg.from_email,
        from_name=msg.from_name,
        to=list(to),
        cc=list(cc) if cc else None,
        bcc=list(bcc) if bcc else None,
        subject=msg.subject or "",
        body_html=msg.body_html,
        body_text=msg.body_text,
        contact_id=msg.contact_id,
        in_reply_to_message_id=parent_message_id,
        include_unsubscribe=include_unsubscribe,
    )

    # The pending row is the operator-visible canonical message —
    # it already has a stable id the UI references from
    # /emails/programados — so we carry the real Gmail ids onto it
    # and drop the stub `gmail_send_email` minted. Snapshot the
    # ids first, then DELETE-then-flush the stub so the
    # `(account, gmail_message_id)` unique constraint doesn't blow
    # up while both rows momentarily share the new id.
    new_gmail_message_id = new_message.gmail_message_id
    new_thread_id = new_message.thread_id
    new_snippet = new_message.snippet
    sentinel_thread = session.get(EmailThread, msg.thread_id)
    sentinel_to_drop = (
        sentinel_thread
        if (
            sentinel_thread is not None
            and sentinel_thread.gmail_thread_id.startswith("pending:")
            and sentinel_thread.id != new_thread_id
        )
        else None
    )
    session.delete(new_message)
    if sentinel_to_drop is not None:
        session.delete(sentinel_to_drop)
    session.flush()

    msg.gmail_message_id = new_gmail_message_id
    msg.sent_at = now
    msg.scheduled_status = EmailScheduledStatus.SENT.value
    msg.snippet = new_snippet
    if sentinel_to_drop is not None:
        msg.thread_id = new_thread_id


def scheduled_send_sweep(*, now: datetime | None = None) -> dict[str, int]:
    """Send every pending message whose scheduled_for has arrived.
    Returns a `{sent, failed}` summary for logging / tests. The
    `now` argument is the seam tests exercise — production
    leaves it unset."""
    cutoff = now or datetime.now(UTC)
    summary = {"sent": 0, "failed": 0}
    with Session(get_engine()) as session:
        pending = list(
            session.scalars(
                select(EmailMessage)
                .where(
                    EmailMessage.scheduled_status
                    == EmailScheduledStatus.PENDING.value,
                    EmailMessage.scheduled_for.is_not(None),
                    EmailMessage.scheduled_for <= cutoff,
                )
                .order_by(EmailMessage.scheduled_for.asc())
            )
        )
        for msg in pending:
            msg_id = msg.id
            try:
                _send_one(session, msg, cutoff)
                session.commit()
                summary["sent"] += 1
            except Exception as exc:  # noqa: BLE001 - keep peers alive
                session.rollback()
                fresh = session.get(EmailMessage, msg_id)
                if fresh is not None:
                    fresh.scheduled_status = EmailScheduledStatus.FAILED.value
                    session.commit()
                summary["failed"] += 1
                logger.warning(
                    "email.scheduled_send_sweep failure msg=%s err=%s",
                    msg_id,
                    exc,
                )
        _purge_orphan_pending_threads(session)
    if summary["sent"] or summary["failed"]:
        logger.info(
            "email.scheduled_send_sweep sent=%d failed=%d",
            summary["sent"],
            summary["failed"],
        )
    return summary


def _purge_orphan_pending_threads(session: Session) -> None:
    """Sentinel threads whose only message was cancelled have no
    useful surface anymore — drop them so the inbox doesn't
    accumulate ghost rows. Failed rows STAY so the operator can
    audit what blew up. Sent threads are reconciled to a real
    Gmail id inside `_send_one` and no longer match the sentinel
    `pending:%` LIKE."""
    sentinel_threads = list(
        session.scalars(
            select(EmailThread).where(
                EmailThread.gmail_thread_id.like("pending:%"),
            )
        )
    )
    for thread in sentinel_threads:
        # Skip when ANY message in the thread is still pending or
        # was failed — both shapes deserve to stay visible.
        keep = session.scalar(
            select(EmailMessage.id)
            .where(
                EmailMessage.thread_id == thread.id,
                EmailMessage.scheduled_status.in_(
                    [
                        EmailScheduledStatus.PENDING.value,
                        EmailScheduledStatus.FAILED.value,
                    ]
                ),
            )
            .limit(1)
        )
        if keep is None:
            session.execute(
                delete(EmailMessage).where(
                    EmailMessage.thread_id == thread.id
                )
            )
            session.delete(thread)
    session.commit()


def _sweep_and_rearm() -> None:
    """The RQ-friendly entry point. Runs the sweep, then arms the
    next tick so the heartbeat self-perpetuates."""
    try:
        scheduled_send_sweep()
    finally:
        schedule_sweep()


def schedule_sweep() -> None:
    """Arm a one-shot `_sweep_and_rearm` enqueue N minutes out.
    The SETNX guard makes this safe to call from every API process
    that boots — only the first one wins."""
    interval = timedelta(minutes=_interval_minutes())
    try:
        conn = redis_connection()
        ttl = max(int(interval.total_seconds()) - 30, 5)
        if not conn.set(SCHEDULED_LOCK_KEY, "1", nx=True, ex=ttl):
            return
        try:
            from rq import Queue  # noqa: PLC0415

            Queue(SCHEDULED_QUEUE, connection=conn).enqueue_in(
                interval, _sweep_and_rearm
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "email.scheduled_send_sweep scheduling failed: %s", exc
            )
            conn.delete(SCHEDULED_LOCK_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "email.scheduled_send_sweep redis unreachable: %s", exc
        )


def arm_scheduled_sweep() -> None:
    """Startup hook. Wrapped in try/except so a Redis outage at
    boot doesn't block the API."""
    try:
        schedule_sweep()
    except Exception:  # noqa: BLE001
        logger.warning(
            "email.scheduled_send_sweep arm failed at startup",
            exc_info=True,
        )
