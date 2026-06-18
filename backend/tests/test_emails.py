"""Sprint Email v1 smoke tests — send + thread + webhook flow.

The whole Gmail upstream is mocked. We assert that:

- POST /api/emails/send persists an outbound `email_threads` +
  `email_messages` row and emits an `activity_event`.
- A reply imported via `process_history` lands in the same thread
  and flips `has_unread_replies` on.
- The webhook receiver accepts a well-formed Pub/Sub push, enqueues
  the job, and a follow-up GET surfaces the new message.
- Admin endpoint is admin-only.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.crypto import encrypt
from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    EmailMessage,
    EmailThread,
    User,
    UserGoogleIntegration,
    UserRole,
)
from tests._test_helpers import auth_headers, seed_test_users


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


@pytest.fixture()
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _user_id(session: Session, role: UserRole) -> str:
    return session.scalar(select(User.id).where(User.role == role))


def _seed_gmail_integration(
    session_factory: sessionmaker,
    *,
    user_id: str,
    allowed_aliases: tuple[str, ...] = ("info@bomedia.net",),
) -> None:
    """Seed the Gmail integration row + one alias preference per
    `allowed_aliases`. The first alias becomes the default. Tests
    that want to exercise the "alias not in prefs" path should pass
    `allowed_aliases=()`."""
    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

    with session_factory() as session:
        session.add(
            UserGoogleIntegration(
                user_id=user_id,
                google_email="bart@bomedia.net",
                access_token_encrypted=encrypt("access"),
                refresh_token_encrypted=encrypt("refresh"),
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                scopes=(
                    "https://www.googleapis.com/auth/calendar.events "
                    "https://www.googleapis.com/auth/gmail.send "
                    "https://www.googleapis.com/auth/gmail.modify"
                ),
                connected_at=datetime.now(UTC),
            )
        )
        for idx, alias in enumerate(allowed_aliases):
            session.add(
                UserEmailAliasPref(
                    user_id=user_id,
                    alias_email=alias,
                    is_allowed=True,
                    is_default=idx == 0,
                )
            )
        session.commit()


def test_send_email_persists_thread_and_message(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    sent: list[dict[str, Any]] = []

    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def send_message(self, **kwargs: Any) -> dict[str, Any]:
            sent.append(kwargs)
            return {"id": "gmail-msg-1", "threadId": "gmail-thr-1"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )

    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["client@example.com"],
            "subject": "Hola",
            "body_text": "Cuerpo del email",
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["direction"] == "outbound"
    assert body["from_email"] == "info@bomedia.net"
    assert sent[0]["from_alias"] == "info@bomedia.net"

    with session_factory() as session:
        threads = list(session.scalars(select(EmailThread)))
        assert len(threads) == 1
        assert threads[0].gmail_thread_id == "gmail-thr-1"
        msgs = list(session.scalars(select(EmailMessage)))
        assert len(msgs) == 1
        assert msgs[0].gmail_message_id == "gmail-msg-1"


def test_send_email_without_gmail_scope_returns_403(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    """A user who connected Google for Calendar but didn't grant
    gmail.send should get a clean 403 with the reauth hint, not a
    500."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        session.add(
            UserGoogleIntegration(
                user_id=uid,
                google_email="bart@bomedia.net",
                access_token_encrypted=encrypt("access"),
                refresh_token_encrypted=encrypt("refresh"),
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                scopes="https://www.googleapis.com/auth/calendar.events",
                connected_at=datetime.now(UTC),
            )
        )
        session.commit()
    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["client@example.com"],
            "subject": "x",
            "body_text": "x",
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 403
    assert "autorizar" in response.json()["detail"].lower()


def test_process_history_imports_inbound_reply(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    class _FakeClient:
        history_id_counter = 100

        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def send_message(self, **_kwargs: Any) -> dict[str, Any]:
            return {"id": "out-1", "threadId": "thr-A"}

        def list_history(self, _start: int) -> dict[str, Any]:
            return {
                "history": [
                    {
                        "messagesAdded": [
                            {"message": {"id": "in-1", "threadId": "thr-A"}}
                        ]
                    }
                ]
            }

        def get_message(self, _mid: str) -> dict[str, Any]:
            return {
                "id": "in-1",
                "snippet": "Reply preview",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "client@example.com"},
                        {"name": "To", "value": "info@bomedia.net"},
                        {"name": "Subject", "value": "Re: Hola"},
                        {
                            "name": "Date",
                            "value": "Fri, 31 Dec 2099 23:59:00 +0000",
                        },
                    ],
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(
                            b"Mi respuesta"
                        ).decode()
                    },
                },
            }

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )

    # First, send so a thread exists.
    client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["client@example.com"],
            "subject": "Hola",
            "body_text": "Body",
        },
        headers=auth_headers(client, "user"),
    )
    with session_factory() as session:
        thread = session.scalar(select(EmailThread))
        assert thread is not None
        gmail_thread_id = thread.gmail_thread_id
        # Patch the thread's gmail_thread_id to match the fake
        # `list_history` output (the send mock returned "thr-A").
        if gmail_thread_id != "thr-A":
            thread.gmail_thread_id = "thr-A"
        # Seed the watch row so process_history can resume.
        from app.models.crm import GmailPubsubWatch  # noqa: PLC0415

        session.add(
            GmailPubsubWatch(
                user_id=thread.gmail_account_user_id,
                history_id=1,
                watch_expires_at=datetime.now(UTC) + timedelta(days=6),
                last_renewed_at=datetime.now(UTC),
                topic_name="projects/x/topics/y",
            )
        )
        session.commit()
        owner_id = thread.gmail_account_user_id

    # Run the processor.
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    with session_factory() as session:
        imported = gmail_service.process_history(
            session, user_id=owner_id, new_history_id=200
        )
        session.commit()
    assert imported == 1

    with session_factory() as session:
        thread = session.scalar(select(EmailThread))
        assert thread is not None
        assert thread.has_unread_replies is True
        msgs = list(
            session.scalars(
                select(EmailMessage).order_by(EmailMessage.sent_at)
            )
        )
        assert [m.direction.value for m in msgs] == ["outbound", "inbound"]


