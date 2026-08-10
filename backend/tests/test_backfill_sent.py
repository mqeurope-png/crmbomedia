"""CRM-BACKFILL-SENT — captura de mails ENVIADOS desde Gmail directo.

Cubre la detección de dirección en `_persist_message` (From = alias activo →
outbound, propietario = dueño del alias, contacto por destinatarios), la
regresión del gate inbound por `delivered_to`, el default de labels del
backfill universal con SENT y el flujo completo backfill/push para un
mensaje SENT.
"""
from __future__ import annotations

import base64
import inspect
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Importa la app para registrar TODOS los modelos en Base.metadata (los
# templates/folders viven fuera de app.models.crm y create_all los necesita).
import app.main  # noqa: F401
from app.integrations.gmail import service as gmail_service
from app.integrations.gmail.backfill_universal import run_backfill_universal
from app.models.crm import (
    Base,
    Contact,
    EmailDirection,
    EmailMessage,
    EmailThread,
    GmailPubsubWatch,
    User,
    UserEmailAlias,
    UserRole,
)
from app.services.email_aliases import active_alias_map
from tests._test_helpers import (
    seed_org_google_integration,
    seed_test_users,
)

ALIAS = "norma@bomedia.net"


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
        owner = seed.scalar(select(User.id).where(User.role == UserRole.USER))
        seed.add(
            UserEmailAlias(user_id=owner, alias_email=ALIAS, active=True)
        )
        seed_org_google_integration(seed, connected_by_user_id=owner)
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


def _owner_id(session: Session) -> str:
    uid = session.scalar(select(User.id).where(User.role == UserRole.USER))
    assert uid
    return uid


def _raw(
    mid: str,
    thread_id: str,
    *,
    from_addr: str,
    to: str,
    cc: str | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    headers = [
        {"name": "From", "value": from_addr},
        {"name": "To", "value": to},
        {"name": "Subject", "value": "Oferta rotulación"},
        {"name": "Date", "value": "Mon, 03 Aug 2026 10:00:00 +0000"},
    ]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    return {
        "id": mid,
        "threadId": thread_id,
        "snippet": "hola",
        "labelIds": labels if labels is not None else ["SENT"],
        "payload": {
            "headers": headers,
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"cuerpo").decode()},
        },
    }


# ---------------------------------------------------------------------------
# _persist_message — dirección
# ---------------------------------------------------------------------------


