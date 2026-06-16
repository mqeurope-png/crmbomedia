"""Mini-PR Gmail Templates — backend tests for /api/emails/gmail-templates.

Mocks `app.integrations.gmail.client.GmailClient` to skip the real
Google API. Confirms:

- 200 + parsed payload when Gmail returns drafts with `^smartlabel_
  canned_response`.
- 200 + [] when the user has no Gmail integration (graceful, no
  banner spam).
- 403 when the integration row exists but lacks `gmail.send` scope
  (paridad con /aliases).
"""
from __future__ import annotations

import base64
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

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
        seed.commit()
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


def _user_id(factory: sessionmaker, role: UserRole) -> str:
    with factory() as session:
        return session.scalar(select(User.id).where(User.role == role))


def _seed_gmail(factory: sessionmaker, *, user_id: str, scopes: str) -> None:
    with factory() as session:
        session.add(
            UserGoogleIntegration(
                user_id=user_id,
                google_email="bart@bomedia.net",
                access_token_encrypted=encrypt("access"),
                refresh_token_encrypted=encrypt("refresh"),
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                scopes=scopes,
                connected_at=datetime.now(UTC),
            )
        )
        session.commit()


def _build_raw_email(*, subject: str, body_html: str) -> str:
    """Construye un RFC822 mínimo en formato urlsafe_b64 que `service.
    list_gmail_templates` parsea con email.message_from_bytes."""
    raw = (
        f"Subject: {subject}\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        f"{body_html}"
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


class _FakeGmailClient:
    """Stand-in del cliente Gmail. Modelo:

    - `draft-1` y `draft-2` son templates (label `^smartlabel_canned_
      response` y `^smartlabel_canned_response_template`).
    - `draft-3` es un draft normal, sin label de template — debe ser
      filtrado salvo en modo debug.
    """

    def __init__(self, *_a, **_kw) -> None:
        pass

    def list_draft_templates(
        self, *, query: str | None = None, max_results: int = 30
    ) -> list[dict]:
        _ = query
        _ = max_results
        return [{"id": "draft-1"}, {"id": "draft-2"}, {"id": "draft-3"}]

    def get_draft_metadata(self, draft_id: str) -> dict:
        catalog = {
            "draft-1": {
                "snippet": "Hola equipo,",
                "internalDate": "1718000000000",
                "labelIds": ["DRAFT", "^smartlabel_canned_response"],
                "threadId": "thr-1",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Plantilla de bienvenida"},
                    ]
                },
            },
            "draft-2": {
                "snippet": "Adjunto",
                "internalDate": "1718500000000",
                "labelIds": [
                    "DRAFT",
                    "^smartlabel_canned_response_template",
                ],
                "threadId": "thr-2",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Reenvío plantilla"},
                    ]
                },
            },
            "draft-3": {
                "snippet": "Borrador normal",
                "internalDate": "1719000000000",
                "labelIds": ["DRAFT"],
                "threadId": "thr-3",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Borrador X"}]
                },
            },
        }
        return {"id": draft_id, "message": catalog[draft_id]}

    def get_draft_template(self, draft_id: str) -> dict:
        if draft_id == "draft-1":
            return {
                "id": draft_id,
                "message": {
                    "id": "msg-1",
                    "snippet": "Hola equipo,",
                    "internalDate": "1718000000000",
                    "raw": _build_raw_email(
                        subject="Plantilla de bienvenida",
                        body_html="<p>Bienvenido al CRM</p>",
                    ),
                },
            }
        return {
            "id": draft_id,
            "message": {
                "id": "msg-2",
                "snippet": "Adjunto",
                "internalDate": "1718500000000",
                "raw": _build_raw_email(
                    subject="Reenvío plantilla",
                    body_html="<p>Cuerpo adicional</p>",
                ),
            },
        }


def test_gmail_templates_filters_by_label_ignoring_non_templates(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 drafts: 2 con label de template (legacy + moderno), 1 sin.
    El endpoint debe devolver SOLO los 2 con label de template."""
    uid = _user_id(session_factory, UserRole.USER)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailClient,
    )

    resp = client.get(
        "/api/emails/gmail-templates",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {item["id"] for item in body}
    assert ids == {"draft-1", "draft-2"}, body
    by_id = {item["id"]: item for item in body}
    assert by_id["draft-1"]["subject"] == "Plantilla de bienvenida"
    assert "<p>Bienvenido al CRM</p>" in by_id["draft-1"]["body_html"]
    assert by_id["draft-1"]["snippet"] == "Hola equipo,"
    assert by_id["draft-2"]["subject"] == "Reenvío plantilla"


def test_gmail_templates_debug_returns_all_drafts_with_labels(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modo debug ignora el filtro: TODOS los drafts vuelven con
    `label_ids` + `thread_id` para diagnosticar el patrón real."""
    uid = _user_id(session_factory, UserRole.USER)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailClient,
    )

    resp = client.get(
        "/api/emails/gmail-templates?debug=true",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {item["id"] for item in body}
    assert ids == {"draft-1", "draft-2", "draft-3"}
    by_id = {item["id"]: item for item in body}
    assert by_id["draft-1"]["label_ids"] == [
        "DRAFT",
        "^smartlabel_canned_response",
    ]
    assert by_id["draft-3"]["label_ids"] == ["DRAFT"]
    assert by_id["draft-3"]["thread_id"] == "thr-3"


def test_gmail_templates_returns_empty_when_not_connected(
    client: TestClient,
) -> None:
    """User sin integration row → no banner ni 500: lista vacía."""
    resp = client.get(
        "/api/emails/gmail-templates",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_gmail_templates_403_when_scope_missing(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Integration sin gmail.send → 403 con detalle (mismo patrón que
    /aliases)."""
    uid = _user_id(session_factory, UserRole.USER)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes="https://www.googleapis.com/auth/calendar.events",
    )

    resp = client.get(
        "/api/emails/gmail-templates",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 403
    assert "gmail.send" in resp.json()["detail"]
