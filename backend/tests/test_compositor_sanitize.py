"""CRM-COMPOSITOR-V2.2 — sanitize HTML + fallback text/plain + CID assets.

El compositor (TinyMCE, ya rico) manda body_html; el backend lo sanitiza
SIEMPRE en el envío (bleach whitelist), genera el text/plain del
multipart/alternative cuando falta, e incrusta como CID las imágenes
pegadas en el editor (assets de disco) para que el destinatario las vea
aunque bloquee imágenes remotas.
"""
from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.integrations.gmail.service import _swap_asset_urls_to_cid
from app.main import app
from app.models.crm import Base, EmailMessage, UserRole
from app.services.html_sanitizer import html_to_text, sanitize_email_html
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_emails import _seed_gmail_integration, _user_id  # noqa: F401

# ---------------------------------------------------------------------------
# Sanitizador (unit)
# ---------------------------------------------------------------------------


def test_sanitize_removes_scripts() -> None:
    out = sanitize_email_html(
        "<p>hola</p><script>alert(1)</script><p>adiós</p>"
    )
    assert out is not None
    assert "<script" not in out
    assert "alert(1)" not in out
    assert "hola" in out and "adiós" in out


def test_sanitize_removes_onerror_handlers() -> None:
    out = sanitize_email_html(
        '<img src="https://x.com/a.png" onerror="alert(1)" alt="logo">'
    )
    assert out is not None
    assert "onerror" not in out
    assert 'src="https://x.com/a.png"' in out
    assert 'alt="logo"' in out


def test_sanitize_removes_javascript_urls_and_iframes() -> None:
    out = sanitize_email_html(
        '<a href="javascript:alert(1)">click</a>'
        '<iframe src="https://evil.com"></iframe>'
    )
    assert out is not None
    assert "javascript:" not in out
    assert "<iframe" not in out
    assert "click" in out


def test_sanitize_keeps_formatting_and_safe_styles() -> None:
    html = (
        '<h1>Título</h1><ul><li><strong>uno</strong></li></ul>'
        '<p style="color: red; text-align: center;">rojo</p>'
        '<span style="position: fixed; color: blue;">azul</span>'
    )
    out = sanitize_email_html(html)
    assert out is not None
    assert "<h1>" in out and "<strong>" in out and "<li>" in out
    assert "color: red" in out and "text-align: center" in out
    # Propiedad fuera de whitelist eliminada; la permitida sobrevive.
    assert "position" not in out
    assert "color: blue" in out


def test_sanitize_keeps_signature_comment_markers() -> None:
    html = "<!--crmbo:signature--><p>Bart</p><!--/crmbo:signature-->"
    out = sanitize_email_html(html)
    assert out is not None
    assert "<!--crmbo:signature-->" in out


def test_html_to_text_fallback() -> None:
    text = html_to_text(
        "<h1>Hola</h1><p>Un <strong>párrafo</strong> con "
        '<a href="https://x.com">link</a>.</p>'
    )
    assert "Hola" in text
    assert "párrafo" in text
    assert "<" not in text.replace("<https://x.com>", "")


# ---------------------------------------------------------------------------
# Swap de assets del editor → CID (unit)
# ---------------------------------------------------------------------------


def test_swap_asset_urls_to_cid_embeds_and_rewrites() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        data = b"PNGDATA"
        digest = hashlib.sha256(data).hexdigest()
        rel = f"2026/08/{digest}.png"
        (root / "2026" / "08").mkdir(parents=True)
        (root / rel).write_bytes(data)
        html = (
            f'<p>hola</p><img src="https://crm.example.com'
            f'/assets/email-templates/{rel}" width="120">'
        )
        with patch(
            "app.core.config.get_settings",
            return_value=SimpleNamespace(email_assets_dir=str(root)),
        ):
            out, parts = _swap_asset_urls_to_cid(html)
        assert len(parts) == 1
        assert parts[0]["data"] == data
        assert parts[0]["content_type"] == "image/png"
        assert f'src="cid:{parts[0]["cid"]}"' in out
        assert "/assets/email-templates/" not in out


def test_swap_asset_urls_leaves_missing_files_as_remote() -> None:
    with tempfile.TemporaryDirectory() as td:
        html = (
            '<img src="/assets/email-templates/2026/08/'
            + "0" * 64
            + '.png">'
        )
        with patch(
            "app.core.config.get_settings",
            return_value=SimpleNamespace(email_assets_dir=td),
        ):
            out, parts = _swap_asset_urls_to_cid(html)
        assert parts == []
        assert "/assets/email-templates/" in out


# ---------------------------------------------------------------------------
# End-to-end: POST /api/emails/send sanitiza + genera fallback
# ---------------------------------------------------------------------------


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


def test_send_email_sanitizes_html_and_generates_text_fallback(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    captured: dict = {}

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def send_message(self, **kwargs):
            captured.update(kwargs)
            return {"id": "msg-sane-1", "threadId": "thr-sane-1"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["cliente@fuera.com"],
            "subject": "Oferta",
            "body_html": (
                "<p>Hola <strong>Eva</strong></p>"
                "<script>alert('xss')</script>"
                '<img src="https://x.com/a.png" onerror="alert(2)">'
            ),
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text

    # Lo que salió hacia Gmail: sin script ni handler, formato intacto.
    assert "<script" not in captured["body_html"]
    assert "onerror" not in captured["body_html"]
    assert "<strong>Eva</strong>" in captured["body_html"]
    # Fallback text/plain generado desde el HTML (el caller no lo mandó).
    assert captured["body_text"]
    assert "Hola" in captured["body_text"]
    assert "<" not in captured["body_text"]

    # Lo persistido también quedó sanitizado.
    with session_factory() as session:
        msg = session.scalar(select(EmailMessage))
        assert msg is not None
        assert "<script" not in (msg.body_html or "")


def test_send_email_respects_caller_body_text(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el caller manda su propio body_text, no lo pisamos."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    captured: dict = {}

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def send_message(self, **kwargs):
            captured.update(kwargs)
            return {"id": "msg-sane-2", "threadId": "thr-sane-2"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["cliente@fuera.com"],
            "subject": "Oferta",
            "body_html": "<p>Hola</p>",
            "body_text": "TEXTO PROPIO",
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    assert captured["body_text"] == "TEXTO PROPIO"