def test_persist_message_outbound_saves_with_direction_outbound(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        owner = _owner_id(session)
        alias_map = active_alias_map(session)
        message = gmail_service._persist_message(
            session,
            user_id=owner,
            raw=_raw("s1", "ts1", from_addr=ALIAS, to="cliente@fuera.com"),
            gmail_thread_id="ts1",
            alias_map=alias_map,
            emit_activity=False,
            imported_via="historical_backfill",
        )
        session.commit()
        assert message is not None
        assert message.direction == EmailDirection.OUTBOUND
        # delivered_to no aplica en outbound.
        assert message.delivered_to is None
        # Un mail enviado no marca el hilo como no leído.
        thread = session.get(EmailThread, message.thread_id)
        assert thread is not None
        assert thread.has_unread_replies is False


def test_persist_message_outbound_owner_is_alias_owner(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        owner = _owner_id(session)
        admin = session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        alias_map = active_alias_map(session)
        # `user_id` (cuenta org) es el ADMIN, pero el alias del From es de
        # Norma → el thread nuevo debe pertenecer a Norma.
        message = gmail_service._persist_message(
            session,
            user_id=admin,
            raw=_raw("s2", "ts2", from_addr=ALIAS, to="cliente@fuera.com"),
            gmail_thread_id="ts2",
            alias_map=alias_map,
            emit_activity=False,
        )
        session.commit()
        assert message is not None
        assert message.created_by_user_id == owner
        thread = session.get(EmailThread, message.thread_id)
        assert thread is not None
        assert thread.initiated_by_user_id == owner


def test_persist_message_outbound_contact_matched_by_to_not_from(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        owner = _owner_id(session)
        contact = Contact(first_name="Eva", email="eva@cliente.com")
        session.add(contact)
        session.flush()
        alias_map = active_alias_map(session)
        message = gmail_service._persist_message(
            session,
            user_id=owner,
            raw=_raw(
                "s3",
                "ts3",
                from_addr=ALIAS,
                to="otro@fuera.com",
                cc="eva@cliente.com",
            ),
            gmail_thread_id="ts3",
            alias_map=alias_map,
            emit_activity=False,
        )
        session.commit()
        assert message is not None
        # El contacto se casa por los destinatarios (To/Cc), no por el From.
        assert message.contact_id == contact.id


def test_persist_message_still_gates_inbound_by_delivered_to(
    session_factory: sessionmaker,
) -> None:
    """Regresión: un inbound que no va a ningún alias configurado sigue
    descartándose (la captura outbound no relaja el gate)."""
    with session_factory() as session:
        owner = _owner_id(session)
        alias_map = active_alias_map(session)
        result = gmail_service._persist_message(
            session,
            user_id=owner,
            raw=_raw(
                "s4",
                "ts4",
                from_addr="desconocido@fuera.com",
                to="nadie@otro-dominio.com",
                labels=["INBOX"],
            ),
            gmail_thread_id="ts4",
            alias_map=alias_map,
            emit_activity=False,
        )
        assert result is None
        assert session.scalar(select(EmailMessage)) is None


# ---------------------------------------------------------------------------
# Backfill universal — SENT
# ---------------------------------------------------------------------------


def test_backfill_universal_default_labels_include_sent() -> None:
    default = inspect.signature(run_backfill_universal).parameters["labels"].default
    assert tuple(default) == ("INBOX", "SPAM", "SENT")


def test_backfill_universal_sent_message_creates_outbound_row(
    session_factory: sessionmaker,
) -> None:
    class _Fake:
        def list_messages(
            self,
            *,
            query: str,
            page_size: int = 100,
            page_token: str | None = None,
            label_ids: list[str] | None = None,
        ) -> dict[str, Any]:
            if label_ids == ["SENT"]:
                return {"messages": [{"id": "sent-1", "threadId": "tsent"}]}
            return {"messages": []}

        def get_message(self, mid: str) -> dict[str, Any]:
            return _raw(mid, "tsent", from_addr=ALIAS, to="cliente@fuera.com")

    with session_factory() as session:
        owner = _owner_id(session)
        with patch.object(
            gmail_service, "_client_for", return_value=_Fake()
        ):
            report = run_backfill_universal(
                session,
                user_id=owner,
                since=datetime(2026, 2, 7).date(),
                until=datetime(2026, 8, 10).date(),
                progress=lambda _line: None,
            )
        session.commit()

        assert report.outbound == 1
        msg = session.scalar(select(EmailMessage))
        assert msg is not None
        assert msg.direction == EmailDirection.OUTBOUND
        assert msg.from_email == ALIAS


def test_backfill_universal_sent_from_non_alias_discarded(
    session_factory: sessionmaker,
) -> None:
    """SENT cuyo From no es alias del CRM (forward raro) → descartado."""

    class _Fake:
        def list_messages(
            self,
            *,
            query: str,
            page_size: int = 100,
            page_token: str | None = None,
            label_ids: list[str] | None = None,
        ) -> dict[str, Any]:
            if label_ids == ["SENT"]:
                return {"messages": [{"id": "sent-x", "threadId": "tx"}]}
            return {"messages": []}

        def get_message(self, mid: str) -> dict[str, Any]:
            return _raw(
                mid,
                "tx",
                from_addr="ajeno@fuera.com",
                to="tercero@otro.com",
            )

    with session_factory() as session:
        owner = _owner_id(session)
        with patch.object(
            gmail_service, "_client_for", return_value=_Fake()
        ):
            report = run_backfill_universal(
                session,
                user_id=owner,
                since=datetime(2026, 2, 7).date(),
                until=datetime(2026, 8, 10).date(),
                progress=lambda _line: None,
            )
        assert report.skipped_no_alias == 1
        assert report.outbound == 0
        assert session.scalar(select(EmailMessage)) is None


# ---------------------------------------------------------------------------
# Push real-time — stub SENT
# ---------------------------------------------------------------------------


def test_process_history_captures_sent_stub_outbound(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        owner = _owner_id(session)
        session.add(
            GmailPubsubWatch(
                user_id=owner,
                history_id=1,
                watch_expires_at=datetime.now(UTC) + timedelta(days=6),
                last_renewed_at=datetime.now(UTC),
                topic_name="projects/x/topics/y",
            )
        )
        session.commit()

    class _Fake:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_history(self, _start: int) -> dict[str, Any]:
            return {
                "history": [
                    {
                        "messagesAdded": [
                            {
                                "message": {
                                    "id": "rt-sent",
                                    "threadId": "trt",
                                    "labelIds": ["SENT"],
                                }
                            }
                        ]
                    }
                ]
            }

        def get_message(self, _mid: str) -> dict[str, Any]:
            return _raw("rt-sent", "trt", from_addr=ALIAS, to="cliente@fuera.com")

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _Fake
    )
    with session_factory() as session:
        imported = gmail_service.process_history(
            session, user_id=_owner_id(session), new_history_id=200
        )
        session.commit()
    assert imported == 1
    with session_factory() as session:
        msg = session.scalar(select(EmailMessage))
        assert msg is not None
        assert msg.direction == EmailDirection.OUTBOUND
        assert msg.imported_via == "incoming_realtime"
