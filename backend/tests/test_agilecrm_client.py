"""End-to-end tests for `AgileCRMClient` over an httpx MockTransport.

We drive the client through a mock transport so the assertions exercise
the real httpx + tenacity stack (including the parent
`IntegrationHTTPClient` audit + retry logic) without touching the
network.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Generator

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.integrations.agilecrm.client import AgileCRMClient
from app.integrations.errors import IntegrationAuthError
from app.models.crm import Base, ExternalSystem
from app.models.integration_settings import IntegrationAccount


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as session:
        session.add(
            IntegrationAccount(
                system=ExternalSystem.AGILECRM,
                account_id="es",
                display_name="AgileCRM España",
                api_base_url="https://es.agilecrm.example",
                # New canonical form: identifier in plaintext column,
                # API key in encrypted column.
                auth_identifier="ops@example.com",
                api_key_encrypted=crypto.encrypt("secret-key-xyz"),
                credential_status="configured",
            )
        )
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)


def _make_transport(handler):
    return httpx.MockTransport(handler)


def _run_with_transport(session, transport, coro_factory):
    async def _go():
        client = AgileCRMClient(session, "es")
        async with client:
            client._client = httpx.AsyncClient(
                base_url=client.base_url,
                timeout=client.timeout,
                transport=transport,
                headers=client._client.headers if client._client else None,
            )
            return await coro_factory(client)

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_constructor_builds_basic_auth_header(session_factory):
    expected = base64.b64encode(b"ops@example.com:secret-key-xyz").decode("ascii")
    with session_factory() as session:
        client = AgileCRMClient(session, "es")
    assert client._api_key == f"Basic {expected}"
    assert client._email == "ops@example.com"


def test_constructor_accepts_legacy_email_colon_key_with_warning(session_factory):
    """Backwards compatibility: pre-PR-2.1 deploys stored the email
    embedded in the encrypted column. The client still accepts that
    shape, emitting a `DeprecationWarning` so operators see the nudge
    when they tail the worker logs."""
    import warnings as _warnings

    with session_factory() as session:
        account = (
            session.query(IntegrationAccount)
            .filter_by(account_id="es")
            .one()
        )
        account.auth_identifier = None
        account.api_key_encrypted = crypto.encrypt("legacy@example.com:legacy-key")
        session.commit()

    with session_factory() as session:
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            client = AgileCRMClient(session, "es")
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)
        expected = base64.b64encode(b"legacy@example.com:legacy-key").decode("ascii")
        assert client._api_key == f"Basic {expected}"


def test_constructor_rejects_missing_credential(session_factory):
    """No `auth_identifier` AND the encrypted column is just a key
    (no embedded email) → IntegrationAuthError."""
    with session_factory() as session:
        account = (
            session.query(IntegrationAccount)
            .filter_by(account_id="es")
            .one()
        )
        account.auth_identifier = None
        account.api_key_encrypted = crypto.encrypt("just-a-key-no-email")
        session.commit()

    with session_factory() as session:
        with pytest.raises(IntegrationAuthError) as exc_info:
            AgileCRMClient(session, "es")
        assert "auth_identifier" in str(exc_info.value)


# ---------------------------------------------------------------------------
# list_contacts
# ---------------------------------------------------------------------------


def test_outbound_requests_include_accept_application_json(session_factory):
    """AgileCRM defaults to XML; we force `Accept: application/json`
    in the AsyncClient's default headers so every outbound call asks
    for JSON regardless of the per-method kwargs."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["accept"] = request.headers.get("accept", "")
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json=[])

    with session_factory() as session:
        _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.list_contacts(page_size=10),
        )
    assert "application/json" in captured["accept"]


def test_list_contacts_returns_items_and_cursor(session_factory):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        captured["ua"] = request.headers.get("user-agent", "")
        # Return a page that's exactly page_size long so the client
        # infers there's a next page. AgileCRM tags every contact in
        # the response with its own opaque `cursor`; only the last
        # one matters for pagination.
        items = [
            {"id": i, "cursor": f"cur-{i}", "properties": []}
            for i in range(1, 11)
        ]
        return httpx.Response(200, json=items)

    with session_factory() as session:
        items, cursor = _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.list_contacts(page_size=10),
        )

    assert len(items) == 10
    # The next-page token is the opaque `cursor` field carried by the
    # last item, NOT the contact id (AgileCRM 500s if we send the id).
    assert cursor == "cur-10"
    assert "page_size=10" in captured["url"]
    assert captured["auth"].startswith("Basic ")
    assert "CRMBO-Media-CRM" in captured["ua"]


def test_list_contacts_empty_page_returns_no_cursor(session_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with session_factory() as session:
        items, cursor = _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.list_contacts(page_size=50),
        )
    assert items == []
    assert cursor is None


