"""Tests para la migración 20260618_0055 (backfill
email_threads.contact_id).

Bug residual del PR-Ficha-Cleanup: muchos threads históricos tienen
contact_id NULL aunque sus mensajes referencian un contacto. La
migración asocia retroactivamente vía email match.

Cubrimos:
- Outbound: thread con un mensaje cuyo `to_emails_json` matchea un
  contacto → thread.contact_id = contacto.
- Inbound: thread con un mensaje cuyo `from_email` matchea →
  thread.contact_id = contacto.
- Thread con contact_id ya seteado → NO se sobreescribe.
- Thread con email no asociado a ningún contacto → contact_id sigue
  NULL.
- Contact inactivo (is_active=0) → NO matchea (Bart no quiere
  resurrecciones).
- Case-insensitive: contacto `Ana@Bomedia.NET` matchea `to_emails_
  json=["ana@bomedia.net"]`.
"""
from __future__ import annotations

import importlib.util
import json
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.crm import (
    Base,
    Contact,
    EmailDirection,
    EmailMessage,
    EmailThread,
)


def _load_migration():
    # `alembic/versions/` no es un paquete Python; lo cargamos por
    # spec para acceder a `upgrade()` desde el test.
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260618_0055_backfill_email_threads_contact_id.py"
    )
    spec = importlib.util.spec_from_file_location("backfill_0055", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_migration()


def _new_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)


def _run_backfill(factory: sessionmaker) -> None:
    """Invoca el `upgrade()` de la migración usando una conexión
    bindeada al engine SQLite del test."""
    with factory() as session:
        engine = session.get_bind()
    with engine.begin() as conn:
        # `op.get_bind()` no funciona en tests sin un alembic context;
        # parcheamos la función del módulo migración temporalmente
        # para que devuelva nuestra conexión.
        import alembic.op as op_module  # noqa: PLC0415

        original_get_bind = op_module.get_bind
        op_module.get_bind = lambda: conn  # type: ignore[assignment]
        try:
            _MIGRATION.upgrade()
        finally:
            op_module.get_bind = original_get_bind  # type: ignore[assignment]