def test_webhook_routes_to_user_and_enqueues(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receiver must find the user by email + push the job onto
    the queue without doing any heavy work itself."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    enqueued: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.integrations.gmail.jobs.enqueue_process_history",
        lambda *, user_id, new_history_id: enqueued.append(
            (user_id, new_history_id)
        ),
    )
    # The webhook does a late-import of the jobs module, so also
    # patch the symbol referenced inside the handler.
    monkeypatch.setattr(
        "app.integrations.gmail.webhook._validate_jwt",
        lambda _auth: None,
    )

    payload = {
        "message": {
            "data": base64.b64encode(
                json.dumps(
                    {"emailAddress": "bart@bomedia.net", "historyId": 500}
                ).encode()
            ).decode(),
        }
    }
    response = client.post("/api/webhooks/gmail", json=payload)
    assert response.status_code == 200
    # Fan-out (commit 1 of this PR) added a `users` counter to the
    # webhook response so we can verify multi-user routing from the
    # response body.
    assert response.json() == {"status": "enqueued", "users": 1}
    assert enqueued == [(uid, 500)]


def test_admin_threads_view_admin_only(
    client: TestClient, session_factory: sessionmaker
) -> None:
    blocked = client.get(
        "/api/emails/admin/all-threads",
        headers=auth_headers(client, "user"),
    )
    assert blocked.status_code == 403
    ok = client.get(
        "/api/emails/admin/all-threads",
        headers=auth_headers(client, "admin"),
    )
    assert ok.status_code == 200
    assert ok.json() == {"items": [], "total": 0}


