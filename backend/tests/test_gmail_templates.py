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
    """Stand-in. Modelo:

    - `draft-tpl1`: template puro ("Pressupost sol·licitat"), pasa
      la heurística.
    - `draft-tpl2`: template ("Adquisición equipos FLUX"), también pasa.
    - `draft-reply`: borrador de respuesta con subject "Re: Tu
      consulta" — NO debería contar como template.
    - `draft-quoted`: borrador con snippet quoted "On Mon, Jun 16
      Bart wrote:" — NO debería contar.
    - `draft-gt`: snippet empieza con `> texto citado` — NO debería
      contar.

    Todos los drafts vienen con `labelIds=["DRAFT"]` para reflejar el
    hallazgo: la API NO expone qué es template.
    """

    def __init__(self, *_a, **_kw) -> None:
        pass

    def list_draft_templates(
        self, *, query: str | None = None, max_results: int = 30
    ) -> list[dict]:
        _ = query
        _ = max_results
        return [
            {"id": "draft-tpl1"},
            {"id": "draft-tpl2"},
            {"id": "draft-reply"},
            {"id": "draft-quoted"},
            {"id": "draft-gt"},
        ]

    def get_draft_metadata(self, draft_id: str) -> dict:
        catalog = {
            "draft-tpl1": {
                "snippet": "Hola, adjuntamos el presupuesto solicitado",
                "internalDate": "1718500000000",  # más reciente
                "labelIds": ["DRAFT"],
                "threadId": "thr-1",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Pressupost sol·licitat"},
                    ]
                },
            },
            "draft-tpl2": {
                "snippet": "Te confirmo la adquisición de equipos",
                "internalDate": "1718000000000",  # más antiguo
                "labelIds": ["DRAFT"],
                "threadId": "thr-2",
                "payload": {
                    "headers": [
                        {
                            "name": "Subject",
                            "value": "Adquisición equipos FLUX",
                        },
                    ]
                },
            },
            "draft-reply": {
                "snippet": "Gracias por la información",
                "internalDate": "1719000000000",
                "labelIds": ["DRAFT"],
                "threadId": "thr-3",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Re: Tu consulta"}]
                },
            },
            "draft-quoted": {
                "snippet": "On Mon, Jun 16 2026 at 10:00, Bart wrote: hola",
                "internalDate": "1719500000000",
                "labelIds": ["DRAFT"],
                "threadId": "thr-4",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Borrador con quote"}
                    ]
                },
            },
            "draft-gt": {
                "snippet": "> Texto citado por completo",
                "internalDate": "1720000000000",
                "labelIds": ["DRAFT"],
                "threadId": "thr-5",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Reply gt"}]
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


def test_heuristic_keeps_templates_drops_replies_and_quoted(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5 drafts:
    - 2 templates puros → quedan.
    - 1 con subject 'Re: …' → fuera.
    - 1 con snippet 'On … wrote:' → fuera.
    - 1 con snippet '> citado' → fuera.

    Resultado: solo los 2 templates, ordenados por updated_at DESC."""
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
    ids = [item["id"] for item in body]
    # Solo los 2 templates, en orden updated_at desc (tpl1 más reciente).
    assert ids == ["draft-tpl1", "draft-tpl2"], body
    assert body[0]["subject"] == "Pressupost sol·licitat"
    assert body[1]["subject"] == "Adquisición equipos FLUX"


def test_debug_returns_all_drafts_with_summary_and_is_template_flag(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modo debug devuelve TODOS los drafts marcados con
    `is_template` boolean + entrada `_summary` con counters."""
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
    summary = body[0]
    assert summary["id"] == "_summary"
    assert summary["total_drafts"] == 5
    assert summary["detected_templates"] == 2

    drafts = body[1:]
    by_id = {item["id"]: item for item in drafts}
    assert by_id["draft-tpl1"]["is_template"] is True
    assert by_id["draft-tpl2"]["is_template"] is True
    assert by_id["draft-reply"]["is_template"] is False
    assert by_id["draft-quoted"]["is_template"] is False
    assert by_id["draft-gt"]["is_template"] is False
    # label_ids siempre ["DRAFT"] (refleja el hallazgo Bart).
    assert by_id["draft-tpl1"]["label_ids"] == ["DRAFT"]


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
