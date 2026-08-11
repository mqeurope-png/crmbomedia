"""CRM-ETIQUETAS-GMAIL-V2.3 — labels de Gmail en el CRM.

Import de labels personalizadas (sync_labels), mapeo retroactivo desde el
JSON `gmail_labels`, sync go-forward vía process_history (labelsAdded/
Removed + import on-the-fly), endpoints de mensaje con propagación
CRM→Gmail (messages.modify) y filtro `label_id` a nivel de mensaje.
El upstream de Gmail está mockeado.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Registra TODOS los modelos en Base.metadata.
import app.main  # noqa: F401
from app.core import crypto
from app.db.session import get_session
from app.integrations.gmail import service as gmail_service
from app.integrations.gmail.labels_sync import (
    is_custom_label_id,
    sync_gmail_labels,
)
from app.main import app
from app.models.crm import (
    ORG_GOOGLE_SINGLETON_ID,
    Base,
    EmailDirection,
    EmailLabel,
    EmailMessage,
    EmailMessageLabel,
    EmailThread,
    EmailThreadLabel,
    GmailPubsubWatch,
    OrgGoogleIntegration,
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
        seed.add(
            OrgGoogleIntegration(
                id=ORG_GOOGLE_SINGLETON_ID,
                google_email="mqeurope@gmail.com",
                access_token_encrypted=crypto.encrypt("fake-access"),
                refresh_token_encrypted=crypto.encrypt("fake-refresh"),
                scopes="https://www.googleapis.com/auth/gmail.send",
                token_expires_at=datetime(2099, 1, 1, tzinfo=UTC),
                connected_at=datetime.now(UTC),
                connected_by_user_id=owner,
                status="active",
            )
        )
        seed.commit()
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
    session: Session,
    *,
    uid: str,
    gid: str,
    messages: list[dict[str, Any]],
    initiated_by: str | None = None,
) -> str:
    thread = EmailThread(
        initiated_by_user_id=initiated_by or uid,
        gmail_thread_id=gid,
        gmail_account_user_id=uid,
        subject=f"Asunto {gid}",
        first_message_at=NOW,
        last_message_at=NOW,
        message_count=len(messages),
    )
    session.add(thread)
    session.flush()
    for idx, spec in enumerate(messages):
        labels = spec.get("gmail_labels")
        session.add(
            EmailMessage(
                thread_id=thread.id,
                gmail_message_id=spec.get("gmail_message_id", f"{gid}-m{idx}"),
                gmail_account_user_id=uid,
                direction=EmailDirection(spec.get("direction", "inbound")),
                from_email=spec.get("from_email", "cliente@fuera.com"),
                to_emails_json='["norma@bomedia.net"]',
                delivered_to=spec.get("delivered_to", ALIAS),
                sent_at=NOW,
                gmail_labels=json.dumps(labels) if labels else None,
            )
        )
    session.flush()
    return thread.id


def _seed_gmail_label(
    session: Session, gid: str = "Label_1", name: str = "Clientes VIP"
) -> str:
    label = EmailLabel(
        user_id=None, name=name, color="#fb4c2f", gmail_label_id=gid
    )
    session.add(label)
    session.flush()
    return label.id


def _seed_watch(session: Session, user_id: str) -> None:
    session.add(
        GmailPubsubWatch(
            user_id=user_id,
            history_id=1,
            watch_expires_at=datetime.now(UTC) + timedelta(days=6),
            last_renewed_at=datetime.now(UTC),
            topic_name="projects/x/topics/y",
        )
    )


UPSTREAM_LABELS = [
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "CATEGORY_SOCIAL", "name": "CATEGORY_SOCIAL", "type": "system"},
    {
        "id": "Label_1",
        "name": "Clientes VIP",
        "type": "user",
        "color": {"backgroundColor": "#fb4c2f"},
    },
    {"id": "Label_2", "name": "Proveedores", "type": "user"},
]


class _FakeLabelsClient:
    """GmailClient mínimo para sync_labels / process_history."""

    upstream: list[dict[str, Any]] = UPSTREAM_LABELS
    history: dict[str, Any] = {"history": []}
    labels_by_id: dict[str, dict[str, Any]] = {}
    modify_calls: list[dict[str, Any]] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def labels_list(self) -> list[dict[str, Any]]:
        return list(self.upstream)

    def labels_get(self, label_id: str) -> dict[str, Any]:
        try:
            return self.labels_by_id[label_id]
        except KeyError as exc:
            raise RuntimeError("404 label not found") from exc

    def list_history(self, _start: int) -> dict[str, Any]:
        return self.history

    def modify_message(
        self,
        message_id: str,
        *,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        type(self).modify_calls.append(
            {
                "message_id": message_id,
                "add": add_label_ids,
                "remove": remove_label_ids,
            }
        )
        return {"id": message_id}


@pytest.fixture(autouse=True)
def _reset_fake() -> Generator[None, None, None]:
    _FakeLabelsClient.upstream = UPSTREAM_LABELS
    _FakeLabelsClient.history = {"history": []}
    _FakeLabelsClient.labels_by_id = {}
    _FakeLabelsClient.modify_calls = []
    yield


def _patch_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeLabelsClient
    )


# ---------------------------------------------------------------------------
# is_custom_label_id
# ---------------------------------------------------------------------------


def test_is_custom_label_id() -> None:
    assert is_custom_label_id("Label_123")
    assert not is_custom_label_id("INBOX")
    assert not is_custom_label_id("CATEGORY_SOCIAL")
    assert not is_custom_label_id("SPAM")
    assert not is_custom_label_id("")


# ---------------------------------------------------------------------------
# sync_labels — import + retroactivo
# ---------------------------------------------------------------------------


def test_sync_imports_only_custom_labels(
    factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch)
    with factory() as session:
        uid = _uid(session)
        report = sync_gmail_labels(session, user_id=uid)
        session.commit()
        assert report.labels_created == 2
        assert report.labels_skipped_system == 2
        labels = list(session.scalars(select(EmailLabel)))
        assert {lbl.gmail_label_id for lbl in labels} == {"Label_1", "Label_2"}
        assert all(lbl.user_id is None for lbl in labels)
        vip = next(lbl for lbl in labels if lbl.gmail_label_id == "Label_1")
        assert vip.name == "Clientes VIP"
        assert vip.color == "#fb4c2f"

        # Idempotente: segunda pasada no crea nada.
        report2 = sync_gmail_labels(session, user_id=uid)
        session.commit()
        assert report2.labels_created == 0
        assert session.scalar(select(EmailLabel.id)) is not None
        assert len(list(session.scalars(select(EmailLabel)))) == 2


def test_sync_updates_renamed_label(
    factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch)
    with factory() as session:
        uid = _uid(session)
        sync_gmail_labels(session, user_id=uid)
        session.commit()
        _FakeLabelsClient.upstream = [
            {"id": "Label_1", "name": "VIP renombrado", "type": "user"},
        ]
        report = sync_gmail_labels(session, user_id=uid)
        session.commit()
        assert report.labels_updated == 1
        label = session.scalar(
            select(EmailLabel).where(EmailLabel.gmail_label_id == "Label_1")
        )
        assert label is not None and label.name == "VIP renombrado"


def test_sync_retroactive_mapping_from_json(
    factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch)
    with factory() as session:
        uid = _uid(session)
        _seed_thread(
            session,
            uid=uid,
            gid="t1",
            messages=[{"gmail_labels": ["INBOX", "Label_1"]}],
        )
        _seed_thread(
            session,
            uid=uid,
            gid="t2",
            messages=[{"gmail_labels": ["INBOX"]}],
        )
        session.commit()

        report = sync_gmail_labels(session, user_id=uid)
        session.commit()
        assert report.mappings_created == 1
        rows = list(session.scalars(select(EmailMessageLabel)))
        assert len(rows) == 1
        label = session.get(EmailLabel, rows[0].label_id)
        assert label is not None and label.gmail_label_id == "Label_1"

        # Idempotente.
        report2 = sync_gmail_labels(session, user_id=uid)
        session.commit()
        assert report2.mappings_created == 0
        assert len(list(session.scalars(select(EmailMessageLabel)))) == 1


def test_sync_dry_run_counts_without_writing(
    factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch)
    with factory() as session:
        uid = _uid(session)
        _seed_thread(
            session,
            uid=uid,
            gid="t1",
            messages=[{"gmail_labels": ["Label_1", "Label_2"]}],
        )
        session.commit()
        report = sync_gmail_labels(session, user_id=uid, dry_run=True)
        session.commit()
        assert report.labels_created == 2
        assert report.mappings_created == 2
        assert list(session.scalars(select(EmailLabel))) == []
        assert list(session.scalars(select(EmailMessageLabel))) == []


# ---------------------------------------------------------------------------
# GET /api/emails/labels — org + personales + counts
# ---------------------------------------------------------------------------


def test_list_labels_includes_org_labels_with_counts(
    client: TestClient, factory: sessionmaker
) -> None:
    with factory() as session:
        uid = _uid(session)
        other = _uid(session, UserRole.VIEWER)
        label_id = _seed_gmail_label(session)
        # Personal del caller + personal de OTRO user (no debe salir).
        mine = EmailLabel(user_id=uid, name="Mía", sort_order=0)
        theirs = EmailLabel(user_id=other, name="Ajena", sort_order=0)
        session.add_all([mine, theirs])
        session.flush()
        thread_id = _seed_thread(
            session, uid=uid, gid="t1", messages=[{}, {}]
        )
        messages = list(
            session.scalars(
                select(EmailMessage).where(EmailMessage.thread_id == thread_id)
            )
        )
        # Dos mensajes del MISMO hilo etiquetados → cuenta 1 hilo.
        for msg in messages:
            session.add(
                EmailMessageLabel(
                    message_id=msg.id, label_id=label_id, applied_at=NOW
                )
            )
        session.add(
            EmailThreadLabel(
                thread_id=thread_id, label_id=mine.id, applied_at=NOW
            )
        )
        session.commit()

    response = client.get(
        "/api/emails/labels", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200, response.text
    by_name = {item["name"]: item for item in response.json()}
    assert "Ajena" not in by_name
    assert by_name["Clientes VIP"]["gmail_label_id"] == "Label_1"
    assert by_name["Clientes VIP"]["thread_count"] == 1
    assert by_name["Mía"]["gmail_label_id"] is None
    assert by_name["Mía"]["thread_count"] == 1


# ---------------------------------------------------------------------------
# POST/DELETE /api/emails/messages/{id}/labels/{label_id}
# ---------------------------------------------------------------------------


def test_add_message_label_propagates_and_persists(
    client: TestClient,
    factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch)
    with factory() as session:
        uid = _uid(session)
        label_id = _seed_gmail_label(session)
        thread_id = _seed_thread(session, uid=uid, gid="t1", messages=[{}])
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.thread_id == thread_id)
        )
        msg_id = msg.id
        session.commit()

    response = client.post(
        f"/api/emails/messages/{msg_id}/labels/{label_id}",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Clientes VIP"
    assert _FakeLabelsClient.modify_calls == [
        {"message_id": "t1-m0", "add": ["Label_1"], "remove": None}
    ]
    with factory() as session:
        assert session.get(EmailMessageLabel, (msg_id, label_id)) is not None
        msg = session.get(EmailMessage, msg_id)
        assert "Label_1" in json.loads(msg.gmail_labels or "[]")

    # Idempotente: repetir no duplica ni vuelve a llamar a Gmail.
    again = client.post(
        f"/api/emails/messages/{msg_id}/labels/{label_id}",
        headers=auth_headers(client, "user"),
    )
    assert again.status_code == 200
    assert len(_FakeLabelsClient.modify_calls) == 1


def test_remove_message_label_propagates(
    client: TestClient,
    factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch)
    with factory() as session:
        uid = _uid(session)
        label_id = _seed_gmail_label(session)
        thread_id = _seed_thread(
            session,
            uid=uid,
            gid="t1",
            messages=[{"gmail_labels": ["INBOX", "Label_1"]}],
        )
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.thread_id == thread_id)
        )
        msg_id = msg.id
        session.add(
            EmailMessageLabel(
                message_id=msg_id, label_id=label_id, applied_at=NOW
            )
        )
        session.commit()

    response = client.delete(
        f"/api/emails/messages/{msg_id}/labels/{label_id}",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 204, response.text
    assert _FakeLabelsClient.modify_calls == [
        {"message_id": "t1-m0", "add": None, "remove": ["Label_1"]}
    ]
    with factory() as session:
        assert session.get(EmailMessageLabel, (msg_id, label_id)) is None
        msg = session.get(EmailMessage, msg_id)
        assert "Label_1" not in json.loads(msg.gmail_labels or "[]")


def test_add_message_label_rejects_personal_label(
    client: TestClient, factory: sessionmaker
) -> None:
    with factory() as session:
        uid = _uid(session)
        personal = EmailLabel(user_id=uid, name="Mía")
        session.add(personal)
        session.flush()
        personal_id = personal.id
        thread_id = _seed_thread(session, uid=uid, gid="t1", messages=[{}])
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.thread_id == thread_id)
        )
        msg_id = msg.id
        session.commit()

    response = client.post(
        f"/api/emails/messages/{msg_id}/labels/{personal_id}",
        headers=auth_headers(client, "user"),
    )
    # Las personales operan a nivel de hilo — aquí no existen.
    assert response.status_code == 404


def test_message_label_visibility_guard(
    client: TestClient,
    factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un comercial no puede etiquetar mensajes de hilos ajenos (404, y
    sin llamada a Gmail)."""
    _patch_client(monkeypatch)
    with factory() as session:
        admin_id = _uid(session, UserRole.ADMIN)
        label_id = _seed_gmail_label(session)
        # Hilo iniciado por el admin, entregado a un alias que no es del
        # caller.
        thread_id = _seed_thread(
            session,
            uid=admin_id,
            gid="t1",
            messages=[{"delivered_to": "bart@bomedia.net"}],
            initiated_by=admin_id,
        )
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.thread_id == thread_id)
        )
        msg_id = msg.id
        session.commit()

    denied = client.post(
        f"/api/emails/messages/{msg_id}/labels/{label_id}",
        headers=auth_headers(client, "user"),
    )
    assert denied.status_code == 404
    assert _FakeLabelsClient.modify_calls == []

    # El admin sí puede.
    allowed = client.post(
        f"/api/emails/messages/{msg_id}/labels/{label_id}",
        headers=auth_headers(client, "admin"),
    )
    assert allowed.status_code == 200
    assert len(_FakeLabelsClient.modify_calls) == 1