def test_threads_list_scopes_to_current_user(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QoL sprint: el default es scope=mine para TODOS los roles
    (antes manager/admin veían todo por defecto, ahora explicito con
    `?scope=team`). El user role nunca puede subir a `team`."""
    with session_factory() as session:
        user_id = _user_id(session, UserRole.USER)
        admin_id = _user_id(session, UserRole.ADMIN)
        for owner in (user_id, admin_id):
            session.add(
                EmailThread(
                    initiated_by_user_id=owner,
                    gmail_thread_id=f"thr-{owner[:6]}",
                    gmail_account_user_id=owner,
                    first_message_at=datetime.now(UTC),
                    last_message_at=datetime.now(UTC),
                    message_count=1,
                )
            )
        session.commit()

    _ = monkeypatch
    # User: default mine → 1 thread propio.
    user_response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    assert user_response.json()["total"] == 1
    # Admin: default mine → 1 thread propio (cambio vs pre-QoL).
    admin_default = client.get(
        "/api/emails/threads", headers=auth_headers(client, "admin")
    )
    assert admin_default.json()["total"] == 1
    # Admin con scope=team → ambos.
    admin_team = client.get(
        "/api/emails/threads?scope=team",
        headers=auth_headers(client, "admin"),
    )
    assert admin_team.json()["total"] == 2


def test_threads_filtered_by_contact_skip_user_scope(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    """PR-Contact-Emails-Team. La pestaña Emails de la ficha contacto
    es COLABORATIVA: muestra TODOS los threads del contacto sin
    filtrar por quién los envió. Bug pre-fix: Bart abría la ficha de
    un cliente que Manel había contactado, veía "sin emails" porque
    el endpoint filtraba por `initiated_by_user_id == bart.id`.

    Spec: cuando el filtro contact_id está presente, el scope=mine
    default se SALTA. La bandeja general (`/emails` sin contact_id)
    sigue siendo per-user."""
    from app.models.crm import Contact  # noqa: PLC0415

    with session_factory() as session:
        # bart = el operador que abre la ficha (no es el sender);
        # manel = el sender histórico que envió el email.
        manel_id = _user_id(session, UserRole.ADMIN)
        contact = Contact(
            first_name="Salome",
            email="sara_kali@hotmail.es",
            commercial_status="new",
            is_active=True,
        )
        session.add(contact)
        session.flush()
        contact_id = contact.id
        # Thread enviado por Manel al contacto. Bart NO es el sender,
        # pero al abrir la ficha del contacto debería verlo.
        session.add(
            EmailThread(
                contact_id=contact_id,
                initiated_by_user_id=manel_id,
                gmail_thread_id="thr-manel-to-salome",
                gmail_account_user_id=manel_id,
                first_message_at=datetime.now(UTC),
                last_message_at=datetime.now(UTC),
                message_count=1,
            )
        )
        # Thread huérfano (contact_id=None) enviado por Manel.
        # NO debe aparecer en la ficha del contacto.
        session.add(
            EmailThread(
                contact_id=None,
                initiated_by_user_id=manel_id,
                gmail_thread_id="thr-manel-other",
                gmail_account_user_id=manel_id,
                first_message_at=datetime.now(UTC),
                last_message_at=datetime.now(UTC),
                message_count=1,
            )
        )
        session.commit()

    # Bart abre la ficha del contacto → debe ver el thread de Manel.
    response = client.get(
        f"/api/emails/threads?contact_id={contact_id}",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1, body
    assert body["items"][0]["gmail_thread_id"] == "thr-manel-to-salome"


def test_bandeja_general_keeps_user_scope_default(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    """El fix anterior NO debe regresar la bandeja general (`/emails`)
    a mostrar emails ajenos. Sin contact_id, scope=mine sigue
    filtrando por initiated_by_user_id == current_user.id."""
    with session_factory() as session:
        bart_id = _user_id(session, UserRole.USER)
        manel_id = _user_id(session, UserRole.ADMIN)
        for owner, label in ((bart_id, "bart-own"), (manel_id, "manel-own")):
            session.add(
                EmailThread(
                    initiated_by_user_id=owner,
                    gmail_thread_id=f"thr-{label}",
                    gmail_account_user_id=owner,
                    first_message_at=datetime.now(UTC),
                    last_message_at=datetime.now(UTC),
                    message_count=1,
                )
            )
        session.commit()

    response = client.get(
        "/api/emails/threads",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["gmail_thread_id"] == "thr-bart-own"


def test_send_email_emits_activity_event_when_contact_id_set(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.crm import ActivityEvent, Contact  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        contact = Contact(first_name="Cliente", email="client@example.com")
        session.add(contact)
        session.commit()
        contact_id = contact.id
    _seed_gmail_integration(session_factory, user_id=uid)

    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def send_message(self, **_kwargs: Any) -> dict[str, Any]:
            return {"id": "msg-evt", "threadId": "thr-evt"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["client@example.com"],
            "subject": "Hola",
            "body_text": "Body",
            "contact_id": contact_id,
        },
        headers=auth_headers(client, "user"),
    )
    with session_factory() as session:
        events = list(
            session.scalars(
                select(ActivityEvent).where(
                    ActivityEvent.event_type == "email.sent_from_crm"
                )
            )
        )
    assert len(events) == 1
    assert events[0].contact_id == contact_id


# Suppress the unused-import lint on `patch` — kept available for
# future tests that swap the entire client.
_ = patch


# ---------------------------------------------------------------------------
# Sprint Email v1 follow-up — per-user alias preferences
# ---------------------------------------------------------------------------


def test_put_preferences_upserts_rows(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

    response = client.put(
        "/api/emails/aliases/preferences",
        json={
            "preferences": [
                {
                    "alias_email": "info@bomedia.net",
                    "is_allowed": True,
                    "is_default": False,
                },
                {
                    "alias_email": "ventas@bomedia.net",
                    "is_allowed": True,
                    "is_default": True,
                },
            ]
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    with session_factory() as session:
        prefs = list(session.scalars(select(UserEmailAliasPref)))
        assert len(prefs) == 2
        defaults = [p for p in prefs if p.is_default]
        assert len(defaults) == 1
        assert defaults[0].alias_email == "ventas@bomedia.net"


def test_put_preferences_rejects_two_defaults(client: TestClient) -> None:
    response = client.put(
        "/api/emails/aliases/preferences",
        json={
            "preferences": [
                {
                    "alias_email": "info@bomedia.net",
                    "is_allowed": True,
                    "is_default": True,
                },
                {
                    "alias_email": "ventas@bomedia.net",
                    "is_allowed": True,
                    "is_default": True,
                },
            ]
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any("Solo un alias" in str(item) for item in detail)


def test_put_preferences_normalises_zero_default_to_first_allowed(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    """PR-Aliases-UX. Si el operador manda allowed sin default
    explícito, el handler elige el primer marcado como default.
    Garantía: el composer siempre tiene un default determinista."""
    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

    response = client.put(
        "/api/emails/aliases/preferences",
        json={
            "preferences": [
                {
                    "alias_email": "info@bomedia.net",
                    "is_allowed": True,
                    "is_default": False,
                },
                {
                    "alias_email": "ventas@bomedia.net",
                    "is_allowed": True,
                    "is_default": False,
                },
            ]
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(UserEmailAliasPref).order_by(
                    UserEmailAliasPref.alias_email
                )
            )
        )
        defaults = [r.alias_email for r in rows if r.is_default]
        assert defaults == ["info@bomedia.net"]


def test_put_preferences_disallow_default_reassigns(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    """PR-Aliases-UX. Si el operador desmarca el default actual y
    deja otros allowed, el handler reasigna el default al primer
    superviviente. Sin esto el user quedaría sin default y el
    composer no sabría cuál pre-seleccionar."""
    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

    # Seed con default = info.
    client.put(
        "/api/emails/aliases/preferences",
        json={
            "preferences": [
                {
                    "alias_email": "info@bomedia.net",
                    "is_allowed": True,
                    "is_default": True,
                },
                {
                    "alias_email": "ventas@bomedia.net",
                    "is_allowed": True,
                    "is_default": False,
                },
            ]
        },
        headers=auth_headers(client, "user"),
    )
    # Desmarca info (el default actual). ventas pasa a ser default.
    response = client.put(
        "/api/emails/aliases/preferences",
        json={
            "preferences": [
                {
                    "alias_email": "info@bomedia.net",
                    "is_allowed": False,
                    "is_default": False,
                },
                {
                    "alias_email": "ventas@bomedia.net",
                    "is_allowed": True,
                    "is_default": False,
                },
            ]
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    with session_factory() as session:
        rows = list(session.scalars(select(UserEmailAliasPref)))
        assert len(rows) == 1
        assert rows[0].alias_email == "ventas@bomedia.net"
        assert rows[0].is_default is True


def test_put_preferences_disallow_removes_row(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

    # Seed.
    client.put(
        "/api/emails/aliases/preferences",
        json={
            "preferences": [
                {
                    "alias_email": "info@bomedia.net",
                    "is_allowed": True,
                    "is_default": False,
                },
            ]
        },
        headers=auth_headers(client, "user"),
    )
    # Disallow.
    client.put(
        "/api/emails/aliases/preferences",
        json={
            "preferences": [
                {
                    "alias_email": "info@bomedia.net",
                    "is_allowed": False,
                    "is_default": False,
                },
            ]
        },
        headers=auth_headers(client, "user"),
    )
    with session_factory() as session:
        prefs = list(session.scalars(select(UserEmailAliasPref)))
        assert prefs == []


def test_my_aliases_intersects_gmail_and_prefs(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`my-aliases` only returns prefs whose alias still exists in
    Gmail. Stale prefs (alias removed from Gmail) drop out."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(
        session_factory,
        user_id=uid,
        allowed_aliases=("info@bomedia.net", "ghost@bomedia.net"),
    )

    monkeypatch.setattr(
        "app.api.emails.gmail_service.list_aliases",
        lambda _s, _u: [
            {
                "send_as_email": "info@bomedia.net",
                "display_name": "Bomedia",
                "is_primary": False,
                "is_default": False,
                "verification_status": "accepted",
            },
        ],
    )
    response = client.get(
        "/api/emails/my-aliases", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    body = response.json()
    assert [a["send_as_email"] for a in body] == ["info@bomedia.net"]
    assert body[0]["is_default"] is True


def test_send_with_unmarked_alias_returns_403(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The send endpoint rejects an alias that isn't in the user's
    preferences, even when Gmail itself would accept it."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(
        session_factory,
        user_id=uid,
        allowed_aliases=("info@bomedia.net",),  # only info@ allowed
    )

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def send_message(self, **_kw):  # pragma: no cover - never called
            raise AssertionError("send_message must not run for unmarked alias")

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "ventas@bomedia.net",  # NOT in prefs
            "to": ["client@example.com"],
            "subject": "x",
            "body_text": "x",
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 403
    assert "preferencias" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Multi-user Gmail fan-out + tolerant history processing
# ---------------------------------------------------------------------------


def test_webhook_enqueues_one_job_per_matching_integration(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two CRM users sharing the same `google_email` must both get
    a process_history job — otherwise replies for the user not
    chosen by `session.scalar()` would be silently dropped."""
    now = datetime.now(UTC)
    with session_factory() as session:
        admin_id = _user_id(session, UserRole.ADMIN)
        user_id = _user_id(session, UserRole.USER)
        for uid in (admin_id, user_id):
            session.add(
                UserGoogleIntegration(
                    user_id=uid,
                    google_email="shared@bomedia.net",
                    access_token_encrypted=encrypt("a"),
                    refresh_token_encrypted=encrypt("r"),
                    token_expires_at=now + timedelta(hours=1),
                    scopes=(
                        "https://www.googleapis.com/auth/gmail.send "
                        "https://www.googleapis.com/auth/gmail.modify"
                    ),
                    connected_at=now,
                )
            )
        session.commit()

    enqueued: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.integrations.gmail.jobs.enqueue_process_history",
        lambda *, user_id, new_history_id: enqueued.append(
            (user_id, new_history_id)
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.webhook._validate_jwt",
        lambda _auth: None,
    )

    payload = {
        "message": {
            "data": base64.b64encode(
                json.dumps(
                    {
                        "emailAddress": "shared@bomedia.net",
                        "historyId": 777,
                    }
                ).encode()
            ).decode(),
        }
    }
    response = client.post("/api/webhooks/gmail", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "enqueued", "users": 2}
    assert sorted(uid for uid, _ in enqueued) == sorted([admin_id, user_id])
    assert all(hid == 777 for _, hid in enqueued)


def test_webhook_returns_ignored_when_no_integration_matches(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueued: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.integrations.gmail.jobs.enqueue_process_history",
        lambda *, user_id, new_history_id: enqueued.append(
            (user_id, new_history_id)
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.webhook._validate_jwt",
        lambda _auth: None,
    )
    payload = {
        "message": {
            "data": base64.b64encode(
                json.dumps(
                    {
                        "emailAddress": "stranger@example.com",
                        "historyId": 1,
                    }
                ).encode()
            ).decode(),
        }
    }
    response = client.post("/api/webhooks/gmail", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert enqueued == []


def _make_http_error(status: int) -> Exception:
    """Build a googleapiclient HttpError without speaking HTTP. The
    test mocks raise it from `get_message` to trigger the 404 path."""
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    class _Resp:
        def __init__(self, code: int) -> None:
            self.status = code
            self.reason = "Not Found"

    err = HttpError.__new__(HttpError)
    err.resp = _Resp(status)
    err.content = b""
    err.uri = "https://example/gmail"
    err.error_details = ""
    err.status_code = status
    return err


def test_process_history_skips_messages_returning_404(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ghost messages (drafts deleted / spam moved / trashed) used
    to abort the whole batch and trap the watch. Now they're logged
    + skipped and the watch still advances."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    # Seed a thread the user owns + the watch row.
    from app.models.crm import EmailThread, GmailPubsubWatch  # noqa: PLC0415

    with session_factory() as session:
        thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_thread_id="thr-A",
            gmail_account_user_id=uid,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
        )
        session.add(thread)
        session.add(
            GmailPubsubWatch(
                user_id=uid,
                history_id=1,
                watch_expires_at=datetime.now(UTC) + timedelta(days=6),
                last_renewed_at=datetime.now(UTC),
                topic_name="projects/x/topics/y",
            )
        )
        session.commit()

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def list_history(self, _start):
            return {
                "history": [
                    {
                        "messagesAdded": [
                            {"message": {"id": "ghost", "threadId": "thr-A"}},
                            {"message": {"id": "real", "threadId": "thr-A"}},
                        ]
                    }
                ]
            }

        def get_message(self, mid):
            if mid == "ghost":
                raise _make_http_error(404)
            return {
                "id": "real",
                "snippet": "ok",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "client@example.com"},
                        {"name": "To", "value": "info@bomedia.net"},
                        {"name": "Subject", "value": "Re: Hola"},
                        {
                            "name": "Date",
                            "value": "Fri, 31 Dec 2099 23:59:00 +0000",
                        },
                    ],
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(
                            b"hi"
                        ).decode()
                    },
                },
            }

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )

    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    with session_factory() as session:
        imported = gmail_service.process_history(
            session, user_id=uid, new_history_id=999
        )
        session.commit()
    # Ghost skipped, real persisted.
    assert imported == 1

    with session_factory() as session:
        # Watch advanced even though one message in the batch
        # raised 404 — critical invariant.
        watch = session.scalar(
            select(GmailPubsubWatch).where(GmailPubsubWatch.user_id == uid)
        )
        assert watch is not None
        assert watch.history_id == 999


