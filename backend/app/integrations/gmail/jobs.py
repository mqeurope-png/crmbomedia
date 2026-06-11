"""RQ jobs for the Gmail integration.

Two surfaces:
- `enqueue_process_history` — fired by the webhook to import inbound
  replies for a given user.
- `enqueue_renew_all_watches` — fired by the scheduler heartbeat
  to top up watches before the 7-day upstream expiry.

The job entry points use the standard `app.db.session.get_session`
context so they can run under the worker without an HTTP request.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models.crm import GmailPubsubWatch, UserGoogleIntegration

logger = logging.getLogger(__name__)


def enqueue_process_history(*, user_id: str, new_history_id: int) -> None:
    """Push the history-processing job onto the worker queue. Best
    effort — if Redis is unreachable, we fall back to in-process
    execution so the webhook still imports the replies."""
    try:
        from app.workers.queues import queue_for  # noqa: PLC0415

        queue = queue_for("gmail", "process_history")
        queue.enqueue(process_history_job, user_id, new_history_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "gmail.enqueue_failed user_id=%s; running inline", user_id
        )
        process_history_job(user_id, new_history_id)


def process_history_job(user_id: str, new_history_id: int) -> int:
    """RQ entry point. Returns the count of messages imported."""
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    with Session(get_engine()) as session:
        try:
            imported = gmail_service.process_history(
                session, user_id=user_id, new_history_id=new_history_id
            )
            session.commit()
            return imported
        except Exception:
            session.rollback()
            logger.warning(
                "gmail.process_history_job_failed user_id=%s", user_id, exc_info=True
            )
            raise


def enqueue_renew_all_watches() -> None:
    try:
        from app.workers.queues import queue_for  # noqa: PLC0415

        queue = queue_for("gmail", "renew_watches")
        queue.enqueue(renew_all_watches_job)
    except Exception:  # noqa: BLE001
        logger.warning("gmail.renew.enqueue_failed; running inline")
        renew_all_watches_job()


def renew_all_watches_job() -> int:
    """Renew every user's Gmail watch. Returns the count of
    successful renewals. Continues past individual failures."""
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    renewed = 0
    with Session(get_engine()) as session:
        # Iterate every user with Gmail credentials. We don't filter
        # on the watch table because users may have never registered
        # one (first-time renewal).
        users_with_google = list(
            session.scalars(
                select(UserGoogleIntegration).where(
                    UserGoogleIntegration.scopes.like(
                        "%https://www.googleapis.com/auth/gmail.send%"
                    )
                )
            )
        )
        for integration in users_with_google:
            try:
                gmail_service.register_watch(
                    session, user_id=integration.user_id
                )
                session.commit()
                renewed += 1
            except Exception:  # noqa: BLE001
                session.rollback()
                logger.warning(
                    "gmail.renew_failed user_id=%s",
                    integration.user_id,
                    exc_info=True,
                )
    return renewed


def watches_expiring_soon(session: Session, *, days: int = 1) -> list[GmailPubsubWatch]:
    """Return watches whose expiry is within `days` days. Used by
    the cron heartbeat to renew lazily instead of unconditionally."""
    horizon = datetime.now(UTC) + timedelta(days=days)
    return list(
        session.scalars(
            select(GmailPubsubWatch).where(
                GmailPubsubWatch.watch_expires_at <= horizon
            )
        )
    )