def test_gmail_failure_aborts_without_persisting(
    client: TestClient,
    factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Broken(_FakeLabelsClient):
        def modify_message(self, *_a: Any, **_k: Any) -> dict[str, Any]:
            raise RuntimeError("boom upstream")

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _Broken
    )
    with factory() as session:
        uid = _uid(session)
        label_id = _seed_gmail_label(session)
        thread_id = _seed_thread(session, uid=uid, gid="t1", messages=[{}])
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.thread_id == thread_id)
        )
        msg_id = msg.id
        session.commit()

    response = client.post(
        f"/api/emails/messages/{msg_id}/labels/{label_id}",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 502
    with factory() as session:
        assert session.get(EmailMessageLabel, (msg_id, label_id)) is None


# ---------------------------------------------------------------------------
# Filtro label_id a nivel de mensaje + detail expone labels
# ---------------------------------------------------------------------------


def test_list_threads_filters_by_message_level_label(
    client: TestClient, factory: sessionmaker
) -> None:
    with factory() as session:
        uid = _uid(session)
        label_id = _seed_gmail_label(session)
        labeled = _seed_thread(session, uid=uid, gid="t1", messages=[{}])
        _seed_thread(session, uid=uid, gid="t2", messages=[{}])
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.thread_id == labeled)
        )
        session.add(
            EmailMessageLabel(
                message_id=msg.id, label_id=label_id, applied_at=NOW
            )
        )
        session.commit()

    response = client.get(
        f"/api/emails/threads?label_id={label_id}",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == labeled


def test_thread_detail_exposes_message_labels(
    client: TestClient, factory: sessionmaker
) -> None:
    with factory() as session:
        uid = _uid(session)
        label_id = _seed_gmail_label(session)
        thread_id = _seed_thread(session, uid=uid, gid="t1", messages=[{}])
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.thread_id == thread_id)
        )
        session.add(
            EmailMessageLabel(
                message_id=msg.id, label_id=label_id, applied_at=NOW
            )
        )
        session.commit()

    response = client.get(
        f"/api/emails/threads/{thread_id}",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    message = response.json()["messages"][0]
    assert [lbl["name"] for lbl in message["labels"]] == ["Clientes VIP"]
    assert message["labels"][0]["gmail_label_id"] == "Label_1"


# ---------------------------------------------------------------------------
# process_history — go-forward (Gmail→CRM)
# ---------------------------------------------------------------------------


def _run_history(
    factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    *,
    owner_id: str,
    history: dict[str, Any],
) -> None:
    _FakeLabelsClient.history = history
    _patch_client(monkeypatch)
    with factory() as session:
        gmail_service.process_history(
            session, user_id=owner_id, new_history_id=99
        )
        session.commit()


def test_process_history_syncs_custom_label_add_and_remove(
    factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    with factory() as session:
        uid = _uid(session)
        label_id = _seed_gmail_label(session)
        thread_id = _seed_thread(session, uid=uid, gid="t1", messages=[{}])
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.thread_id == thread_id)
        )
        msg_id = msg.id
        gmail_mid = msg.gmail_message_id
        _seed_watch(session, uid)
        session.commit()

    _run_history(
        factory,
        monkeypatch,
        owner_id=uid,
        history={
            "history": [
                {
                    "labelsAdded": [
                        {
                            "labelIds": ["Label_1"],
                            "message": {
                                "id": gmail_mid,
                                "labelIds": ["INBOX", "Label_1"],
                            },
                        }
                    ]
                }
            ]
        },
    )
    with factory() as session:
        assert session.get(EmailMessageLabel, (msg_id, label_id)) is not None
        msg = session.get(EmailMessage, msg_id)
        assert "Label_1" in json.loads(msg.gmail_labels or "[]")
        # Re-armar el watch para la segunda pasada.
        watch = session.scalar(select(GmailPubsubWatch))
        watch.history_id = 1
        session.commit()

    _run_history(
        factory,
        monkeypatch,
        owner_id=uid,
        history={
            "history": [
                {
                    "labelsRemoved": [
                        {
                            "labelIds": ["Label_1"],
                            "message": {
                                "id": gmail_mid,
                                "labelIds": ["INBOX"],
                            },
                        }
                    ]
                }
            ]
        },
    )
    with factory() as session:
        assert session.get(EmailMessageLabel, (msg_id, label_id)) is None
        msg = session.get(EmailMessage, msg_id)
        assert "Label_1" not in json.loads(msg.gmail_labels or "[]")


