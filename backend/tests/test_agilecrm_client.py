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
                api_key_encrypted=crypto.encrypt("ops@example.com:secret-key-xyz"),
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


def test_constructor_rejects_credential_without_colon(session_factory):
    with session_factory() as session:
        # Replace the key with a single token (no colon) and reload.
        account = (
            session.query(IntegrationAccount)
            .filter_by(account_id="es")
            .one()
        )
        account.api_key_encrypted = crypto.encrypt("just-a-key-no-email")
        session.commit()

    with session_factory() as session:
        with pytest.raises(IntegrationAuthError):
            AgileCRMClient(session, "es")


# ---------------------------------------------------------------------------
# list_contacts
# ---------------------------------------------------------------------------


def test_list_contacts_returns_items_and_cursor(session_factory):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        captured["ua"] = request.headers.get("user-agent", "")
        # Return a page that's exactly page_size long so the client
        # infers there's a next page.
        items = [{"id": i, "properties": []} for i in range(1, 11)]
        return httpx.Response(200, json=items)

    with session_factory() as session:
        items, cursor = _run_with_transport(
            session,
            _make_transport(handler),
            lambda client: client.list_contacts(page_size=10),
        )

    assert len(items) == 10
    assert cursor == "10"  # Last item's id, stringified
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
