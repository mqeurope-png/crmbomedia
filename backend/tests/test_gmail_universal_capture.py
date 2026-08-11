"""CRM-GMAIL — captura universal + spam sync + alias por usuario + webhook JWT.

El upstream de Gmail está mockeado (fake client). Cubre las Partes A/C/D/E.
"""
from __future__ import annotations

import base64
import importlib.util
import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    EmailMessage,
    EmailThread,
    GmailPubsubWatch,
    User,
    UserEmailAlias,
    UserRole,
)
from tests._test_helpers import (
    auth_headers,
    seed_org_google_integration,
    seed_test_users,
)


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


def _seed_watch(session: Session, user_id: str, history_id: int = 1) -> None:
    session.add(
        GmailPubsubWatch(
            user_id=user_id,
            history_id=history_id,
            watch_expires_at=datetime.now(UTC) + timedelta(days=6),
            last_renewed_at=datetime.now(UTC),
            topic_name="projects/x/topics/y",
        )
    )


def _inbound_message(
    mid: str, thread_id: str, to: str, *, labels: list[str] | None = None
) -> dict[str, Any]:
    return {
        "id": mid,
        "threadId": thread_id,
        "snippet": "hola",
        "labelIds": labels if labels is not None else ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": "desconocido@fuera.com"},
                {"name": "To", "value": to},
                {"name": "Subject", "value": "Consulta"},
                {"name": "Date", "value": "Fri, 31 Dec 2099 23:59:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"cuerpo").decode()},
        },
    }


# ---------------------------------------------------------------------------
# Parte A — CRUD de alias
# ---------------------------------------------------------------------------