def test_process_history_imports_unknown_label_on_the_fly(
    factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    with factory() as session:
        uid = _uid(session)
        thread_id = _seed_thread(session, uid=uid, gid="t1", messages=[{}])
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.thread_id == thread_id)
        )
        msg_id = msg.id
        gmail_mid = msg.gmail_message_id
        _seed_watch(session, uid)
        session.commit()

    _FakeLabelsClient.labels_by_id = {
        "Label_9": {"id": "Label_9", "name": "Nueva en Gmail", "type": "user"},
    }
    _run_history(
        factory,
        monkeypatch,
        owner_id=uid,
        history={
            "history": [
                {
                    "labelsAdded": [
                        {
                            "labelIds": ["Label_9"],
                            "message": {"id": gmail_mid},
                        }
                    ]
                }
            ]
        },
    )
    with factory() as session:
        label = session.scalar(
            select(EmailLabel).where(EmailLabel.gmail_label_id == "Label_9")
        )
        assert label is not None
        assert label.name == "Nueva en Gmail"
        assert label.user_id is None
        assert (
            session.get(EmailMessageLabel, (msg_id, label.id)) is not None
        )


def test_persist_message_applies_known_custom_labels(
    factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un mail nuevo que llega con una label ya importada materializa el
    mapeo al persistirse (messagesAdded del push)."""
    import base64

    with factory() as session:
        uid = _uid(session)
        label_id = _seed_gmail_label(session)
        _seed_watch(session, uid)
        session.commit()

    raw = {
        "id": "nuevo1",
        "threadId": "tnew",
        "snippet": "hola",
        "labelIds": ["INBOX", "Label_1"],
        "payload": {
            "headers": [
                {"name": "From", "value": "desconocido@fuera.com"},
                {"name": "To", "value": ALIAS},
                {"name": "Subject", "value": "Consulta"},
                {"name": "Date", "value": "Fri, 31 Dec 2100 23:59:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"cuerpo").decode()},
        },
    }

    class _FakeWithMessage(_FakeLabelsClient):
        history = {
            "history": [
                {
                    "messagesAdded": [
                        {
                            "message": {
                                "id": "nuevo1",
                                "threadId": "tnew",
                                "labelIds": ["INBOX", "Label_1"],
                            }
                        }
                    ]
                }
            ]
        }

        def get_message(self, _mid: str) -> dict[str, Any]:
            return raw

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeWithMessage
    )
    with factory() as session:
        imported = gmail_service.process_history(
            session, user_id=uid, new_history_id=99
        )
        session.commit()
        assert imported == 1

    with factory() as session:
        msg = session.scalar(
            select(EmailMessage).where(
                EmailMessage.gmail_message_id == "nuevo1"
            )
        )
        assert msg is not None
        assert (
            session.get(EmailMessageLabel, (msg.id, label_id)) is not None
        )