def test_process_history_advances_watch_even_when_every_message_fails(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All-404 batch — watch.history_id must still advance so the
    next push isn't trapped on the same range."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    from app.models.crm import EmailThread, GmailPubsubWatch  # noqa: PLC0415

    with session_factory() as session:
        session.add(
            EmailThread(
                initiated_by_user_id=uid,
                gmail_thread_id="thr-A",
                gmail_account_user_id=uid,
                first_message_at=datetime.now(UTC),
                last_message_at=datetime.now(UTC),
                message_count=1,
            )
        )
        session.add(
            GmailPubsubWatch(
                user_id=uid,
                history_id=1,
                watch_expires_at=datetime.now(UTC) + timedelta(days=6),
                last_renewed_at=datetime.now(UTC),
                topic_name="projects/x/topics/y",
            )
        )
        session.commit()

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def list_history(self, _start):
            return {
                "history": [
                    {
                        "messagesAdded": [
                            {"message": {"id": "g1", "threadId": "thr-A"}},
                            {"message": {"id": "g2", "threadId": "thr-A"}},
                        ]
                    }
                ]
            }

        def get_message(self, _mid):
            raise _make_http_error(404)

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )

    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    with session_factory() as session:
        imported = gmail_service.process_history(
            session, user_id=uid, new_history_id=42_000
        )
        session.commit()
    assert imported == 0

    with session_factory() as session:
        watch = session.scalar(
            select(GmailPubsubWatch).where(GmailPubsubWatch.user_id == uid)
        )
        assert watch is not None
        assert watch.history_id == 42_000


