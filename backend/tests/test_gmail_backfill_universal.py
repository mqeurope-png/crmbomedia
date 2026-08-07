"""CRM-GMAIL-BACKFILL — reprocesado histórico con captura universal.

Gmail está mockeado (fake client con list_messages paginado + get_message).
"""
from __future__ import annotations

import base64
from collections.abc import Generator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401  — registra todos los modelos en Base.metadata
from app.integrations.gmail.backfill_universal import run_backfill_universal
from app.models.crm import (
    Base,
    Contact,
    EmailMessage,
    User,
    UserEmailAlias,
    UserRole,
)
from tests._test_helpers import seed_org_google_integration, seed_test_users

SINCE = date(2026, 2, 1)
UNTIL = date(2026, 8, 1)


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


def _user_id(session: Session, role: UserRole) -> str:
    return session.scalar(select(User.id).where(User.role == role))


def _raw(mid: str, thread_id: str, to: str, *, frm: str = "desconocido@fuera.com",
         labels: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": mid,
        "threadId": thread_id,
        "snippet": "hola",
        "labelIds": labels if labels is not None else ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": frm},
                {"name": "To", "value": to},
                {"name": "Subject", "value": "Consulta"},
                {"name": "Date", "value": "Fri, 15 Mar 2026 10:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"cuerpo").decode()},
        },
    }


def _one_page(msgs: dict[str, dict]) -> dict:
    return {
        "messages": [{"id": k, "threadId": v["threadId"]} for k, v in msgs.items()]
    }


def _make_fake_client(pages_by_label: dict[str, list[dict]], messages: dict[str, dict]):
    """Devuelve una clase FakeClient con datos horneados. `pages_by_label`:
    label → lista de páginas [{"messages": [{id, threadId}], }]. La paginación
    usa el índice de página como token."""

    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_messages(self, *, query, page_size, page_token=None, label_ids=None):
            label = (label_ids or ["INBOX"])[0]
            seq = pages_by_label.get(label, [])
            idx = int(page_token) if page_token else 0
            page = seq[idx] if idx < len(seq) else {"messages": []}
            nxt = str(idx + 1) if idx + 1 < len(seq) else None
            return {
                "messages": page.get("messages", []),
                "nextPageToken": nxt,
                "resultSizeEstimate": len(page.get("messages", [])),
            }

        def get_message(self, mid: str) -> dict[str, Any]:
            return messages[mid]

    return _FakeClient


def _seed(session_factory: sessionmaker, *, aliases=("norma@bomedia.net",)):
    with session_factory() as session:
        owner = _user_id(session, UserRole.USER)
        seed_org_google_integration(session, connected_by_user_id=owner)
        for alias in aliases:
            session.add(
                UserEmailAlias(user_id=owner, alias_email=alias, active=True)
            )
        session.commit()
    return owner


def _run(session_factory, owner, **kwargs):
    with session_factory() as session:
        report = run_backfill_universal(
            session, user_id=owner, since=SINCE, until=UNTIL, **kwargs
        )
        session.commit()
        return report


def _count_messages(session_factory) -> int:
    with session_factory() as session:
        return int(session.scalar(select(func.count()).select_from(EmailMessage)))


def test_backfill_dry_run_no_writes(session_factory, monkeypatch):
    owner = _seed(session_factory)
    fake = _make_fake_client(
        {"INBOX": [{"messages": [{"id": "m1", "threadId": "t1"}]}]},
        {"m1": _raw("m1", "t1", "norma@bomedia.net")},
    )
    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", fake)
    report = _run(session_factory, owner, dry_run=True, labels=["INBOX"])
    assert report.imported_orphan == 1
    assert report.total_processed == 1
    # Dry-run: NADA persistido.
    assert _count_messages(session_factory) == 0


def test_backfill_dedupe_skips_existing(session_factory, monkeypatch):
    owner = _seed(session_factory)
    # Pre-sembrar un email con el mismo gmail_message_id.
    with session_factory() as session:
        from app.models.crm import EmailThread  # noqa: PLC0415

        thread = EmailThread(
            initiated_by_user_id=owner, gmail_thread_id="t1",
            gmail_account_user_id=owner, first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC), message_count=1,
        )
        session.add(thread)
        session.flush()
        session.add(
            EmailMessage(
                thread_id=thread.id, gmail_message_id="dup", direction="inbound",
                gmail_account_user_id=owner, from_email="x@y.com",
                to_emails_json='["norma@bomedia.net"]', sent_at=datetime.now(UTC),
            )
        )
        session.commit()
    fake = _make_fake_client(
        {"INBOX": [{"messages": [{"id": "dup", "threadId": "t1"}]}]},
        {"dup": _raw("dup", "t1", "norma@bomedia.net")},
    )
    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", fake)
    report = _run(session_factory, owner, labels=["INBOX"])
    assert report.skipped_dedupe == 1
    assert report.imported_orphan == 0
    assert _count_messages(session_factory) == 1  # sin duplicar