def test_list_contacts_underflow_returns_no_cursor(session_factory):
    """When the page comes back with fewer items than `page_size` the
    cursor must be None — there's no next page."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

    with session_factory() as session:
        items, cursor = _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.list_contacts(page_size=10),
        )
    assert len(items) == 2
    assert cursor is None


def test_list_contacts_full_page_without_cursor_field_returns_none(session_factory):
    """When AgileCRM returns a full page but the last item carries no
    `cursor` field, the dataset is exhausted: there is no next page to
    fetch. The client must NOT fall back to the contact id (that would
    re-trigger the production 500 the previous bug caused)."""

    def handler(request: httpx.Request) -> httpx.Response:
        items = [{"id": i, "properties": []} for i in range(1, 11)]
        return httpx.Response(200, json=items)

    with session_factory() as session:
        items, cursor = _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.list_contacts(page_size=10),
        )
    assert len(items) == 10
    assert cursor is None


def test_list_contacts_ignores_non_string_cursor_field(session_factory):
    """Defensive: if AgileCRM ships back a `cursor` field with a wrong
    type (int, None, empty string), don't treat that as a real cursor."""

    def handler(request: httpx.Request) -> httpx.Response:
        items = [
            {"id": 1, "cursor": "cur-1"},
            {"id": 2, "cursor": ""},  # empty string → falsy
        ]
        return httpx.Response(200, json=items)

    with session_factory() as session:
        items, cursor = _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.list_contacts(page_size=2),
        )
    assert len(items) == 2
    assert cursor is None


def test_list_contacts_omits_cursor_param_on_first_call(session_factory):
    """AgileCRM responds 500 with `IllegalArgumentException: Invalid
    cursor` when the request includes `cursor=` with an empty value.
    `list_contacts(cursor=None)` must not put the parameter in the URL
    at all."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["query"] = request.url.query.decode("utf-8")
        return httpx.Response(200, json=[])

    with session_factory() as session:
        _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.list_contacts(page_size=25, cursor=None),
        )
    assert "cursor" not in captured["query"], (
        f"first-page request leaked an empty cursor: {captured['query']!r}"
    )
    assert "page_size=25" in captured["query"]


def test_list_contacts_includes_cursor_param_when_paginating(session_factory):
    """Once we have a non-empty cursor (next-page token from AgileCRM),
    it must travel in the query string verbatim."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = request.url.query.decode("utf-8")
        return httpx.Response(200, json=[])

    with session_factory() as session:
        _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.list_contacts(page_size=25, cursor="ABC123"),
        )
    assert "cursor=ABC123" in captured["query"]


def test_list_contacts_does_not_leak_order_by_when_unset(session_factory):
    """Same defensive contract for `order_by`: omit when the caller
    didn't pass anything. AgileCRM's parser is fussy about empty values
    in other params too."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = request.url.query.decode("utf-8")
        return httpx.Response(200, json=[])

    with session_factory() as session:
        _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.list_contacts(),
        )
    assert "order_by" not in captured["query"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

    with session_factory() as session:
        items, cursor = _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.list_contacts(page_size=10),
        )
    assert len(items) == 2
    assert cursor is None


def test_unauthorized_marks_credential_error(session_factory):
    """401 must propagate through the parent's auth handling — flips
    `credential_status='error'` and raises `IntegrationAuthError`."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad credentials")

    with session_factory() as session:
        with pytest.raises(IntegrationAuthError):
            _run_with_transport(
                session,
                _make_transport(handler),
                lambda client: client.list_contacts(),
            )

    with session_factory() as session:
        refreshed = (
            session.query(IntegrationAccount)
            .filter_by(account_id="es")
            .one()
        )
        assert refreshed.credential_status == "error"


# ---------------------------------------------------------------------------
# delete_contact / count_contacts
# ---------------------------------------------------------------------------


def test_delete_contact_swallows_404(session_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with session_factory() as session:
        # Must not raise.
        _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.delete_contact("missing-id"),
        )


def test_count_contacts_parses_plain_text_response(session_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="421")

    with session_factory() as session:
        total = _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.count_contacts(),
        )
    assert total == 421


def test_count_contacts_returns_none_on_400(session_factory):
    """AgileCRM's count endpoint is flaky across tenants — when it
    refuses (400 / 404 / other 4xx) we return None so the caller
    decides whether to skip the operation. We must NOT crash the
    surrounding job."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    with session_factory() as session:
        total = _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.count_contacts(),
        )
    assert total is None


def test_count_contacts_returns_none_on_unparseable_text(session_factory):
    """200 OK with a body we can't interpret as an integer also yields
    None (the caller must decide what to do with the soft failure)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="unexpected format")

    with session_factory() as session:
        total = _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.count_contacts(),
        )
    assert total is None


def test_count_contacts_returns_none_on_5xx(session_factory):
    """Persistent 5xx also collapses to None so a saturated AgileCRM
    tenant doesn't take the purge job with it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    with session_factory() as session:
        total = _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.count_contacts(),
        )
    assert total is None
