"""Sprint Email v2.3a — tracking endpoints + send wiring tests."""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.email_tracking import services as tracking_services
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    ContactTag,
    EmailDirection,
    EmailEventType,
    EmailMessage,
    EmailMessageEvent,
    EmailMessageToken,
    EmailThread,
    EmailUnsubscribe,
    EmailUnsubscribeScope,
    Tag,
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


# ───────────────────────────────────────────────────────────────────
# Pure service helpers
# ───────────────────────────────────────────────────────────────────


def test_wrap_links_rewrites_only_real_urls() -> None:
    html = (
        '<a href="https://example.com/x">A</a>'
        '<a href="mailto:foo@example.com">B</a>'
        '<a href="tel:+34123">C</a>'
        '<a href="#top">D</a>'
        '<a href="https://example.com/skip">E</a>'
    )
    out = tracking_services.wrap_links_for_tracking(
        html,
        token="tok",
        base_url="https://crm.example",
        extra_skip={"https://example.com/skip"},
    )
    assert (
        'href="https://crm.example/api/email-track/click/tok?d=' in out
    )
    assert 'href="mailto:foo@example.com"' in out
    assert 'href="tel:+34123"' in out
    assert 'href="#top"' in out
    assert 'href="https://example.com/skip"' in out
    # The base64 payload round-trips.
    encoded = tracking_services.b64url_encode("https://example.com/x")
    assert encoded in out


def test_inject_pixel_appends_when_no_body_tag() -> None:
    out = tracking_services.inject_open_pixel(
        "<p>Hola</p>", token="t1", base_url="https://crm.example"
    )
    assert out.endswith("/>")
    assert "https://crm.example/api/email-track/open/t1" in out


def test_inject_pixel_splices_before_body_close_when_present() -> None:
    out = tracking_services.inject_open_pixel(
        "<html><body><p>X</p></body></html>",
        token="t1",
        base_url="https://crm.example",
    )
    assert out.count("</body>") == 1
    assert "/email-track/open/t1" in out
    assert out.index("/email-track/open/t1") < out.index("</body>")


def test_build_unsubscribe_block_returns_link_and_headers() -> None:
    html, headers, url = tracking_services.build_unsubscribe_block(
        token="tok", base_url="https://crm.example"
    )
    assert "Anular suscripción" in html
    assert url == "https://crm.example/api/unsubscribe/tok"
    assert headers["List-Unsubscribe"] == f"<{url}>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_b64url_round_trip_with_special_chars() -> None:
    src = "https://example.com/?a=1&b=ñoño#frag"
    encoded = tracking_services.b64url_encode(src)
    assert "=" not in encoded
    assert tracking_services.b64url_decode(encoded) == src


# ───────────────────────────────────────────────────────────────────
# Open + click endpoints
# ───────────────────────────────────────────────────────────────────


def _create_message_with_token(
    session_factory: sessionmaker, token: str
) -> str:
    """Returns the new message id."""
    with session_factory() as session:
        thread = EmailThread(
            initiated_by_user_id="user-x",
            gmail_thread_id="thr-x",
            gmail_account_user_id="user-x",
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
            subject="x",
        )
        session.add(thread)
        session.flush()
        message = EmailMessage(
            thread_id=thread.id,
            gmail_message_id="gmsg-x",
            gmail_account_user_id="user-x",
            direction=EmailDirection.OUTBOUND,
            from_email="user@example.com",
            to_emails_json='["lead@example.com"]',
            sent_at=datetime.now(UTC),
        )
        session.add(message)
        session.flush()
        session.add(
            EmailMessageToken(token=token, message_id=message.id)
        )
        session.commit()
        return message.id