def test_backfill_orphan_saved_when_sender_not_contact(session_factory, monkeypatch):
    owner = _seed(session_factory)
    fake = _make_fake_client(
        {"INBOX": [{"messages": [{"id": "o1", "threadId": "t1"}]}]},
        {"o1": _raw("o1", "t1", "norma@bomedia.net", frm="rando@nadie.com")},
    )
    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", fake)
    report = _run(session_factory, owner, labels=["INBOX"])
    assert report.imported_orphan == 1
    assert report.imported_linked == 0
    with session_factory() as session:
        msg = session.scalar(select(EmailMessage))
        assert msg.contact_id is None
        assert msg.delivered_to == "norma@bomedia.net"
        assert msg.imported_via == "historic_backfill_universal"


def test_backfill_links_when_sender_is_contact(session_factory, monkeypatch):
    owner = _seed(session_factory)
    with session_factory() as session:
        session.add(Contact(first_name="Cli", email="cliente@fuera.com"))
        session.commit()
    fake = _make_fake_client(
        {"INBOX": [{"messages": [{"id": "l1", "threadId": "t1"}]}]},
        {"l1": _raw("l1", "t1", "norma@bomedia.net", frm="cliente@fuera.com")},
    )
    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", fake)
    report = _run(session_factory, owner, labels=["INBOX"])
    assert report.imported_linked == 1
    assert report.imported_orphan == 0


def test_backfill_skips_message_not_delivered_to_any_alias(session_factory, monkeypatch):
    owner = _seed(session_factory)
    fake = _make_fake_client(
        {"INBOX": [{"messages": [{"id": "n1", "threadId": "t1"}]}]},
        {"n1": _raw("n1", "t1", "info@bomedia.net")},  # alias NO configurado
    )
    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", fake)
    report = _run(session_factory, owner, labels=["INBOX"])
    assert report.skipped_no_alias == 1
    assert report.imported_orphan == 0
    assert report.discard_by_alias.get("info@bomedia.net") == 1
    assert _count_messages(session_factory) == 0


def test_backfill_marks_spam_from_labels(session_factory, monkeypatch):
    owner = _seed(session_factory)
    fake = _make_fake_client(
        {"SPAM": [{"messages": [{"id": "s1", "threadId": "t1"}]}]},
        {"s1": _raw("s1", "t1", "norma@bomedia.net", labels=["SPAM"])},
    )
    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", fake)
    report = _run(session_factory, owner, labels=["SPAM"])
    assert report.spam == 1
    assert report.imported_orphan == 1
    with session_factory() as session:
        assert session.scalar(select(EmailMessage)).is_spam is True


def test_backfill_report_totals_correct(session_factory, monkeypatch):
    owner = _seed(session_factory)
    with session_factory() as session:
        session.add(Contact(first_name="Cli", email="cliente@fuera.com"))
        session.commit()
    msgs = {
        "a": _raw("a", "ta", "norma@bomedia.net", frm="cliente@fuera.com"),  # linked
        "b": _raw("b", "tb", "norma@bomedia.net", frm="rando@x.com"),        # orphan
        "c": _raw("c", "tc", "ajeno@otro.com"),                              # no_alias
        "d": _raw("d", "td", "norma@bomedia.net", labels=["SPAM"], frm="z@z.com"),  # orphan+spam
    }
    fake = _make_fake_client({"INBOX": [_one_page(msgs)]}, msgs)
    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", fake)
    report = _run(session_factory, owner, labels=["INBOX"])
    assert report.imported_linked == 1
    assert report.imported_orphan == 2
    assert report.spam == 1
    assert report.skipped_no_alias == 1
    assert report.total_processed == 4  # 1 linked + 2 orphan + 1 no_alias
    assert _count_messages(session_factory) == 3  # los 3 importados


def test_backfill_dry_run_limit_respected(session_factory, monkeypatch):
    owner = _seed(session_factory)
    msgs = {
        f"m{i}": _raw(f"m{i}", f"t{i}", "norma@bomedia.net") for i in range(5)
    }
    fake = _make_fake_client({"INBOX": [_one_page(msgs)]}, msgs)
    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", fake)
    report = _run(
        session_factory, owner, dry_run=True, dry_run_limit=2, labels=["INBOX"]
    )
    assert report.total_processed == 2  # cortó a los 2 primeros examinados
    assert _count_messages(session_factory) == 0
