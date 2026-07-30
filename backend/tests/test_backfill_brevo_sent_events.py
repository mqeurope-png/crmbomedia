"""PR-Fix-Sent-Backfill — core del backfill retroactivo de `email.sent`.

Testea la lógica de inserción (match por email, idempotencia, dry-run)
sin tocar la red: `backfill_sent_for_campaign` recibe la lista de emails
de destinatarios directamente (en producción el CLI la saca del export
de Brevo).
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra todos los modelos en Base.metadata
from app.models.crm import ActivityEvent, Base, Contact
from scripts.backfill_brevo_sent_events import backfill_sent_for_campaign

SENT_AT = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as s:
        s.add_all([
            Contact(first_name="A", email="a@x.com"),
            Contact(first_name="B", email="b@x.com"),
        ])
        s.commit()
    yield factory
    Base.metadata.drop_all(engine)


def _count_sent(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count(ActivityEvent.id)).where(
                ActivityEvent.event_type == "email.sent"
            )
        )
        or 0
    )


def test_backfill_brevo_sent_events_matches_contact_by_email(session_factory):
    with session_factory() as s:
        counts = backfill_sent_for_campaign(
            s, brevo_campaign_id=10, account_id="main",
            recipient_emails=["a@x.com", "unknown@x.com"], sent_at=SENT_AT,
        )
    assert counts["matched"] == 1
    assert counts["created"] == 1
    assert counts["unmatched_email"] == 1
    with session_factory() as s:
        ev = s.scalar(
            select(ActivityEvent).where(ActivityEvent.event_type == "email.sent")
        )
        assert ev is not None
        assert ev.campaign_brevo_id == 10
        assert s.get(Contact, ev.contact_id).email == "a@x.com"


def test_backfill_brevo_sent_events_idempotent(session_factory):
    emails = ["a@x.com", "b@x.com"]
    with session_factory() as s:
        c1 = backfill_sent_for_campaign(
            s, brevo_campaign_id=10, account_id="main",
            recipient_emails=emails, sent_at=SENT_AT,
        )
        assert c1["created"] == 2
        assert _count_sent(s) == 2
        # Segunda corrida: no duplica.
        c2 = backfill_sent_for_campaign(
            s, brevo_campaign_id=10, account_id="main",
            recipient_emails=emails, sent_at=SENT_AT,
        )
        assert c2["created"] == 0
        assert c2["skipped_existing"] == 2
        assert _count_sent(s) == 2


def test_backfill_dry_run_no_writes(session_factory):
    with session_factory() as s:
        counts = backfill_sent_for_campaign(
            s, brevo_campaign_id=10, account_id="main",
            recipient_emails=["a@x.com", "b@x.com"], sent_at=SENT_AT,
            dry_run=True,
        )
        # Cuenta lo que crearía…
        assert counts["created"] == 2
        # …pero no escribe nada.
        assert _count_sent(s) == 0


def test_backfill_dedupes_repeated_email_in_one_run(session_factory):
    with session_factory() as s:
        counts = backfill_sent_for_campaign(
            s, brevo_campaign_id=10, account_id="main",
            recipient_emails=["a@x.com", "A@X.com", "a@x.com"], sent_at=SENT_AT,
        )
        assert counts["created"] == 1
        assert _count_sent(s) == 1