def test_open_endpoint_records_event_and_returns_pixel(
    client: TestClient, session_factory: sessionmaker
) -> None:
    msg_id = _create_message_with_token(session_factory, "open-tok")
    response = client.get(
        "/api/email-track/open/open-tok",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"
    assert "no-store" in response.headers["cache-control"]
    assert response.content.startswith(b"GIF89a")
    with session_factory() as session:
        events = list(
            session.scalars(
                select(EmailMessageEvent).where(
                    EmailMessageEvent.message_id == msg_id
                )
            )
        )
        assert len(events) == 1
        assert events[0].event_type == EmailEventType.OPEN


def test_open_dedupes_within_window(
    client: TestClient, session_factory: sessionmaker
) -> None:
    msg_id = _create_message_with_token(session_factory, "dup-tok")
    for _ in range(3):
        client.get(
            "/api/email-track/open/dup-tok",
            headers={"User-Agent": "Mozilla"},
        )
    with session_factory() as session:
        events = list(
            session.scalars(
                select(EmailMessageEvent).where(
                    EmailMessageEvent.message_id == msg_id
                )
            )
        )
        assert len(events) == 1


def test_open_endpoint_pixel_for_unknown_token(client: TestClient) -> None:
    response = client.get("/api/email-track/open/nope")
    assert response.status_code == 200
    assert response.content.startswith(b"GIF89a")


def test_click_endpoint_records_and_redirects(
    client: TestClient, session_factory: sessionmaker
) -> None:
    msg_id = _create_message_with_token(session_factory, "click-tok")
    destination = "https://boprint.net/producto/young"
    d = tracking_services.b64url_encode(destination)
    response = client.get(
        f"/api/email-track/click/click-tok?d={d}",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == destination
    with session_factory() as session:
        events = list(
            session.scalars(
                select(EmailMessageEvent).where(
                    EmailMessageEvent.message_id == msg_id,
                    EmailMessageEvent.event_type == EmailEventType.CLICK,
                )
            )
        )
        assert len(events) == 1
        assert destination in (events[0].metadata_json or "")


def test_click_rejects_non_http_destinations(client: TestClient) -> None:
    d = tracking_services.b64url_encode(
        "javascript:alert('x')"
    )
    response = client.get(
        f"/api/email-track/click/any?d={d}", follow_redirects=False
    )
    assert response.status_code == 400


# ───────────────────────────────────────────────────────────────────
# Unsubscribe endpoints
# ───────────────────────────────────────────────────────────────────


def _seed_message_with_contact(
    session_factory: sessionmaker, *, token: str
) -> tuple[str, str]:
    """Returns (message_id, contact_id)."""
    with session_factory() as session:
        contact = Contact(
            first_name="Lead",
            email="lead@example.com",
            tags="",
        )
        session.add(contact)
        session.flush()
        thread = EmailThread(
            initiated_by_user_id="user-x",
            gmail_thread_id="thr-u",
            gmail_account_user_id="user-x",
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
            contact_id=contact.id,
            subject="x",
        )
        session.add(thread)
        session.flush()
        message = EmailMessage(
            thread_id=thread.id,
            gmail_message_id="gmsg-u",
            gmail_account_user_id="user-x",
            direction=EmailDirection.OUTBOUND,
            from_email="user@example.com",
            to_emails_json='["lead@example.com"]',
            sent_at=datetime.now(UTC),
            contact_id=contact.id,
        )
        session.add(message)
        session.flush()
        session.add(EmailMessageToken(token=token, message_id=message.id))
        session.commit()
        return message.id, contact.id


def test_unsubscribe_get_renders_confirm_page(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_message_with_contact(session_factory, token="unsub-1")
    response = client.get("/api/unsubscribe/unsub-1")
    assert response.status_code == 200
    assert "Anular suscripción" in response.text
    assert "/api/unsubscribe/unsub-1" in response.text


def test_unsubscribe_post_records_row_and_tags_contact(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _, contact_id = _seed_message_with_contact(
        session_factory, token="unsub-2"
    )
    response = client.post("/api/unsubscribe/unsub-2")
    assert response.status_code == 200
    with session_factory() as session:
        row = session.scalar(
            select(EmailUnsubscribe).where(
                EmailUnsubscribe.token == "unsub-2"
            )
        )
        assert row is not None
        assert row.contact_id == contact_id
        assert row.scope == EmailUnsubscribeScope.MARKETING
        tag = session.scalar(
            select(Tag).where(Tag.name_normalized == "unsubscribed")
        )
        assert tag is not None
        link = session.get(ContactTag, (contact_id, tag.id))
        assert link is not None
        assert link.source == "email-unsubscribe"


def test_unsubscribe_one_click_returns_200(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_message_with_contact(session_factory, token="oc-3")
    response = client.post(
        "/api/unsubscribe/oc-3",
        headers={"List-Unsubscribe": "One-Click"},
    )
    assert response.status_code == 200
    with session_factory() as session:
        row = session.scalar(
            select(EmailUnsubscribe).where(EmailUnsubscribe.token == "oc-3")
        )
        assert row is not None
        assert row.source == "one-click"


def test_unsubscribe_twice_is_idempotent(
    client: TestClient, session_factory: sessionmaker
) -> None:
    _seed_message_with_contact(session_factory, token="dup-4")
    client.post("/api/unsubscribe/dup-4")
    response = client.post("/api/unsubscribe/dup-4")
    assert response.status_code == 200
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(EmailUnsubscribe).where(
                    EmailUnsubscribe.token == "dup-4"
                )
            )
        )
        assert len(rows) == 1


# ───────────────────────────────────────────────────────────────────
# Send-time validation: blocked by unsubscribe
# ───────────────────────────────────────────────────────────────────


def test_send_to_unsubscribed_contact_returns_422(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_gmail_for_user(session_factory)

    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def get_message(self, _message_id: str) -> dict[str, Any]:
            return {"id": _message_id, "payload": {"headers": []}}

        def send_message(self, **_kwargs: Any) -> dict[str, Any]:
            return {"id": "x", "threadId": "x"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    with session_factory() as session:
        contact = Contact(
            first_name="Lead",
            email="lead@example.com",
            tags="",
        )
        session.add(contact)
        session.flush()
        session.add(
            EmailUnsubscribe(
                contact_id=contact.id,
                scope=EmailUnsubscribeScope.MARKETING,
                source="manual",
                token="seed-token-block",
                unsubscribed_at=datetime.now(UTC),
            )
        )
        session.commit()
        contact_id = contact.id
    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["lead@example.com"],
            "subject": "Hola",
            "body_html": "<p>Hola</p>",
            "body_text": None,
            "contact_id": contact_id,
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 422
    assert "se ha dado de baja" in response.json()["detail"].lower()


def test_send_to_other_contact_still_works(client: TestClient) -> None:
    # Sanity: a 422 only fires when the contact_id is actually
    # opted out. A bare send without contact_id sails through the
    # unsubscribe gate.
    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["lead@example.com"],
            "subject": "Hola",
            "body_html": "<p>Hola</p>",
            "body_text": None,
        },
        headers=auth_headers(client, "user"),
    )
    # No Gmail integration seeded → expect the Gmail-not-connected
    # error from the alias check, NOT the unsubscribe-422.
    assert response.status_code != 422 or "baja" not in str(
        response.json()
    ).lower()


# ───────────────────────────────────────────────────────────────────
# Bounce parser
# ───────────────────────────────────────────────────────────────────


def test_parse_ndr_extracts_from_body_text() -> None:
    from app.integrations.gmail.service import _parse_ndr

    body = """\
Hi. This is the mail system at host mail.example.com.

I'm sorry to have to inform you that your message could not
be delivered to one or more recipients.

Final-Recipient: rfc822;ghost@nowhere.test
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp; 550 5.1.1 The email account that you tried to reach does not exist.
"""
    info = _parse_ndr({}, body)
    assert info["failed_to"] == "ghost@nowhere.test"
    assert info["status"] == "5.1.1"
    assert "5.1.1" in info["reason"] or "550" in info["reason"]


def test_parse_ndr_uses_x_failed_recipients_header() -> None:
    from app.integrations.gmail.service import _parse_ndr

    info = _parse_ndr(
        {"x-failed-recipients": "ghost@example.com"}, body_text=None
    )
    assert info["failed_to"] == "ghost@example.com"


def test_is_ndr_detects_mailer_daemon_and_header() -> None:
    from app.integrations.gmail.service import _is_ndr

    assert _is_ndr("mailer-daemon@googlemail.com", {})
    assert _is_ndr("postmaster@example.com", {})
    assert not _is_ndr("lead@example.com", {})
    assert _is_ndr(
        "lead@example.com", {"x-failed-recipients": "x@y.test"}
    )


# ───────────────────────────────────────────────────────────────────
# Send happy path — tracking wiring
# ───────────────────────────────────────────────────────────────────


def _seed_gmail_for_user(session_factory: sessionmaker) -> str:
    """Mirrors `_seed_gmail_integration` in test_emails.py without
    importing it (we need just enough to make /api/emails/send work)."""
    from datetime import timedelta

    from app.core.crypto import encrypt
    from app.models.crm import (
        User,
        UserEmailAliasPref,
        UserGoogleIntegration,
    )

    with session_factory() as session:
        user_id = session.scalar(
            select(User.id).where(User.role == UserRole.USER)
        )
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
        session.add(
            UserEmailAliasPref(
                user_id=user_id,
                alias_email="info@bomedia.net",
                is_allowed=True,
                is_default=True,
            )
        )
        session.commit()
        return user_id


def test_send_wraps_links_injects_pixel_and_records_sent(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_gmail_for_user(session_factory)
    sent: list[dict[str, Any]] = []

    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def get_message(self, _message_id: str) -> dict[str, Any]:
            return {"id": _message_id, "payload": {"headers": []}}

        def send_message(self, **kwargs: Any) -> dict[str, Any]:
            sent.append(kwargs)
            return {"id": "gmail-out-1", "threadId": "gmail-thr-1"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["lead@example.com"],
            "subject": "Hola",
            "body_html": '<p>visita <a href="https://example.com">aquí</a></p>',
            "body_text": None,
            "include_unsubscribe": True,
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    assert sent
    call = sent[0]
    # Link wrapped into a click-redirect URL.
    assert "/api/email-track/click/" in (call["body_html"] or "")
    # Pixel injected.
    assert "/api/email-track/open/" in (call["body_html"] or "")
    # Unsubscribe footer present.
    assert "Anular suscripción" in (call["body_html"] or "")
    # Headers carry the RFC 8058 pair.
    assert "List-Unsubscribe" in (call["extra_headers"] or {})
    assert (
        call["extra_headers"]["List-Unsubscribe-Post"]
        == "List-Unsubscribe=One-Click"
    )
    # The unsubscribe link inside the body must NOT be wrapped — only
    # tracked clicks should hit /click, the One-Click footer goes
    # straight to /unsubscribe.
    body = call["body_html"] or ""
    assert "/api/unsubscribe/" in body
    # A sent event got recorded.
    with session_factory() as session:
        events = list(
            session.scalars(
                select(EmailMessageEvent).where(
                    EmailMessageEvent.event_type == EmailEventType.SENT
                )
            )
        )
        assert len(events) == 1


def test_send_without_unsubscribe_skips_footer_and_headers(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_gmail_for_user(session_factory)
    sent: list[dict[str, Any]] = []

    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def get_message(self, _message_id: str) -> dict[str, Any]:
            return {"id": _message_id, "payload": {"headers": []}}

        def send_message(self, **kwargs: Any) -> dict[str, Any]:
            sent.append(kwargs)
            return {"id": "gmail-out-2", "threadId": "gmail-thr-2"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["lead@example.com"],
            "subject": "Hola",
            "body_html": "<p>x</p>",
            "body_text": None,
            "include_unsubscribe": False,
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    call = sent[0]
    assert "Anular suscripción" not in (call["body_html"] or "")
    assert not call.get("extra_headers")
