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
    google_email: str = "bart@bomedia.net",
    scopes: str = (
        "https://www.googleapis.com/auth/calendar.events "
        "https://www.googleapis.com/auth/gmail.send "
        "https://www.googleapis.com/auth/gmail.modify"
    ),
) -> None:
    """PR-OAuth-Google-Unificado. Seed the ORG Google integration
    singleton (tokens compartidos por todo el equipo) + one alias
    preference per `allowed_aliases` for `user_id`. The first alias
    becomes the default. Tests that want the "alias not in prefs"
    path should pass `allowed_aliases=()`.

    `connected_by_user_id` apunta a `user_id` para que el webhook /
    process_history queden atribuidos a ese user. Idempotente en el PK
    singleton."""
    from app.models.crm import (  # noqa: PLC0415
        ORG_GOOGLE_SINGLETON_ID,
        OrgGoogleIntegration,
        UserEmailAliasPref,
    )

    with session_factory() as session:
        integ = session.get(OrgGoogleIntegration, ORG_GOOGLE_SINGLETON_ID)
        if integ is None:
            integ = OrgGoogleIntegration(id=ORG_GOOGLE_SINGLETON_ID)
            session.add(integ)
        integ.google_email = google_email
        integ.access_token_encrypted = encrypt("access")
        integ.refresh_token_encrypted = encrypt("refresh")
        integ.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        integ.scopes = scopes
        integ.connected_at = datetime.now(UTC)
        integ.connected_by_user_id = user_id
        integ.status = "active"
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
    # PR-OAuth-Google-Unificado. La cuenta org está conectada pero solo
    # con scope de calendar (sin gmail.send) → 403 scope-missing.
    _seed_gmail_integration(
        session_factory,
        user_id=uid,
        scopes="https://www.googleapis.com/auth/calendar.events",
    )
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
        from app.models.crm import (  # noqa: PLC0415
            GmailPubsubWatch,
            UserEmailAlias,
        )

        session.add(
            GmailPubsubWatch(
                user_id=thread.gmail_account_user_id,
                history_id=1,
                watch_expires_at=datetime.now(UTC) + timedelta(days=6),
                last_renewed_at=datetime.now(UTC),
                topic_name="projects/x/topics/y",
            )
        )
        # CRM-GMAIL — captura universal: el mail entra por ir dirigido a un
        # alias activo (info@bomedia.net), no por pertenecer a un thread
        # conocido.
        session.add(
            UserEmailAlias(
                user_id=thread.gmail_account_user_id,
                alias_email="info@bomedia.net",
                active=True,
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
    """CRM-GMAIL Parte E — Bart eligió FILTRAR también la pestaña Emails de
    la ficha por alias (revierte el «historial colaborativo» previo).

    Un comercial (user) ve en la ficha solo los threads que inició o que
    llegaron a sus alias; NO los que otro comercial cruzó con el contacto.
    Admin ve todo el historial del contacto."""
    from app.models.crm import Contact, EmailMessage, UserEmailAlias  # noqa: PLC0415

    with session_factory() as session:
        manel_id = _user_id(session, UserRole.ADMIN)  # otro comercial (admin)
        bart_id = _user_id(session, UserRole.USER)  # abre la ficha
        session.add(
            UserEmailAlias(
                user_id=bart_id, alias_email="bart@bomedia.net", active=True
            )
        )
        contact = Contact(
            first_name="Salome",
            email="sara_kali@hotmail.es",
            commercial_status="new",
            is_active=True,
        )
        session.add(contact)
        session.flush()
        contact_id = contact.id
        # (1) Thread iniciado por Manel al contacto: Bart NO debe verlo.
        manel_thread = EmailThread(
            contact_id=contact_id,
            initiated_by_user_id=manel_id,
            gmail_thread_id="thr-manel-to-salome",
            gmail_account_user_id=manel_id,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
        )
        # (2) Thread con un mensaje entregado al alias de Bart: SÍ lo ve.
        bart_thread = EmailThread(
            contact_id=contact_id,
            initiated_by_user_id=manel_id,
            gmail_thread_id="thr-inbound-to-bart",
            gmail_account_user_id=manel_id,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
        )
        session.add_all([manel_thread, bart_thread])
        session.flush()
        session.add(
            EmailMessage(
                thread_id=bart_thread.id,
                gmail_message_id="m-in-bart",
                gmail_account_user_id=manel_id,
                direction="inbound",
                from_email="sara_kali@hotmail.es",
                to_emails_json='["bart@bomedia.net"]',
                delivered_to="bart@bomedia.net",
                sent_at=datetime.now(UTC),
                contact_id=contact_id,
            )
        )
        session.commit()

    # Bart (user) abre la ficha → solo el thread entregado a su alias.
    resp_user = client.get(
        f"/api/emails/threads?contact_id={contact_id}",
        headers=auth_headers(client, "user"),
    )
    assert resp_user.status_code == 200
    body_user = resp_user.json()
    assert body_user["total"] == 1, body_user
    assert body_user["items"][0]["gmail_thread_id"] == "thr-inbound-to-bart"

    # Admin ve todo el historial del contacto (ambos threads).
    resp_admin = client.get(
        f"/api/emails/threads?contact_id={contact_id}",
        headers=auth_headers(client, "admin"),
    )
    assert resp_admin.json()["total"] == 2, resp_admin.json()


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


# PR-DisplayName-Remitente tests ----------------------------------------------


def test_list_aliases_syncs_gmail_display_name(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/emails/aliases sincroniza el `gmail_display_name`
    cacheado contra el `displayName` que Gmail devuelve hoy."""
    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    monkeypatch.setattr(
        "app.api.emails.gmail_service.list_aliases",
        lambda _s, _u: [
            {
                "send_as_email": "info@bomedia.net",
                "display_name": "Bomedia Sales",
                "is_primary": False,
                "is_default": False,
                "verification_status": "accepted",
            },
        ],
    )

    response = client.get(
        "/api/emails/aliases", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    body = response.json()
    assert body[0]["gmail_display_name"] == "Bomedia Sales"
    assert body[0]["resolved_display_name"] == "Bomedia Sales"

    # Cache persistido.
    with session_factory() as session:
        pref = session.scalar(
            select(UserEmailAliasPref).where(
                UserEmailAliasPref.alias_email == "info@bomedia.net"
            )
        )
        assert pref is not None
        assert pref.gmail_display_name == "Bomedia Sales"


def test_resolved_display_name_prefers_override(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """display_name_override gana sobre gmail_display_name."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    monkeypatch.setattr(
        "app.api.emails.gmail_service.list_aliases",
        lambda _s, _u: [
            {
                "send_as_email": "info@bomedia.net",
                "display_name": "Gmail Name",
                "is_primary": False,
                "is_default": False,
                "verification_status": "accepted",
            },
        ],
    )
    client.put(
        "/api/emails/aliases/preferences",
        json={
            "preferences": [
                {
                    "alias_email": "info@bomedia.net",
                    "is_allowed": True,
                    "is_default": True,
                    "display_name_override": "  Manual Override  ",
                },
            ]
        },
        headers=auth_headers(client, "user"),
    )
    response = client.get(
        "/api/emails/aliases", headers=auth_headers(client, "user")
    )
    body = response.json()
    assert body[0]["display_name_override"] == "Manual Override"
    assert body[0]["resolved_display_name"] == "Manual Override"


def test_clearing_override_falls_back_to_gmail_name(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty string en `display_name_override` → NULL en BD →
    `resolved_display_name` vuelve a usar el de Gmail."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    monkeypatch.setattr(
        "app.api.emails.gmail_service.list_aliases",
        lambda _s, _u: [
            {
                "send_as_email": "info@bomedia.net",
                "display_name": "Bomedia Default",
                "is_primary": False,
                "is_default": False,
                "verification_status": "accepted",
            },
        ],
    )
    # Set override.
    client.put(
        "/api/emails/aliases/preferences",
        json={
            "preferences": [
                {
                    "alias_email": "info@bomedia.net",
                    "is_allowed": True,
                    "is_default": True,
                    "display_name_override": "Custom",
                },
            ]
        },
        headers=auth_headers(client, "user"),
    )
    # Restaurar: empty string.
    client.put(
        "/api/emails/aliases/preferences",
        json={
            "preferences": [
                {
                    "alias_email": "info@bomedia.net",
                    "is_allowed": True,
                    "is_default": True,
                    "display_name_override": "",
                },
            ]
        },
        headers=auth_headers(client, "user"),
    )
    response = client.get(
        "/api/emails/aliases", headers=auth_headers(client, "user")
    )
    body = response.json()
    assert body[0]["display_name_override"] is None
    assert body[0]["resolved_display_name"] == "Bomedia Default"


def test_send_email_uses_resolved_display_name_from_pref(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cuando el composer NO manda from_name (caso común), el send
    path resuelve el display name desde la pref del alias y lo mete
    en el header `From:`."""
    from email.utils import parseaddr

    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)
    # Pref con override.
    with session_factory() as session:
        pref = session.scalar(
            select(UserEmailAliasPref).where(
                UserEmailAliasPref.alias_email == "info@bomedia.net"
            )
        )
        pref.display_name_override = "Bárbara Ñoño"  # non-ASCII para
        # validar RFC 2047
        session.commit()

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
            "body_text": "test",
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    # El servicio recibió from_name resuelto.
    assert sent[0]["from_name"] == "Bárbara Ñoño"

    # Y formataddr genera un header RFC 2047 que decodifica correcto.
    from email.header import decode_header, make_header
    from email.utils import formataddr

    header = formataddr((sent[0]["from_name"], sent[0]["from_alias"]))
    # Bárbara Ñoño tiene caracteres no-ASCII → formataddr emite la
    # cadena codificada =?utf-8?b?...?=. parseaddr la devuelve sin
    # decodificar; decode_header sí.
    name, email_part = parseaddr(header)
    decoded_name = str(make_header(decode_header(name)))
    assert decoded_name == "Bárbara Ñoño"
    assert email_part == "info@bomedia.net"


def test_send_email_explicit_from_name_wins(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el caller manda `from_name` explícito (API consumer / tests),
    NO sobreescribimos con el de la pref — respetamos el override
    explícito."""
    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)
    with session_factory() as session:
        pref = session.scalar(
            select(UserEmailAliasPref).where(
                UserEmailAliasPref.alias_email == "info@bomedia.net"
            )
        )
        pref.display_name_override = "Cached Name"
        session.commit()

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
            "from_name": "Explicit Caller Name",
            "to": ["client@example.com"],
            "subject": "x",
            "body_text": "x",
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    assert sent[0]["from_name"] == "Explicit Caller Name"


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


def test_webhook_enqueues_single_org_job(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-OAuth-Google-Unificado. La conexión Google es org-wide: por
    mucho que haya N users en el CRM, el webhook encola UN solo
    process_history, atribuido a `org.connected_by_user_id`. Antes el
    fan-out per-user duplicaba el mismo email N veces."""
    with session_factory() as session:
        admin_id = _user_id(session, UserRole.ADMIN)
    # La cuenta org la conectó el admin → connected_by_user_id=admin_id.
    _seed_gmail_integration(
        session_factory,
        user_id=admin_id,
        google_email="shared@bomedia.net",
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )

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
    assert response.json() == {"status": "enqueued", "users": 1}
    assert enqueued == [(admin_id, 777)]


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

    # Seed a thread the user owns + the watch row + an active alias.
    from app.models.crm import (  # noqa: PLC0415
        EmailThread,
        GmailPubsubWatch,
        UserEmailAlias,
    )

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
        session.add(
            UserEmailAlias(
                user_id=uid, alias_email="info@bomedia.net", active=True
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

    from app.models.crm import (  # noqa: PLC0415
        EmailThread,
        GmailPubsubWatch,
        UserEmailAlias,
    )

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
        # Alias activo para que la captura universal llegue a get_message
        # (y ejercite el 404) en vez de descartar por «sin alias».
        session.add(
            UserEmailAlias(
                user_id=uid, alias_email="info@bomedia.net", active=True
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
        from app.models.crm import UserEmailAlias  # noqa: PLC0415

        session.add(
            UserEmailAlias(
                user_id=uid, alias_email="info@bomedia.net", active=True
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


# ---------------------------------------------------------------------------
# CRM-BANDEJA — filtros rápidos «Con adjuntos» / «Con contacto CRM» +
# adjuntos expuestos en el thread detail
# ---------------------------------------------------------------------------


def _seed_bandeja_thread(
    session: Session,
    *,
    uid: str,
    gmail_thread_id: str,
    contact_id: str | None = None,
    message_contact_id: str | None = None,
    attachments_json: str | None = None,
    direction: str = "outbound",
    from_email: str = "user@example.com",
    created_by_user_id: str | None = None,
    is_spam: bool = False,
    thread_state: str = "inbox",
) -> tuple[str, str]:
    """Thread + 1 mensaje del user (outbound por defecto). Devuelve
    (thread_id, message_id)."""
    from app.models.crm import (  # noqa: PLC0415
        EmailDirection,
        EmailThreadState,
    )

    thread = EmailThread(
        initiated_by_user_id=uid,
        gmail_thread_id=gmail_thread_id,
        gmail_account_user_id=uid,
        subject=f"Asunto {gmail_thread_id}",
        first_message_at=datetime.now(UTC),
        last_message_at=datetime.now(UTC),
        message_count=1,
        contact_id=contact_id,
        state=EmailThreadState(thread_state),
    )
    session.add(thread)
    session.flush()
    message = EmailMessage(
        thread_id=thread.id,
        gmail_message_id=f"msg-{gmail_thread_id}",
        gmail_account_user_id=uid,
        direction=EmailDirection(direction),
        from_email=from_email,
        to_emails_json='["dest@example.com"]',
        sent_at=datetime.now(UTC),
        contact_id=message_contact_id,
        attachments_json=attachments_json,
        created_by_user_id=created_by_user_id,
        is_spam=is_spam,
    )
    session.add(message)
    session.flush()
    return thread.id, message.id


def test_list_threads_has_attachments_filter(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """`has_attachments=true` → hilos con adjunto vía sumario inline
    (`attachments_json`) O vía fila binaria en `email_message_attachments`.
    `false` → solo los hilos sin ninguno de los dos."""
    from app.models.crm import EmailMessageAttachment  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        _seed_bandeja_thread(
            session,
            uid=uid,
            gmail_thread_id="att-inline",
            attachments_json=(
                '[{"filename": "oferta.pdf",'
                ' "mime_type": "application/pdf", "size": 2048}]'
            ),
        )
        _, msg_row_id = _seed_bandeja_thread(
            session, uid=uid, gmail_thread_id="att-row"
        )
        session.add(
            EmailMessageAttachment(
                message_id=msg_row_id,
                filename="foto.jpg",
                mime_type="image/jpeg",
                size_bytes=4096,
                storage_path="2026/08/foto.jpg",
                created_at=datetime.now(UTC),
            )
        )
        _seed_bandeja_thread(session, uid=uid, gmail_thread_id="att-none")
        session.commit()

    headers = auth_headers(client, "user")
    with_att = client.get(
        "/api/emails/threads?has_attachments=true", headers=headers
    )
    assert with_att.status_code == 200
    got = {t["gmail_thread_id"] for t in with_att.json()["items"]}
    assert got == {"att-inline", "att-row"}

    without_att = client.get(
        "/api/emails/threads?has_attachments=false", headers=headers
    )
    assert without_att.status_code == 200
    got = {t["gmail_thread_id"] for t in without_att.json()["items"]}
    assert got == {"att-none"}


def test_list_threads_has_contact_filter(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """`has_contact=true` → hilos vinculados a contacto CRM, ya sea en el
    propio thread o en cualquiera de sus mensajes."""
    from app.models.crm import Contact  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        contact = Contact(first_name="Eva", email="eva@cliente.com")
        session.add(contact)
        session.flush()
        _seed_bandeja_thread(
            session,
            uid=uid,
            gmail_thread_id="ct-thread",
            contact_id=contact.id,
        )
        _seed_bandeja_thread(
            session,
            uid=uid,
            gmail_thread_id="ct-message",
            message_contact_id=contact.id,
        )
        _seed_bandeja_thread(session, uid=uid, gmail_thread_id="ct-none")
        session.commit()

    headers = auth_headers(client, "user")
    with_contact = client.get(
        "/api/emails/threads?has_contact=true", headers=headers
    )
    assert with_contact.status_code == 200
    got = {t["gmail_thread_id"] for t in with_contact.json()["items"]}
    assert got == {"ct-thread", "ct-message"}

    without_contact = client.get(
        "/api/emails/threads?has_contact=false", headers=headers
    )
    assert without_contact.status_code == 200
    got = {t["gmail_thread_id"] for t in without_contact.json()["items"]}
    assert got == {"ct-none"}


def test_thread_detail_exposes_message_attachments(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """El detail expone chips de adjuntos: fila binaria → downloadable
    (con id); solo sumario inline → metadata sin descarga."""
    from app.models.crm import EmailMessageAttachment  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread_id, message_id = _seed_bandeja_thread(
            session,
            uid=uid,
            gmail_thread_id="att-detail",
            attachments_json='[{"filename": "resumen.txt", "size": 10}]',
        )
        session.add(
            EmailMessageAttachment(
                message_id=message_id,
                filename="contrato.pdf",
                mime_type="application/pdf",
                size_bytes=1234,
                storage_path="2026/08/contrato.pdf",
                created_at=datetime.now(UTC),
            )
        )
        # Segundo mensaje SOLO con sumario inline (binario no descargado).
        thread2_id, _ = _seed_bandeja_thread(
            session,
            uid=uid,
            gmail_thread_id="att-detail-inline",
            attachments_json=(
                '[{"filename": "grande.zip",'
                ' "mime_type": "application/zip", "size": 999}]'
            ),
        )
        session.commit()

    headers = auth_headers(client, "user")
    detail = client.get(f"/api/emails/threads/{thread_id}", headers=headers)
    assert detail.status_code == 200
    atts = detail.json()["messages"][0]["attachments"]
    # La fila binaria tiene prioridad sobre el sumario inline.
    assert len(atts) == 1
    assert atts[0]["filename"] == "contrato.pdf"
    assert atts[0]["downloadable"] is True
    assert atts[0]["id"]

    detail2 = client.get(f"/api/emails/threads/{thread2_id}", headers=headers)
    assert detail2.status_code == 200
    atts2 = detail2.json()["messages"][0]["attachments"]
    assert len(atts2) == 1
    assert atts2[0]["filename"] == "grande.zip"
    assert atts2[0]["downloadable"] is False
    assert atts2[0]["id"] is None


def test_thread_detail_metadata_only_attachment_is_downloadable(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """CRM-ADJUNTOS-BACKFILL (Opción B): fila metadata-only (storage_path
    NULL pero con gmail_attachment_id) → downloadable=True vía fetch
    on-demand desde Gmail."""
    from app.models.crm import EmailMessageAttachment  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread_id, message_id = _seed_bandeja_thread(
            session, uid=uid, gmail_thread_id="att-ondemand"
        )
        session.add(
            EmailMessageAttachment(
                message_id=message_id,
                filename="factura.pdf",
                mime_type="application/pdf",
                size_bytes=2048,
                storage_path=None,
                gmail_attachment_id="att-live-1",
                created_at=datetime.now(UTC),
            )
        )
        session.commit()

    headers = auth_headers(client, "user")
    detail = client.get(f"/api/emails/threads/{thread_id}", headers=headers)
    assert detail.status_code == 200
    atts = detail.json()["messages"][0]["attachments"]
    assert len(atts) == 1
    assert atts[0]["filename"] == "factura.pdf"
    assert atts[0]["downloadable"] is True
    assert atts[0]["id"]


# ---------------------------------------------------------------------------
# CRM-BANDEJA-FIX-ENVIADOS — la carpeta «Enviados» (state=sent) filtra por
# outbound REALMENTE propio (created_by o From = alias activo del comercial)
# ---------------------------------------------------------------------------


def _seed_enviados_fixture(session: Session) -> dict[str, str]:
    """4 threads visibles para el user (initiated_by=user):
    - sent-own-alias: outbound capturado de Gmail (From = SU alias).
    - sent-own-composer: outbound del compositor CRM (created_by = user).
    - sent-false-outbound: falso outbound del backfill de junio (From
      externo, sin created_by) — NO debe salir en su Enviados.
    - inbound-only: mensaje recibido.
    """
    from app.models.crm import UserEmailAlias  # noqa: PLC0415

    uid = _user_id(session, UserRole.USER)
    session.add(
        UserEmailAlias(
            user_id=uid, alias_email="brice@artisjet.eu", active=True
        )
    )
    session.flush()
    ids = {}
    ids["own_alias"], _ = _seed_bandeja_thread(
        session,
        uid=uid,
        gmail_thread_id="sent-own-alias",
        from_email="Brice@Artisjet.eu".lower(),
    )
    ids["own_composer"], _ = _seed_bandeja_thread(
        session,
        uid=uid,
        gmail_thread_id="sent-own-composer",
        from_email="user@example.com",
        created_by_user_id=uid,
    )
    ids["false_outbound"], _ = _seed_bandeja_thread(
        session,
        uid=uid,
        gmail_thread_id="sent-false-outbound",
        from_email="noreply@leboncoin.fr",
    )
    ids["inbound_only"], _ = _seed_bandeja_thread(
        session,
        uid=uid,
        gmail_thread_id="inbound-only",
        direction="inbound",
        from_email="cliente@fuera.com",
    )
    session.commit()
    return ids


def test_list_threads_state_sent_returns_only_outbound_owned(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        _seed_enviados_fixture(session)

    response = client.get(
        "/api/emails/threads?state=sent", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    got = {t["gmail_thread_id"] for t in response.json()["items"]}
    # Solo los enviados PROPIOS: por alias en el From o por created_by.
    assert got == {"sent-own-alias", "sent-own-composer"}


def test_list_threads_state_sent_hides_inbound_threads(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        _seed_enviados_fixture(session)

    response = client.get(
        "/api/emails/threads?state=sent", headers=auth_headers(client, "user")
    )
    got = {t["gmail_thread_id"] for t in response.json()["items"]}
    assert "inbound-only" not in got
    # El falso outbound del backfill de junio (From externo) tampoco sale.
    assert "sent-false-outbound" not in got


def test_list_threads_state_sent_admin_sees_all_outbound(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        _seed_enviados_fixture(session)

    # Admin sin condición de propiedad — con scope=team ve TODOS los hilos
    # con outbound del equipo (consistente con el resto de la bandeja).
    response = client.get(
        "/api/emails/threads?state=sent&scope=team",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200
    got = {t["gmail_thread_id"] for t in response.json()["items"]}
    assert got == {
        "sent-own-alias",
        "sent-own-composer",
        "sent-false-outbound",
    }
    assert "inbound-only" not in got


def test_list_threads_state_inbox_still_shows_all_visible_threads(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Regresión: «Bandeja» (default, sin state) sigue mostrando todo lo
    visible del comercial — inbound y outbound."""
    with session_factory() as session:
        _seed_enviados_fixture(session)

    response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    got = {t["gmail_thread_id"] for t in response.json()["items"]}
    assert got == {
        "sent-own-alias",
        "sent-own-composer",
        "sent-false-outbound",
        "inbound-only",
    }


# ---------------------------------------------------------------------------
# CRM-BANDEJA-FIX-SPAM — Bandeja oculta spam de Gmail por defecto; carpeta
# Spam muestra los hilos con mensajes is_spam=true (sincronizados de Gmail)
# ---------------------------------------------------------------------------


def _seed_spam_fixture(session: Session) -> None:
    """Para el user: 1 hilo normal (inbox), 1 hilo con spam de Gmail
    (is_spam=true, state sigue inbox), 1 hilo movido a spam desde el CRM
    (state=SPAM)."""
    uid = _user_id(session, UserRole.USER)
    _seed_bandeja_thread(
        session, uid=uid, gmail_thread_id="normal", direction="inbound",
        from_email="cliente@fuera.com",
    )
    _seed_bandeja_thread(
        session, uid=uid, gmail_thread_id="gmail-spam", direction="inbound",
        from_email="promo@spam.com", is_spam=True,
    )
    _seed_bandeja_thread(
        session, uid=uid, gmail_thread_id="crm-spam", direction="inbound",
        from_email="otro@spam.com", thread_state="spam",
    )
    session.commit()


def test_list_threads_inbox_excludes_spam_by_default(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        _seed_spam_fixture(session)

    # Bandeja por defecto (sin state) NO debe traer el hilo con spam Gmail.
    response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    got = {t["gmail_thread_id"] for t in response.json()["items"]}
    assert got == {"normal"}
    assert "gmail-spam" not in got

    # state=inbox explícito → mismo comportamiento.
    response2 = client.get(
        "/api/emails/threads?state=inbox", headers=auth_headers(client, "user")
    )
    got2 = {t["gmail_thread_id"] for t in response2.json()["items"]}
    assert got2 == {"normal"}


def test_list_threads_spam_returns_only_is_spam_true(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        _seed_spam_fixture(session)

    response = client.get(
        "/api/emails/threads?state=spam", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    got = {t["gmail_thread_id"] for t in response.json()["items"]}
    # Carpeta Spam: el spam sincronizado de Gmail (is_spam) + el movido a
    # spam desde el CRM (state=SPAM). NO el hilo normal.
    assert got == {"gmail-spam", "crm-spam"}
    assert "normal" not in got


def test_list_threads_spam_can_be_forced_to_show_in_inbox(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """`exclude_spam=false` explícito fuerza a incluir el spam de Gmail en
    la Bandeja (vía "ver todo" / ficha) — el default no rompe el override."""
    with session_factory() as session:
        _seed_spam_fixture(session)

    response = client.get(
        "/api/emails/threads?state=inbox&exclude_spam=false",
        headers=auth_headers(client, "user"),
    )
    got = {t["gmail_thread_id"] for t in response.json()["items"]}
    assert "gmail-spam" in got


def test_is_spam_marked_when_gmail_labels_include_SPAM(
    session_factory: sessionmaker,
) -> None:
    """Regresión de la persistencia: un mensaje con label SPAM de Gmail se
    guarda con is_spam=true (la base del filtro de carpeta Spam)."""
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415
    from app.models.crm import UserEmailAlias  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        session.add(
            UserEmailAlias(
                user_id=uid, alias_email="norma@bomedia.net", active=True
            )
        )
        session.commit()
        alias_map = {"norma@bomedia.net": "norma@bomedia.net"}
        raw = {
            "id": "spam-msg-1",
            "threadId": "spam-thr-1",
            "snippet": "gana dinero ya",
            "labelIds": ["SPAM"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "promo@spam.com"},
                    {"name": "To", "value": "norma@bomedia.net"},
                    {"name": "Subject", "value": "Oferta"},
                    {"name": "Date", "value": "Mon, 03 Aug 2026 10:00:00 +0000"},
                ],
                "mimeType": "text/plain",
                "body": {"data": "aG9sYQ=="},
            },
        }
        msg = gmail_service._persist_message(
            session,
            user_id=uid,
            raw=raw,
            gmail_thread_id="spam-thr-1",
            alias_map=alias_map,
            emit_activity=False,
        )
        session.commit()
        assert msg is not None
        assert msg.is_spam is True


def test_bandeja_admin_scope_team_still_excludes_spam(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """La vista de equipo del admin (scope=team, state inbox) sigue
    ocultando el spam de Gmail — la exclusión depende de la vista, no del
    scope."""
    with session_factory() as session:
        _seed_spam_fixture(session)

    response = client.get(
        "/api/emails/threads?scope=team", headers=auth_headers(client, "admin")
    )
    assert response.status_code == 200
    got = {t["gmail_thread_id"] for t in response.json()["items"]}
    assert "gmail-spam" not in got
    assert "normal" in got

    # Pero la carpeta Spam del admin SÍ lo muestra.
    spam = client.get(
        "/api/emails/threads?state=spam&scope=team",
        headers=auth_headers(client, "admin"),
    )
    got_spam = {t["gmail_thread_id"] for t in spam.json()["items"]}
    assert "gmail-spam" in got_spam
