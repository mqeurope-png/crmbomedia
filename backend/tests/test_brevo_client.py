"""Brevo HTTP client — endpoint shapes + error mapping.

Driven through `httpx.MockTransport` like the AgileCRM client tests:
the real httpx + tenacity stack runs, only the wire is mocked.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Generator

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.integrations.brevo.client import BrevoClient
from app.integrations.errors import (
    IntegrationAuthError,
    IntegrationDuplicateError,
)
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
                system=ExternalSystem.BREVO,
                account_id="main",
                display_name="Brevo principal",
                enabled=True,
                api_key_encrypted=crypto.encrypt("xkeysib-test"),
            )
        )
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)


async def _client_with(session, transport: httpx.MockTransport) -> BrevoClient:
    client = BrevoClient(session, "main", max_retries=2)
    await client.__aenter__()
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        headers={"api-key": "xkeysib-test", **{}},
        timeout=client.timeout,
        transport=transport,
    )
    return client


def _run(session, transport, coro_factory):
    async def _go():
        client = await _client_with(session, transport)
        try:
            return await coro_factory(client)
        finally:
            await client.__aexit__(None, None, None)

    return asyncio.run(_go())


def test_api_key_header_is_set_without_bearer(session_factory):
    """Brevo wants `api-key: <key>` — no `Authorization: Bearer`."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["api-key"] = request.headers.get("api-key")
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"contacts": [], "count": 0})

    with session_factory() as session:
        # Build the client normally (no transport swap) to verify the
        # real header wiring, using MockTransport at the httpx level.
        async def _go():
            client = BrevoClient(session, "main", max_retries=1)
            async with client:
                assert client._client is not None
                client._client = httpx.AsyncClient(
                    base_url=client.base_url,
                    headers=dict(client._client.headers),
                    timeout=client.timeout,
                    transport=httpx.MockTransport(handler),
                )
                return await client.list_contacts()

        result = asyncio.run(_go())
    assert result == {"contacts": [], "count": 0}
    assert captured["api-key"] == "xkeysib-test"
    assert captured["authorization"] is None


def test_list_contacts_passes_modified_since(session_factory):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200, json={"contacts": [{"id": 1}], "count": 1}
        )

    with session_factory() as session:
        result = _run(
            session,
            httpx.MockTransport(handler),
            lambda c: c.list_contacts(
                limit=10, offset=20, modified_since="2026-06-01T00:00:00+00:00"
            ),
        )
    assert result["count"] == 1
    assert seen["params"]["limit"] == "10"
    assert seen["params"]["offset"] == "20"
    assert seen["params"]["modifiedSince"] == "2026-06-01T00:00:00+00:00"


def test_create_contact_duplicate_raises_dedicated_error(session_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "code": "duplicate_parameter",
                "message": "Contact already exist",
            },
        )

    with session_factory() as session:
        with pytest.raises(IntegrationDuplicateError):
            _run(
                session,
                httpx.MockTransport(handler),
                lambda c: c.create_contact({"email": "ana@example.com"}),
            )


def test_401_raises_auth_error(session_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Key not found"})

    with session_factory() as session:
        with pytest.raises(IntegrationAuthError):
            _run(
                session,
                httpx.MockTransport(handler),
                lambda c: c.get_contact("ana@example.com"),
            )


def test_429_retries_then_succeeds(session_factory):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, headers={"Retry-After": "0"}, json={"message": "slow down"}
            )
        return httpx.Response(200, json={"contacts": [], "count": 0})

    with session_factory() as session:
        result = _run(
            session,
            httpx.MockTransport(handler),
            lambda c: c.list_contacts(),
        )
    assert result["count"] == 0
    assert calls["n"] == 2


def test_add_contacts_to_list_posts_emails(session_factory):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(201, json={"contacts": {"success": ["a@b.c"]}})

    with session_factory() as session:
        _run(
            session,
            httpx.MockTransport(handler),
            lambda c: c.add_contacts_to_list(7, ["a@b.c"]),
        )
    assert seen["path"].endswith("/contacts/lists/7/contacts/add")
    assert seen["body"] == {"emails": ["a@b.c"]}


def test_create_list_resolves_default_folder(session_factory):
    seen: dict = {"posts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/contacts/folders"):
            return httpx.Response(
                200, json={"folders": [{"id": 3, "name": "Default"}], "count": 1}
            )
        seen["posts"].append(json.loads(request.content.decode()))
        return httpx.Response(201, json={"id": 99})

    with session_factory() as session:
        result = _run(
            session,
            httpx.MockTransport(handler),
            lambda c: c.create_list("crm-campaign-x"),
        )
    assert result == {"id": 99}
    assert seen["posts"] == [{"name": "crm-campaign-x", "folderId": 3}]


def test_template_and_campaign_endpoints_hit_expected_paths(session_factory):
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(f"{request.method} {request.url.path}")
        body = {"templates": [], "campaigns": [], "count": 0, "id": 5, "senders": []}
        return httpx.Response(200, json=body)

    with session_factory() as session:
        async def _all(c: BrevoClient):
            await c.list_email_templates()
            await c.get_email_template(5)
            await c.create_email_template({"templateName": "T"})
            await c.update_email_template(5, {"subject": "s"})
            await c.delete_email_template(5)
            await c.send_test_template(5, ["x@y.z"])
            await c.list_email_campaigns(status="draft")
            await c.create_email_campaign({"name": "C"})
            await c.send_email_campaign_now(9)
            await c.send_test_email_campaign(9, ["x@y.z"])
            await c.schedule_email_campaign(9, "2026-07-01T10:00:00+02:00")
            await c.get_campaign_recipients_stats(9, "opened")
            await c.list_senders()

        _run(session, httpx.MockTransport(handler), _all)

    assert "GET /v3/smtp/templates" in paths[0]
    assert "GET /v3/smtp/templates/5" in paths[1]
    assert "POST /v3/smtp/templates" in paths[2]
    assert "PUT /v3/smtp/templates/5" in paths[3]
    assert "DELETE /v3/smtp/templates/5" in paths[4]
    assert "POST /v3/smtp/templates/5/sendTest" in paths[5]
    assert "GET /v3/emailCampaigns" in paths[6]
    assert "POST /v3/emailCampaigns" in paths[7]
    assert "POST /v3/emailCampaigns/9/sendNow" in paths[8]
    assert "POST /v3/emailCampaigns/9/sendTest" in paths[9]
    assert "PUT /v3/emailCampaigns/9" in paths[10]
    assert "GET /v3/emailCampaigns/9/opened" in paths[11]
    assert "GET /v3/senders" in paths[12]