def test_alias_admin_only_create_delete(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        user_id = _user_id(session, UserRole.USER)

    # No-admin no puede crear.
    forbidden = client.post(
        f"/api/users/{user_id}/aliases",
        json={"alias_email": "norma@bomedia.net"},
        headers=auth_headers(client, "user"),
    )
    assert forbidden.status_code == 403

    # Admin crea.
    created = client.post(
        f"/api/users/{user_id}/aliases",
        json={"alias_email": "Norma@Bomedia.net"},
        headers=auth_headers(client, "admin"),
    )
    assert created.status_code == 201, created.text
    alias_id = created.json()["id"]
    assert created.json()["alias_email"] == "norma@bomedia.net"  # normalizado

    # El propio user ve su alias; no puede ver el de otro.
    own = client.get(
        f"/api/users/{user_id}/aliases", headers=auth_headers(client, "user")
    )
    assert own.status_code == 200
    assert len(own.json()) == 1
    with session_factory() as session:
        other_id = _user_id(session, UserRole.VIEWER)
    denied = client.get(
        f"/api/users/{other_id}/aliases", headers=auth_headers(client, "user")
    )
    assert denied.status_code == 403

    # No-admin no puede borrar; admin sí.
    assert (
        client.delete(
            f"/api/users/{user_id}/aliases/{alias_id}",
            headers=auth_headers(client, "user"),
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/users/{user_id}/aliases/{alias_id}",
            headers=auth_headers(client, "admin"),
        ).status_code
        == 204
    )


def test_alias_unique_globally_across_users(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        user_id = _user_id(session, UserRole.USER)
        viewer_id = _user_id(session, UserRole.VIEWER)

    first = client.post(
        f"/api/users/{user_id}/aliases",
        json={"alias_email": "ventas@bomedia.net"},
        headers=auth_headers(client, "admin"),
    )
    assert first.status_code == 201
    # El MISMO alias para OTRO usuario → 409 (unique global).
    dup = client.post(
        f"/api/users/{viewer_id}/aliases",
        json={"alias_email": "ventas@bomedia.net"},
        headers=auth_headers(client, "admin"),
    )
    assert dup.status_code == 409, dup.text


def test_alias_patch_toggles_active(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        user_id = _user_id(session, UserRole.USER)
    created = client.post(
        f"/api/users/{user_id}/aliases",
        json={"alias_email": "sat@bomedia.net"},
        headers=auth_headers(client, "admin"),
    ).json()
    patched = client.patch(
        f"/api/users/{user_id}/aliases/{created['id']}",
        json={"active": False},
        headers=auth_headers(client, "admin"),
    )
    assert patched.status_code == 200
    assert patched.json()["active"] is False


# ---------------------------------------------------------------------------
# Parte C — captura universal
# ---------------------------------------------------------------------------


def _run_process_history(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    *,
    owner_id: str,
    fake_client: type,
    new_history_id: int | None = 200,
) -> int:
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", fake_client
    )
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    with session_factory() as session:
        imported = gmail_service.process_history(
            session, user_id=owner_id, new_history_id=new_history_id
        )
        session.commit()
    return imported


def test_gmail_sync_captures_email_from_unknown_sender_if_delivered_to_active_alias(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
        session.add(
            UserEmailAlias(
                user_id=owner_id, alias_email="norma@bomedia.net", active=True
            )
        )
        _seed_watch(session, owner_id)
        session.commit()
    seed_org_google_integration_for(session_factory, owner_id)

    class _Fake:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_history(self, _start: int) -> dict[str, Any]:
            return {
                "history": [
                    {"messagesAdded": [
                        {"message": {"id": "u1", "threadId": "t1",
                                     "labelIds": ["INBOX"]}}
                    ]}
                ]
            }

        def get_message(self, _mid: str) -> dict[str, Any]:
            return _inbound_message("u1", "t1", "norma@bomedia.net")

    imported = _run_process_history(
        session_factory, monkeypatch, owner_id=owner_id, fake_client=_Fake
    )
    assert imported == 1
    with session_factory() as session:
        msg = session.scalar(select(EmailMessage))
        assert msg is not None
        # Remitente desconocido → contact_id NULL, pero se guarda igual.
        assert msg.contact_id is None
        assert msg.from_email == "desconocido@fuera.com"
        assert msg.delivered_to == "norma@bomedia.net"
        assert msg.is_spam is False
        assert msg.imported_via == "incoming_realtime"


def test_gmail_sync_skips_email_not_delivered_to_any_alias(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
        session.add(
            UserEmailAlias(
                user_id=owner_id, alias_email="norma@bomedia.net", active=True
            )
        )
        _seed_watch(session, owner_id)
        session.commit()
    seed_org_google_integration_for(session_factory, owner_id)

    class _Fake:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_history(self, _start: int) -> dict[str, Any]:
            return {
                "history": [
                    {"messagesAdded": [
                        {"message": {"id": "x1", "threadId": "t9",
                                     "labelIds": ["INBOX"]}}
                    ]}
                ]
            }

        def get_message(self, _mid: str) -> dict[str, Any]:
            # Dirigido a un alias que NO está configurado.
            return _inbound_message("x1", "t9", "otro@ajeno.com")

    imported = _run_process_history(
        session_factory, monkeypatch, owner_id=owner_id, fake_client=_Fake
    )
    assert imported == 0
    with session_factory() as session:
        assert session.scalar(select(EmailMessage)) is None


def test_gmail_sync_marks_spam_from_labels(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
        session.add(
            UserEmailAlias(
                user_id=owner_id, alias_email="norma@bomedia.net", active=True
            )
        )
        _seed_watch(session, owner_id)
        session.commit()
    seed_org_google_integration_for(session_factory, owner_id)

    class _Fake:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_history(self, _start: int) -> dict[str, Any]:
            return {
                "history": [
                    {"messagesAdded": [
                        {"message": {"id": "s1", "threadId": "t1",
                                     "labelIds": ["SPAM"]}}
                    ]}
                ]
            }

        def get_message(self, _mid: str) -> dict[str, Any]:
            return _inbound_message(
                "s1", "t1", "norma@bomedia.net", labels=["SPAM"]
            )

    imported = _run_process_history(
        session_factory, monkeypatch, owner_id=owner_id, fake_client=_Fake
    )
    assert imported == 1
    with session_factory() as session:
        msg = session.scalar(select(EmailMessage))
        assert msg.is_spam is True
        assert "SPAM" in json.loads(msg.gmail_labels)


def test_gmail_sync_unmarks_spam_when_label_removed(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
        thread = EmailThread(
            initiated_by_user_id=owner_id,
            gmail_thread_id="t1",
            gmail_account_user_id=owner_id,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
        )
        session.add(thread)
        session.flush()
        session.add(
            EmailMessage(
                thread_id=thread.id,
                gmail_message_id="spammy",
                gmail_account_user_id=owner_id,
                direction="inbound",
                from_email="x@y.com",
                to_emails_json='["norma@bomedia.net"]',
                delivered_to="norma@bomedia.net",
                is_spam=True,
                sent_at=datetime.now(UTC),
            )
        )
        _seed_watch(session, owner_id)
        session.commit()
    seed_org_google_integration_for(session_factory, owner_id)

    class _Fake:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_history(self, _start: int) -> dict[str, Any]:
            return {
                "history": [
                    {"labelsRemoved": [
                        {"message": {"id": "spammy", "labelIds": ["INBOX"]},
                         "labelIds": ["SPAM"]}
                    ]}
                ]
            }

        def get_message(self, _mid: str) -> dict[str, Any]:  # pragma: no cover
            raise AssertionError("no debería pedir el mensaje")

    _run_process_history(
        session_factory, monkeypatch, owner_id=owner_id, fake_client=_Fake
    )
    with session_factory() as session:
        msg = session.scalar(
            select(EmailMessage).where(
                EmailMessage.gmail_message_id == "spammy"
            )
        )
        assert msg.is_spam is False


def test_process_history_idempotent(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
        session.add(
            UserEmailAlias(
                user_id=owner_id, alias_email="norma@bomedia.net", active=True
            )
        )
        _seed_watch(session, owner_id)
        session.commit()
    seed_org_google_integration_for(session_factory, owner_id)

    class _Fake:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_history(self, _start: int) -> dict[str, Any]:
            return {
                "history": [
                    {"messagesAdded": [
                        {"message": {"id": "dup1", "threadId": "t1",
                                     "labelIds": ["INBOX"]}}
                    ]}
                ]
            }

        def get_message(self, _mid: str) -> dict[str, Any]:
            return _inbound_message("dup1", "t1", "norma@bomedia.net")

    first = _run_process_history(
        session_factory, monkeypatch, owner_id=owner_id, fake_client=_Fake
    )
    second = _run_process_history(
        session_factory, monkeypatch, owner_id=owner_id, fake_client=_Fake
    )
    assert first == 1
    assert second == 0  # ya visto → no re-importa
    with session_factory() as session:
        count = len(list(session.scalars(select(EmailMessage))))
        assert count == 1


def test_process_history_incremental_from_last_id(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
        _seed_watch(session, owner_id, history_id=555)
        session.commit()
    seed_org_google_integration_for(session_factory, owner_id)

    seen_starts: list[int] = []

    class _Fake:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_history(self, start: int) -> dict[str, Any]:
            seen_starts.append(start)
            return {"history": []}

    _run_process_history(
        session_factory,
        monkeypatch,
        owner_id=owner_id,
        fake_client=_Fake,
        new_history_id=999,
    )
    # Arrancó desde el cursor guardado y lo avanzó al nuevo.
    assert seen_starts == [555]
    with session_factory() as session:
        watch = session.scalar(select(GmailPubsubWatch))
        assert watch.history_id == 999


def test_watch_renewal_cron_extends_before_expiry(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415
    from app.integrations.gmail.jobs import watches_expiring_soon

    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
        # Watch que caduca en 12h → «expiring soon».
        session.add(
            GmailPubsubWatch(
                user_id=owner_id,
                history_id=10,
                watch_expires_at=datetime.now(UTC) + timedelta(hours=12),
                last_renewed_at=datetime.now(UTC) - timedelta(days=6),
                topic_name="projects/x/topics/y",
            )
        )
        session.commit()
    seed_org_google_integration_for(session_factory, owner_id)

    class _Fake:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def watch_mailbox(self, _topic: str, label_ids=None) -> dict[str, Any]:
            future = int(
                (datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000
            )
            return {"historyId": 42, "expiration": future}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _Fake
    )
    from app.core.config import get_settings

    monkeypatch.setattr(
        get_settings(), "gmail_pubsub_topic", "projects/x/topics/y"
    )

    with session_factory() as session:
        assert len(watches_expiring_soon(session, days=1)) == 1
        gmail_service.register_watch(session, user_id=owner_id)
        session.commit()
    with session_factory() as session:
        watch = session.scalar(select(GmailPubsubWatch))
        # Renovado: expiry a ~7 días, ya no «expiring soon».
        assert watch.watch_expires_at > datetime.now(UTC) + timedelta(days=6)
        assert len(watches_expiring_soon(session, days=1)) == 0


# ---------------------------------------------------------------------------
# Parte D — webhook JWT
# ---------------------------------------------------------------------------


def _pubsub_body(email: str, history_id: int) -> dict[str, Any]:
    data = base64.b64encode(
        json.dumps({"emailAddress": email, "historyId": history_id}).encode()
    ).decode()
    return {"message": {"data": data, "messageId": "m1"}}


def test_webhook_enqueues_job_and_returns_200(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
    seed_org_google_integration_for(session_factory, owner_id)
    enqueued: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.integrations.gmail.jobs.enqueue_process_history",
        lambda *, user_id, new_history_id: enqueued.append(
            (user_id, new_history_id)
        ),
    )
    resp = client.post(
        "/api/webhooks/gmail", json=_pubsub_body("bart@bomedia.net", 321)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "enqueued"
    assert enqueued == [(owner_id, 321)]


def test_webhook_validates_jwt_from_pubsub(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
    seed_org_google_integration_for(session_factory, owner_id)
    monkeypatch.setattr(
        "app.integrations.gmail.jobs.enqueue_process_history",
        lambda *, user_id, new_history_id: None,
    )
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        settings, "gmail_webhook_jwt_audience", "https://bo/webhooks/gmail"
    )
    monkeypatch.setattr(
        settings, "gmail_webhook_service_account_email", "sa@proj.iam.g.com"
    )
    import google.oauth2.id_token as id_token_lib

    monkeypatch.setattr(
        id_token_lib,
        "verify_oauth2_token",
        lambda *_a, **_k: {"email": "sa@proj.iam.g.com", "email_verified": True},
    )
    resp = client.post(
        "/api/webhooks/gmail",
        json=_pubsub_body("bart@bomedia.net", 5),
        headers={"Authorization": "Bearer good.jwt.token"},
    )
    assert resp.status_code == 200, resp.text


def test_webhook_rejects_invalid_jwt(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
    seed_org_google_integration_for(session_factory, owner_id)
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        settings, "gmail_webhook_jwt_audience", "https://bo/webhooks/gmail"
    )
    monkeypatch.setattr(
        settings, "gmail_webhook_service_account_email", "sa@proj.iam.g.com"
    )
    import google.oauth2.id_token as id_token_lib

    def _boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise ValueError("bad signature")

    monkeypatch.setattr(id_token_lib, "verify_oauth2_token", _boom)
    resp = client.post(
        "/api/webhooks/gmail",
        json=_pubsub_body("bart@bomedia.net", 5),
        headers={"Authorization": "Bearer forged"},
    )
    assert resp.status_code == 401


def test_webhook_rejects_jwt_from_wrong_service_account(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
    seed_org_google_integration_for(session_factory, owner_id)
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        settings, "gmail_webhook_jwt_audience", "https://bo/webhooks/gmail"
    )
    monkeypatch.setattr(
        settings, "gmail_webhook_service_account_email", "sa@proj.iam.g.com"
    )
    import google.oauth2.id_token as id_token_lib

    # Firma válida de Google pero de OTRO service account.
    monkeypatch.setattr(
        id_token_lib,
        "verify_oauth2_token",
        lambda *_a, **_k: {"email": "attacker@evil.com", "email_verified": True},
    )
    resp = client.post(
        "/api/webhooks/gmail",
        json=_pubsub_body("bart@bomedia.net", 5),
        headers={"Authorization": "Bearer valid-but-wrong-sa"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Parte E — visibilidad por alias en la lista de emails
# ---------------------------------------------------------------------------


def _seed_inbound_thread(
    session: Session, *, owner_id: str, thread_gid: str, delivered_to: str,
    is_spam: bool = False,
) -> str:
    thread = EmailThread(
        initiated_by_user_id=owner_id,
        gmail_thread_id=thread_gid,
        gmail_account_user_id=owner_id,
        first_message_at=datetime.now(UTC),
        last_message_at=datetime.now(UTC),
        message_count=1,
        state="inbox",
    )
    session.add(thread)
    session.flush()
    session.add(
        EmailMessage(
            thread_id=thread.id,
            gmail_message_id=f"m-{thread_gid}",
            gmail_account_user_id=owner_id,
            direction="inbound",
            from_email="cliente@fuera.com",
            to_emails_json=json.dumps([delivered_to]),
            delivered_to=delivered_to,
            is_spam=is_spam,
            sent_at=datetime.now(UTC),
        )
    )
    return thread.id


def test_list_emails_filtered_by_current_user_aliases(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        org_user = _user_id(session, UserRole.MANAGER)  # cuenta org
        norma = _user_id(session, UserRole.USER)
        session.add(
            UserEmailAlias(
                user_id=norma, alias_email="norma@bomedia.net", active=True
            )
        )
        # Uno a norma@, otro a manel@ (de otro comercial, sin alias de norma).
        _seed_inbound_thread(
            session, owner_id=org_user, thread_gid="to-norma",
            delivered_to="norma@bomedia.net",
        )
        _seed_inbound_thread(
            session, owner_id=org_user, thread_gid="to-manel",
            delivered_to="manel@bomedia.net",
        )
        session.commit()

    resp = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    body = resp.json()
    gids = {t["gmail_thread_id"] for t in body["items"]}
    assert gids == {"to-norma"}, body


def test_list_emails_admin_sees_all(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        org_user = _user_id(session, UserRole.MANAGER)
        _seed_inbound_thread(
            session, owner_id=org_user, thread_gid="to-norma",
            delivered_to="norma@bomedia.net",
        )
        _seed_inbound_thread(
            session, owner_id=org_user, thread_gid="to-manel",
            delivered_to="manel@bomedia.net",
        )
        session.commit()

    # Admin con scope=team ve todo.
    resp = client.get(
        "/api/emails/threads?scope=team", headers=auth_headers(client, "admin")
    )
    assert resp.json()["total"] == 2


def test_list_emails_spam_hidden_from_bandeja_shown_in_spam_folder(
    client: TestClient, session_factory: sessionmaker
) -> None:
    # CRM-BANDEJA-FIX-SPAM revisa la decisión de CRM-GMAIL: el spam de
    # Gmail YA NO se mezcla en la Bandeja; se oculta por defecto y vive en
    # la carpeta Spam (el chip sigue existiendo al abrir el hilo).
    with session_factory() as session:
        org_user = _user_id(session, UserRole.MANAGER)
        norma = _user_id(session, UserRole.USER)
        session.add(
            UserEmailAlias(
                user_id=norma, alias_email="norma@bomedia.net", active=True
            )
        )
        _seed_inbound_thread(
            session, owner_id=org_user, thread_gid="spammy",
            delivered_to="norma@bomedia.net", is_spam=True,
        )
        session.commit()

    # Bandeja por defecto: el spam se oculta.
    resp = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    assert resp.json()["total"] == 0, resp.json()

    # Carpeta Spam: aparece (con su chip has_spam).
    resp_spam = client.get(
        "/api/emails/threads?state=spam", headers=auth_headers(client, "user")
    )
    body_spam = resp_spam.json()
    assert body_spam["total"] == 1, body_spam
    assert body_spam["items"][0]["has_spam"] is True

    # `exclude_spam=false` fuerza a verlo también en la Bandeja.
    resp3 = client.get(
        "/api/emails/threads?exclude_spam=false",
        headers=auth_headers(client, "user"),
    )
    assert resp3.json()["total"] == 1


# ---------------------------------------------------------------------------
# Migración 0090 — semilla + backfill de delivered_to
# ---------------------------------------------------------------------------


def _load_migration_module():
    path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "20260807_0090_gmail_universal_capture.py"
    )
    spec = importlib.util.spec_from_file_location("mig_0090", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_seed_and_backfill(session_factory: sessionmaker) -> None:
    mig = _load_migration_module()
    with session_factory() as session:
        owner_id = _user_id(session, UserRole.USER)
        email = session.scalar(select(User.email).where(User.id == owner_id))
        # Un email ya existente dirigido a la dirección del user.
        thread = EmailThread(
            initiated_by_user_id=owner_id,
            gmail_thread_id="old",
            gmail_account_user_id=owner_id,
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
        )
        session.add(thread)
        session.flush()
        session.add(
            EmailMessage(
                thread_id=thread.id,
                gmail_message_id="old-1",
                gmail_account_user_id=owner_id,
                direction="inbound",
                from_email="x@y.com",
                to_emails_json=json.dumps([email]),
                sent_at=datetime.now(UTC),
            )
        )
        session.commit()

        bind = session.connection()
        seeded = mig._seed_aliases(bind)
        assert seeded >= 1  # al menos un alias por user activo
        backfilled = mig._backfill_delivered_to(bind)
        assert backfilled == 1
        session.commit()

    with session_factory() as session:
        # El alias se sembró desde users.email (único global).
        alias = session.scalar(
            select(UserEmailAlias).where(UserEmailAlias.user_id == owner_id)
        )
        assert alias is not None and alias.alias_email == email
        # delivered_to quedó backfilled con esa dirección.
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.gmail_message_id == "old-1")
        )
        assert msg.delivered_to == email


def seed_org_google_integration_for(
    session_factory: sessionmaker, user_id: str
) -> None:
    with session_factory() as session:
        seed_org_google_integration(
            session, connected_by_user_id=user_id
        )
        session.commit()
