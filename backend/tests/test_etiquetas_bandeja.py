"""CRM-ETIQUETAS-EN-BANDEJA — chips de etiquetas en la fila del asunto.

`GET /api/emails/threads` expone en cada hilo la UNIÓN de sus etiquetas:
las del propio hilo (personales del CRM) + las de Gmail heredadas de sus
mensajes. Se cargan en UNA query batch por página (el nº de queries no
crece con el nº de hilos) y se excluyen las de sistema y las ocultas.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Registra TODOS los modelos en Base.metadata.
import app.main  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    EmailDirection,
    EmailLabel,
    EmailMessage,
    EmailMessageLabel,
    EmailThread,
    EmailThreadLabel,
    User,
    UserEmailAlias,
    UserRole,
)
from tests._test_helpers import auth_headers, seed_test_users

ALIAS = "norma@bomedia.net"
NOW = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sf() as seed:
        seed_test_users(seed)
        owner = seed.scalar(select(User.id).where(User.role == UserRole.USER))
        seed.add(UserEmailAlias(user_id=owner, alias_email=ALIAS, active=True))
        seed.commit()
    sf.kw["bind"] = engine  # el test de N+1 necesita el engine
    yield sf
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _uid(session: Session, role: UserRole = UserRole.USER) -> str:
    uid = session.scalar(select(User.id).where(User.role == role))
    assert uid
    return uid


def _seed_thread(
    session: Session, *, uid: str, gid: str, message_count: int = 1
) -> str:
    thread = EmailThread(
        initiated_by_user_id=uid,
        gmail_thread_id=gid,
        gmail_account_user_id=uid,
        subject=f"Asunto {gid}",
        first_message_at=NOW,
        last_message_at=NOW,
        message_count=message_count,
    )
    session.add(thread)
    session.flush()
    for idx in range(message_count):
        session.add(
            EmailMessage(
                thread_id=thread.id,
                gmail_message_id=f"{gid}-m{idx}",
                gmail_account_user_id=uid,
                direction=EmailDirection.INBOUND,
                from_email="cliente@fuera.com",
                to_emails_json='["norma@bomedia.net"]',
                delivered_to=ALIAS,
                sent_at=NOW,
            )
        )
    session.flush()
    return thread.id


def _gmail_label(
    session: Session,
    *,
    gid: str = "Label_1",
    name: str = "AA Facturas",
    color: str | None = "#fb4c2f",
    text_color: str | None = "#ffffff",
    is_hidden: bool = False,
    is_system: bool = False,
) -> str:
    label = EmailLabel(
        user_id=None,
        name=name,
        color=color,
        text_color=text_color,
        gmail_label_id=gid,
        is_hidden=is_hidden,
        is_system=is_system,
    )
    session.add(label)
    session.flush()
    return label.id


def _tag_messages(
    session: Session, *, thread_id: str, label_id: str
) -> None:
    for msg_id in session.scalars(
        select(EmailMessage.id).where(EmailMessage.thread_id == thread_id)
    ):
        session.add(
            EmailMessageLabel(
                message_id=msg_id, label_id=label_id, applied_at=NOW
            )
        )
    session.flush()


def _row(payload: dict, thread_id: str) -> dict:
    return next(it for it in payload["items"] if it["id"] == thread_id)


# ---------------------------------------------------------------------------


def test_list_threads_includes_gmail_labels_with_both_colors(
    client: TestClient, factory: sessionmaker
) -> None:
    with factory() as session:
        uid = _uid(session)
        thread_id = _seed_thread(session, uid=uid, gid="t1")
        label_id = _gmail_label(session)
        _tag_messages(session, thread_id=thread_id, label_id=label_id)
        session.commit()

    response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200, response.text
    labels = _row(response.json(), thread_id)["labels"]
    assert len(labels) == 1
    assert labels[0]["name"] == "AA Facturas"
    assert labels[0]["color"] == "#fb4c2f"  # backgroundColor de Gmail
    assert labels[0]["text_color"] == "#ffffff"  # textColor de Gmail
    assert labels[0]["gmail_label_id"] == "Label_1"


def test_list_threads_labels_are_distinct_across_thread_messages(
    client: TestClient, factory: sessionmaker
) -> None:
    """Tres mensajes del hilo con la MISMA label → un solo chip."""
    with factory() as session:
        uid = _uid(session)
        thread_id = _seed_thread(session, uid=uid, gid="t1", message_count=3)
        label_id = _gmail_label(session)
        _tag_messages(session, thread_id=thread_id, label_id=label_id)
        session.commit()

    response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    labels = _row(response.json(), thread_id)["labels"]
    assert [lbl["id"] for lbl in labels] == [label_id]


def test_list_threads_merges_thread_and_message_labels(
    client: TestClient, factory: sessionmaker
) -> None:
    """La fila une la etiqueta personal del hilo con la de Gmail del
    mensaje, ordenadas de forma estable."""
    with factory() as session:
        uid = _uid(session)
        thread_id = _seed_thread(session, uid=uid, gid="t1")
        gmail_id = _gmail_label(session, name="AA Facturas")
        personal = EmailLabel(user_id=uid, name="Mía", sort_order=0)
        session.add(personal)
        session.flush()
        session.add(
            EmailThreadLabel(
                thread_id=thread_id, label_id=personal.id, applied_at=NOW
            )
        )
        _tag_messages(session, thread_id=thread_id, label_id=gmail_id)
        session.commit()

    response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    labels = _row(response.json(), thread_id)["labels"]
    assert [lbl["name"] for lbl in labels] == ["AA Facturas", "Mía"]


def test_list_threads_excludes_hidden_and_system_labels(
    client: TestClient, factory: sessionmaker
) -> None:
    """La etiqueta «cajón de sastre» oculta con `hide_label` no ensucia la
    fila; las de sistema tampoco."""
    with factory() as session:
        uid = _uid(session)
        thread_id = _seed_thread(session, uid=uid, gid="t1")
        visible = _gmail_label(session, gid="Label_1", name="AA Facturas")
        hidden = _gmail_label(
            session,
            gid="Label_2",
            name="- Bart - todos los emails",
            is_hidden=True,
        )
        system = _gmail_label(
            session, gid="Label_3", name="Sistema", is_system=True
        )
        for label_id in (visible, hidden, system):
            _tag_messages(session, thread_id=thread_id, label_id=label_id)
        session.commit()

    response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    labels = _row(response.json(), thread_id)["labels"]
    assert [lbl["name"] for lbl in labels] == ["AA Facturas"]


def test_list_threads_without_labels_returns_empty_list(
    client: TestClient, factory: sessionmaker
) -> None:
    with factory() as session:
        uid = _uid(session)
        thread_id = _seed_thread(session, uid=uid, gid="t1")
        session.commit()

    response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    assert _row(response.json(), thread_id)["labels"] == []


def test_list_threads_labels_no_n_plus_1(
    client: TestClient, factory: sessionmaker
) -> None:
    """El nº de queries del listado NO crece con el nº de hilos: las
    etiquetas se cargan en una sola query batch por página."""
    engine = factory.kw["bind"]

    def _count_queries(thread_count: int) -> int:
        with factory() as session:
            uid = _uid(session)
            for idx in range(thread_count):
                thread_id = _seed_thread(session, uid=uid, gid=f"t{idx}")
                label_id = _gmail_label(
                    session, gid=f"Label_{idx}", name=f"Etiqueta {idx}"
                )
                _tag_messages(session, thread_id=thread_id, label_id=label_id)
            session.commit()

        statements: list[str] = []

        def _before_cursor_execute(
            _conn, _cursor, statement, _params, _context, _executemany
        ):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _before_cursor_execute)
        try:
            response = client.get(
                "/api/emails/threads", headers=auth_headers(client, "user")
            )
        finally:
            event.remove(
                engine, "before_cursor_execute", _before_cursor_execute
            )
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == thread_count
        return len(statements)

    with_two = _count_queries(2)
    # Limpia y repite con 10 hilos.
    with factory() as session:
        session.query(EmailMessageLabel).delete()
        session.query(EmailThreadLabel).delete()
        session.query(EmailLabel).delete()
        session.query(EmailMessage).delete()
        session.query(EmailThread).delete()
        session.commit()
    with_ten = _count_queries(10)

    assert with_ten == with_two, (
        f"El listado hace N+1: {with_two} queries con 2 hilos vs "
        f"{with_ten} con 10."
    )