def _seed_user(session: Session) -> str:
    """Mínimo user para satisfacer FKs de email_threads."""
    from app.core.security import hash_password  # noqa: PLC0415
    from app.models.crm import User, UserRole  # noqa: PLC0415

    user = User(
        id=_new_id(),
        email="op@example.com",
        full_name="Op",
        password_hash=hash_password("x" * 12),
        role=UserRole.USER,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user.id


def _seed_contact(
    session: Session,
    *,
    email: str,
    is_active: bool = True,
) -> str:
    contact = Contact(
        id=_new_id(),
        first_name="Test",
        email=email,
        commercial_status="new",
        is_active=is_active,
    )
    session.add(contact)
    session.flush()
    return contact.id


def _seed_thread(
    session: Session,
    *,
    user_id: str,
    contact_id: str | None = None,
) -> str:
    thread = EmailThread(
        id=_new_id(),
        gmail_account_user_id=user_id,
        initiated_by_user_id=user_id,
        gmail_thread_id=_new_id(),
        contact_id=contact_id,
        subject="Test",
        participants_json="[]",
        first_message_at=datetime.now(UTC),
        last_message_at=datetime.now(UTC),
        message_count=1,
    )
    session.add(thread)
    session.flush()
    return thread.id


def _seed_message(
    session: Session,
    *,
    thread_id: str,
    user_id: str,
    direction: EmailDirection,
    from_email: str,
    to_emails: list[str],
    sent_at: datetime | None = None,
) -> None:
    msg = EmailMessage(
        id=_new_id(),
        thread_id=thread_id,
        gmail_message_id=_new_id(),
        gmail_account_user_id=user_id,
        direction=direction,
        from_email=from_email,
        to_emails_json=json.dumps(to_emails),
        sent_at=sent_at or datetime.now(UTC),
    )
    session.add(msg)
    session.flush()


def test_backfill_outbound_links_thread_to_contact(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        user_id = _seed_user(session)
        contact_id = _seed_contact(session, email="ana@bomedia.net")
        thread_id = _seed_thread(session, user_id=user_id, contact_id=None)
        _seed_message(
            session,
            thread_id=thread_id,
            user_id=user_id,
            direction=EmailDirection.OUTBOUND,
            from_email="me@bomedia.net",
            to_emails=["ana@bomedia.net"],
        )
        session.commit()

    _run_backfill(session_factory)

    with session_factory() as session:
        thread = session.get(EmailThread, thread_id)
        assert thread is not None
        assert thread.contact_id == contact_id


def test_backfill_inbound_links_thread_to_sender_contact(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        user_id = _seed_user(session)
        contact_id = _seed_contact(session, email="cliente@empresa.com")
        thread_id = _seed_thread(session, user_id=user_id, contact_id=None)
        _seed_message(
            session,
            thread_id=thread_id,
            user_id=user_id,
            direction=EmailDirection.INBOUND,
            from_email="cliente@empresa.com",
            to_emails=["me@bomedia.net"],
        )
        session.commit()

    _run_backfill(session_factory)

    with session_factory() as session:
        thread = session.get(EmailThread, thread_id)
        assert thread.contact_id == contact_id


def test_backfill_preserves_existing_contact_id(
    session_factory: sessionmaker,
) -> None:
    """Threads que ya tenían contact_id NO se tocan — incluso si otro
    contacto matchearía mejor."""
    with session_factory() as session:
        user_id = _seed_user(session)
        original_id = _seed_contact(session, email="original@bomedia.net")
        other_id = _seed_contact(session, email="other@bomedia.net")
        thread_id = _seed_thread(
            session, user_id=user_id, contact_id=original_id
        )
        _seed_message(
            session,
            thread_id=thread_id,
            user_id=user_id,
            direction=EmailDirection.OUTBOUND,
            from_email="me@bomedia.net",
            to_emails=["other@bomedia.net"],
        )
        session.commit()

    _run_backfill(session_factory)

    with session_factory() as session:
        thread = session.get(EmailThread, thread_id)
        assert thread.contact_id == original_id
        _ = other_id  # silenciar linter


def test_backfill_skips_thread_without_contact_match(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        user_id = _seed_user(session)
        _seed_contact(session, email="ana@bomedia.net")
        thread_id = _seed_thread(session, user_id=user_id, contact_id=None)
        _seed_message(
            session,
            thread_id=thread_id,
            user_id=user_id,
            direction=EmailDirection.OUTBOUND,
            from_email="me@bomedia.net",
            to_emails=["random@external.com"],
        )
        session.commit()

    _run_backfill(session_factory)

    with session_factory() as session:
        thread = session.get(EmailThread, thread_id)
        assert thread.contact_id is None


def test_backfill_ignores_inactive_contacts(
    session_factory: sessionmaker,
) -> None:
    """Bart deshabilitó un contacto a propósito — no queremos
    resucitarlo via backfill de threads."""
    with session_factory() as session:
        user_id = _seed_user(session)
        _seed_contact(
            session, email="archived@empresa.com", is_active=False
        )
        thread_id = _seed_thread(session, user_id=user_id, contact_id=None)
        _seed_message(
            session,
            thread_id=thread_id,
            user_id=user_id,
            direction=EmailDirection.OUTBOUND,
            from_email="me@bomedia.net",
            to_emails=["archived@empresa.com"],
        )
        session.commit()

    _run_backfill(session_factory)

    with session_factory() as session:
        thread = session.get(EmailThread, thread_id)
        assert thread.contact_id is None


def test_backfill_case_insensitive_match(
    session_factory: sessionmaker,
) -> None:
    """Contacto guardado con caps mixtos debe matchear emails lower
    en el JSON (los flows de envío lowercaseaban ya el email, pero
    los imports legacy pueden haber dejado caps)."""
    with session_factory() as session:
        user_id = _seed_user(session)
        # Contact guardado con caps mixtos — el flow de creación los
        # lowercasea, pero datos legacy pueden tenerlos.
        contact_id = _seed_contact(session, email="Ana@Bomedia.NET")
        thread_id = _seed_thread(session, user_id=user_id, contact_id=None)
        _seed_message(
            session,
            thread_id=thread_id,
            user_id=user_id,
            direction=EmailDirection.OUTBOUND,
            from_email="me@bomedia.net",
            to_emails=["ana@bomedia.net"],
        )
        session.commit()

    _run_backfill(session_factory)

    with session_factory() as session:
        thread = session.get(EmailThread, thread_id)
        assert thread.contact_id == contact_id


def test_backfill_oldest_message_wins(
    session_factory: sessionmaker,
) -> None:
    """Si un thread tiene mensajes para 2 contactos distintos, gana
    el match del mensaje MÁS antiguo (estable + intuitivo: quien
    abrió la conversación)."""
    with session_factory() as session:
        user_id = _seed_user(session)
        oldest_id = _seed_contact(session, email="first@empresa.com")
        _seed_contact(session, email="second@empresa.com")
        thread_id = _seed_thread(session, user_id=user_id, contact_id=None)
        _seed_message(
            session,
            thread_id=thread_id,
            user_id=user_id,
            direction=EmailDirection.OUTBOUND,
            from_email="me@bomedia.net",
            to_emails=["first@empresa.com"],
            sent_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
        )
        _seed_message(
            session,
            thread_id=thread_id,
            user_id=user_id,
            direction=EmailDirection.OUTBOUND,
            from_email="me@bomedia.net",
            to_emails=["second@empresa.com"],
            sent_at=datetime(2026, 1, 2, 12, tzinfo=UTC),
        )
        session.commit()

    _run_backfill(session_factory)

    with session_factory() as session:
        thread = session.get(EmailThread, thread_id)
        assert thread.contact_id == oldest_id