# ---------------------------------------------------------------------------
# Email v2.1 — list search, thread detail, activity feed
# ---------------------------------------------------------------------------


def test_list_threads_returns_enriched_last_message_fields(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v2.1 list view needs last_message_from + snippet + direction
    on each thread row so the table renders without an N+1."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def send_message(self, **_kwargs):
            return {"id": "msg-list-1", "threadId": "thr-list-1"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["client@example.com"],
            "subject": "Hola lista",
            "body_text": "Cuerpo para snippet de lista",
        },
        headers=auth_headers(client, "user"),
    )
    response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["last_message_direction"] == "outbound"
    assert item["last_message_from"] == "info@bomedia.net"
    assert "Cuerpo para snippet" in (item["last_message_snippet"] or "")


def test_list_threads_filters_by_search_term(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`?q=` ilike-matches subject + sender + snippet across the
    thread's messages."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    fake_ids = iter(["m1", "m2"])
    fake_threads = iter(["t1", "t2"])

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def send_message(self, **_kwargs):
            return {
                "id": next(fake_ids),
                "threadId": next(fake_threads),
            }

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["c1@example.com"],
            "subject": "Probando filtro foo",
            "body_text": "body",
        },
        headers=auth_headers(client, "user"),
    )
    client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["c2@example.com"],
            "subject": "Hola mundo",
            "body_text": "body 2",
        },
        headers=auth_headers(client, "user"),
    )
    response = client.get(
        "/api/emails/threads?q=foo", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["subject"] == "Probando filtro foo"


def test_activity_endpoint_returns_recent_items(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def send_message(self, **_kwargs):
            return {"id": "msg-act-1", "threadId": "thr-act-1"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["client@example.com"],
            "subject": "Para activity",
            "body_text": "body",
        },
        headers=auth_headers(client, "user"),
    )
    response = client.get(
        "/api/emails/activity?scope=mine&limit=5",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    items = response.json()
    assert len(items) == 1
    item = items[0]
    assert item["type"] == "email.sent_from_crm"
    assert item["direction"] == "outbound"
    assert item["subject"] == "Para activity"


def test_activity_scope_all_only_for_admin(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """v2.1.1 fix — only the `admin` role gets the unfiltered
    `scope=all` view. Manager + user + viewer are forced into the
    `mine` filter regardless of the scope they sent."""
    from app.models.crm import EmailDirection  # noqa: PLC0415

    with session_factory() as session:
        admin_id = _user_id(session, UserRole.ADMIN)
        thread = EmailThread(
            initiated_by_user_id=admin_id,
            gmail_thread_id="thr-admin",
            gmail_account_user_id=admin_id,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
        )
        session.add(thread)
        session.flush()
        session.add(
            EmailMessage(
                thread_id=thread.id,
                gmail_message_id="msg-admin",
                gmail_account_user_id=admin_id,
                direction=EmailDirection.OUTBOUND,
                from_email="admin@example.com",
                to_emails_json='["x@example.com"]',
                sent_at=datetime.now(UTC),
            )
        )
        session.commit()
    # Both user and manager get filtered to "mine".
    user_response = client.get(
        "/api/emails/activity?scope=all&limit=5",
        headers=auth_headers(client, "user"),
    )
    assert user_response.json() == []
    manager_response = client.get(
        "/api/emails/activity?scope=all&limit=5",
        headers=auth_headers(client, "manager"),
    )
    assert manager_response.json() == []
    # Admin sees the seeded thread.
    admin_response = client.get(
        "/api/emails/activity?scope=all&limit=5",
        headers=auth_headers(client, "admin"),
    )
    assert len(admin_response.json()) == 1


def test_inbound_reply_emits_activity_event_on_contact(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The contact timeline should show an `email.reply_received`
    event whenever the webhook imports an inbound reply tied to a
    known contact."""
    from app.models.crm import ActivityEvent, Contact, GmailPubsubWatch  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        contact = Contact(first_name="Cliente", email="client@example.com")
        session.add(contact)
        session.commit()
        cid = contact.id
    _seed_gmail_integration(session_factory, user_id=uid)

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def send_message(self, **_kwargs):
            return {"id": "out-act", "threadId": "thr-act"}

        def list_history(self, _start):
            return {
                "history": [
                    {
                        "messagesAdded": [
                            {"message": {"id": "in-act", "threadId": "thr-act"}}
                        ]
                    }
                ]
            }

        def get_message(self, _mid):
            return {
                "id": "in-act",
                "snippet": "Reply preview",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "client@example.com"},
                        {"name": "To", "value": "info@bomedia.net"},
                        {"name": "Subject", "value": "Re: Hola"},
                        {
                            "name": "Date",
                            "value": "Fri, 31 Dec 2099 23:59:00 +0000",
                        },
                    ],
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(b"Texto").decode()
                    },
                },
            }

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["client@example.com"],
            "subject": "Hola",
            "body_text": "Body",
            "contact_id": cid,
        },
        headers=auth_headers(client, "user"),
    )
    with session_factory() as session:
        session.add(
            GmailPubsubWatch(
                user_id=uid,
                history_id=1,
                watch_expires_at=datetime.now(UTC) + timedelta(days=6),
                last_renewed_at=datetime.now(UTC),
                topic_name="projects/x/topics/y",
            )
        )
        session.commit()
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    with session_factory() as session:
        gmail_service.process_history(
            session, user_id=uid, new_history_id=999
        )
        session.commit()
    with session_factory() as session:
        events = list(
            session.scalars(
                select(ActivityEvent).where(
                    ActivityEvent.event_type == "email.reply_received"
                )
            )
        )
    assert len(events) == 1
    assert events[0].contact_id == cid


# ---------------------------------------------------------------------------
# v2.1.1 — contact_name resolution + auto mark-read
# ---------------------------------------------------------------------------


def test_thread_list_resolves_contact_name_from_contact_row(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.crm import Contact  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        contact = Contact(
            first_name="Eduard", last_name="Riera", email="eduard@example.com"
        )
        session.add(contact)
        session.commit()
        cid = contact.id
    _seed_gmail_integration(session_factory, user_id=uid)

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def send_message(self, **_kwargs):
            return {"id": "msg-eduard", "threadId": "thr-eduard"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["eduard@example.com"],
            "subject": "Test",
            "body_text": "body",
            "contact_id": cid,
        },
        headers=auth_headers(client, "user"),
    )
    response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["contact_name"] == "Eduard Riera"


def test_thread_list_falls_back_to_email_local_when_no_contact(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When there's no linked Contact and no `from_name` header,
    capitalise the local part of the from_email."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def send_message(self, **_kwargs):
            return {"id": "msg-fb", "threadId": "thr-fb"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["x@example.com"],
            "subject": "Test",
            "body_text": "body",
        },
        headers=auth_headers(client, "user"),
    )
    response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    body = response.json()
    # No Contact linked, message has no from_name, so the resolver
    # falls back to capitalising the email's local part: "info".
    assert body["items"][0]["contact_name"] == "Info"


def test_detail_endpoint_auto_marks_read_for_initiator(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    """Opening a thread you initiated flips `has_unread_replies`
    off as a side effect of the GET — the front-end doesn't need
    to chain a mark-read POST."""
    from app.models.crm import EmailDirection  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_thread_id="thr-mark",
            gmail_account_user_id=uid,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
            has_unread_replies=True,
        )
        session.add(thread)
        session.flush()
        session.add(
            EmailMessage(
                thread_id=thread.id,
                gmail_message_id="msg-mark",
                gmail_account_user_id=uid,
                direction=EmailDirection.INBOUND,
                from_email="x@example.com",
                to_emails_json='["info@bomedia.net"]',
                sent_at=datetime.now(UTC),
            )
        )
        session.commit()
        thread_id = thread.id
    response = client.get(
        f"/api/emails/threads/{thread_id}",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    with session_factory() as session:
        thread = session.get(EmailThread, thread_id)
        assert thread.has_unread_replies is False


def test_mark_unread_flips_flag_back_on(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_thread_id="thr-flip",
            gmail_account_user_id=uid,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
            has_unread_replies=False,
        )
        session.add(thread)
        session.commit()
        thread_id = thread.id
    response = client.post(
        f"/api/emails/threads/{thread_id}/mark-unread",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    with session_factory() as session:
        thread = session.get(EmailThread, thread_id)
        assert thread.has_unread_replies is True


def test_reply_to_suggestion_skips_comercial_alias(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Email v2.2 r4: a comercial replying to a lead straight from
    Gmail comes back through the account watch as `inbound` with
    `from_email` set to one of their own aliases. The reply target
    must still be the lead, not the comercial."""
    from app.models.crm import EmailDirection, UserEmailAliasPref

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)  # email user@example.com
        # The operator also sends as info@bomedia.net.
        session.add(
            UserEmailAliasPref(
                user_id=uid,
                alias_email="info@bomedia.net",
                is_allowed=True,
                is_default=True,
            )
        )
        thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_thread_id="thr-reply",
            gmail_account_user_id=uid,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=3,
        )
        session.add(thread)
        session.flush()
        base = datetime.now(UTC)
        session.add_all(
            [
                # 1) comercial -> lead (genuine outbound)
                EmailMessage(
                    thread_id=thread.id,
                    gmail_message_id="m1",
                    gmail_account_user_id=uid,
                    direction=EmailDirection.OUTBOUND,
                    from_email="info@bomedia.net",
                    to_emails_json='["lead@example.com"]',
                    sent_at=base,
                ),
                # 2) lead -> comercial (genuine inbound)
                EmailMessage(
                    thread_id=thread.id,
                    gmail_message_id="m2",
                    gmail_account_user_id=uid,
                    direction=EmailDirection.INBOUND,
                    from_email="lead@example.com",
                    to_emails_json='["info@bomedia.net"]',
                    sent_at=base + timedelta(minutes=5),
                ),
                # 3) comercial replies FROM GMAIL — mislabelled inbound,
                #    from_email is the operator's own alias.
                EmailMessage(
                    thread_id=thread.id,
                    gmail_message_id="m3",
                    gmail_account_user_id=uid,
                    direction=EmailDirection.INBOUND,
                    from_email="info@bomedia.net",
                    to_emails_json='["lead@example.com"]',
                    sent_at=base + timedelta(minutes=10),
                ),
            ]
        )
        session.commit()
        thread_id = thread.id

    response = client.get(
        f"/api/emails/threads/{thread_id}",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    # Despite m3 being the most recent AND labelled inbound, the
    # suggestion is the lead — m3's sender is the operator's alias.
    assert response.json()["reply_to_suggestion"] == "lead@example.com"


def test_reply_to_suggestion_falls_back_to_first_recipient(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Thread with only the operator's own outbound (lead never
    replied) → fall back to whoever the first message was sent to."""
    from app.models.crm import EmailDirection

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_thread_id="thr-out",
            gmail_account_user_id=uid,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
        )
        session.add(thread)
        session.flush()
        session.add(
            EmailMessage(
                thread_id=thread.id,
                gmail_message_id="only",
                gmail_account_user_id=uid,
                direction=EmailDirection.OUTBOUND,
                from_email="user@example.com",
                to_emails_json='["nuevo-lead@example.com"]',
                sent_at=datetime.now(UTC),
            )
        )
        session.commit()
        thread_id = thread.id

    response = client.get(
        f"/api/emails/threads/{thread_id}",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["reply_to_suggestion"] == "nuevo-lead@example.com"


def test_send_reply_uses_parent_rfc_message_id_for_threading(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent's gmail API id isn't an RFC-compliant Message-Id.
    Gmail rejects a malformed `In-Reply-To` and breaks threading even
    when threadId is passed. send_email must fetch the parent's real
    Message-Id header before building the reply MIME."""
    from app.models.crm import EmailDirection

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_thread_id="gmail-thr-original",
            gmail_account_user_id=uid,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
            subject="Hola",
        )
        session.add(thread)
        session.flush()
        parent = EmailMessage(
            thread_id=thread.id,
            gmail_message_id="gmail-msg-parent",
            gmail_account_user_id=uid,
            direction=EmailDirection.INBOUND,
            from_email="lead@example.com",
            to_emails_json='["info@bomedia.net"]',
            sent_at=datetime.now(UTC),
        )
        session.add(parent)
        session.commit()
        parent_id = parent.id
    _seed_gmail_integration(session_factory, user_id=uid)

    sent: list[dict[str, Any]] = []

    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def get_message(self, message_id: str) -> dict[str, Any]:
            assert message_id == "gmail-msg-parent"
            return {
                "id": message_id,
                "payload": {
                    "headers": [
                        {
                            "name": "Message-Id",
                            "value": "<CABcDeFgHiJk@mail.gmail.com>",
                        },
                        {"name": "Subject", "value": "Hola"},
                    ],
                },
            }

        def send_message(self, **kwargs: Any) -> dict[str, Any]:
            sent.append(kwargs)
            return {
                "id": "gmail-msg-reply",
                "threadId": kwargs.get("thread_id") or "gmail-thr-original",
            }

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )

    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["lead@example.com"],
            "subject": "Re: Hola",
            "body_html": "<p>Hola de vuelta</p>",
            "body_text": None,
            "in_reply_to_message_id": parent_id,
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    assert sent
    call = sent[0]
    # threadId still piped through so Gmail groups by conversation.
    assert call["thread_id"] == "gmail-thr-original"
    # The crucial bit: header carries the parent's RFC Message-Id
    # (angle brackets and all), not the API id.
    assert call["in_reply_to_message_id"] == "<CABcDeFgHiJk@mail.gmail.com>"
    assert call["references"] == ["<CABcDeFgHiJk@mail.gmail.com>"]


def test_send_reply_falls_back_when_parent_message_lookup_fails(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Gmail 404s the parent (deleted / expired), the reply still
    flies — just without the In-Reply-To header. Partial chain >
    outright failure."""
    from app.models.crm import EmailDirection

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_thread_id="gmail-thr-x",
            gmail_account_user_id=uid,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
            subject="X",
        )
        session.add(thread)
        session.flush()
        parent = EmailMessage(
            thread_id=thread.id,
            gmail_message_id="gone",
            gmail_account_user_id=uid,
            direction=EmailDirection.OUTBOUND,
            from_email="user@example.com",
            to_emails_json='["lead@example.com"]',
            sent_at=datetime.now(UTC),
        )
        session.add(parent)
        session.commit()
        parent_id = parent.id
    _seed_gmail_integration(session_factory, user_id=uid)

    sent: list[dict[str, Any]] = []

    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def get_message(self, _message_id: str) -> dict[str, Any]:
            raise RuntimeError("parent gone")

        def send_message(self, **kwargs: Any) -> dict[str, Any]:
            sent.append(kwargs)
            return {"id": "gmail-msg-y", "threadId": "gmail-thr-x"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )

    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["lead@example.com"],
            "subject": "Re: X",
            "body_html": "<p>retry</p>",
            "body_text": None,
            "in_reply_to_message_id": parent_id,
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    call = sent[0]
    assert call["thread_id"] == "gmail-thr-x"
    assert call["in_reply_to_message_id"] is None
    assert call["references"] is None


def test_snippet_from_body_strips_style_block_contents() -> None:
    """The inbox list preview fell back to _snippet_from_body, which
    only stripped tags — leaving the TinyMCE `<style>` reset block as
    raw CSS in the preview. It now routes HTML through the shared
    extractor."""
    from app.api.emails import _snippet_from_body

    html = (
        "<p></p>"
        "<style>body,table,td,p,a{margin:0;padding:0}img{border:0}</style>"
        "<p>Hola Eduard, confirmo nuestra cita para mañana.</p>"
    )
    assert (
        _snippet_from_body(None, html)
        == "Hola Eduard, confirmo nuestra cita para mañana."
    )
    # Plain-text body short-circuits untouched.
    assert _snippet_from_body("Hola directo", None) == "Hola directo"
    assert _snippet_from_body(None, None) is None


def test_backfill_helpers_detect_dirty_snippets() -> None:
    from scripts.backfill_email_snippets import _looks_dirty

    assert _looks_dirty("<p></p> <style>body{margin:0}") is True
    assert _looks_dirty("table{border-collapse:collapse}") is True
    assert _looks_dirty("Hola Eduard, confirmo la cita") is False
    assert _looks_dirty(None) is False
    assert _looks_dirty("") is False


def test_backfill_rewrites_dirty_message_and_event(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a stored message + activity event with CSS-dirty
    snippets get repaired in place."""
    import json as _json

    from app.models.crm import ActivityEvent, EmailDirection
    from scripts import backfill_email_snippets

    dirty_html = (
        "<p></p><style>body,td{margin:0}</style>"
        "<p>Hola, te confirmo la reunión.</p>"
    )
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_thread_id="bf-thr",
            gmail_account_user_id=uid,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
            subject="x",
        )
        session.add(thread)
        session.flush()
        msg = EmailMessage(
            thread_id=thread.id,
            gmail_message_id="bf-msg",
            gmail_account_user_id=uid,
            direction=EmailDirection.OUTBOUND,
            from_email="info@bomedia.net",
            to_emails_json='["lead@example.com"]',
            subject="x",
            body_html=dirty_html,
            body_text=None,
            snippet="<style>body,td{margin:0}",
            sent_at=datetime.now(UTC),
            created_by_user_id=uid,
        )
        session.add(msg)
        session.flush()
        session.add(
            ActivityEvent(
                contact_id="c-bf",
                system="crm",
                account_id="emails",
                external_id=f"email:{msg.id}:email.sent_from_crm",
                event_type="email.sent_from_crm",
                subject="x",
                body="<style>body,td{margin:0}",
                metadata_json=_json.dumps(
                    {"message_id": msg.id, "snippet": "<style>body{margin:0}"}
                ),
                occurred_at=datetime.now(UTC),
                synced_at=datetime.now(UTC),
            )
        )
        session.commit()
        msg_id = msg.id

    engine = session_factory.kw["bind"]
    monkeypatch.setattr(
        backfill_email_snippets, "get_engine", lambda: engine
    )
    counts = backfill_email_snippets.backfill(dry_run=False)
    assert counts["messages_fixed"] == 1
    assert counts["events_fixed"] == 1

    with session_factory() as session:
        fixed = session.get(EmailMessage, msg_id)
        assert fixed is not None
        assert fixed.snippet == "Hola, te confirmo la reunión."
        ev = session.scalar(
            select(ActivityEvent).where(
                ActivityEvent.event_type == "email.sent_from_crm"
            )
        )
        assert ev is not None
        assert ev.body == "Hola, te confirmo la reunión."
        assert (
            _json.loads(ev.metadata_json)["snippet"]
            == "Hola, te confirmo la reunión."
        )


def test_thread_list_includes_tracking_counts(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """The inbox list surfaces per-thread open/click/etc counts so the
    rows can show badges. `sent` is excluded from the aggregate."""
    from app.models.crm import (
        EmailDirection,
        EmailEventType,
        EmailMessageEvent,
    )

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_thread_id="trk-thr",
            gmail_account_user_id=uid,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
            subject="x",
        )
        session.add(thread)
        session.flush()
        msg = EmailMessage(
            thread_id=thread.id,
            gmail_message_id="trk-msg",
            gmail_account_user_id=uid,
            direction=EmailDirection.OUTBOUND,
            from_email="info@bomedia.net",
            to_emails_json='["lead@example.com"]',
            subject="x",
            sent_at=datetime.now(UTC),
            created_by_user_id=uid,
        )
        session.add(msg)
        session.flush()
        now = datetime.now(UTC)
        session.add_all(
            [
                EmailMessageEvent(
                    message_id=msg.id,
                    event_type=EmailEventType.SENT,
                    occurred_at=now,
                ),
                EmailMessageEvent(
                    message_id=msg.id,
                    event_type=EmailEventType.OPEN,
                    occurred_at=now,
                ),
                EmailMessageEvent(
                    message_id=msg.id,
                    event_type=EmailEventType.OPEN,
                    occurred_at=now,
                ),
                EmailMessageEvent(
                    message_id=msg.id,
                    event_type=EmailEventType.CLICK,
                    occurred_at=now,
                ),
            ]
        )
        session.commit()

    response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    item = next(
        t for t in response.json()["items"] if t["gmail_thread_id"] == "trk-thr"
    )
    # 2 opens + 1 click; sent is NOT counted in the inbox aggregate.
    assert item["tracking"] == {"open": 2, "click": 1}
