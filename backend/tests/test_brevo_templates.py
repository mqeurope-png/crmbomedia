"""Brevo templates — cache lifecycle + API CRUD with a faked client."""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.brevo import BrevoTemplateCache
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationAccount
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
    with factory() as session:
        seed_test_users(session)
        session.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="main",
                display_name="Brevo",
                enabled=True,
            )
        )
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class _FakeClient:
    """In-memory Brevo template store."""

    templates: dict[int, dict[str, Any]] = {}
    next_id = 100
    calls: list[tuple[str, Any]] = []

    def __init__(self, session, account_id, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def list_email_templates(self, *, limit=50, offset=0):
        rows = list(_FakeClient.templates.values())[offset : offset + limit]
        return {"templates": rows, "count": len(_FakeClient.templates)}

    async def get_email_template(self, template_id):
        return _FakeClient.templates[template_id]

    async def create_email_template(self, payload):
        tid = _FakeClient.next_id
        _FakeClient.next_id += 1
        _FakeClient.templates[tid] = {
            "id": tid,
            "name": payload["templateName"],
            "subject": payload.get("subject"),
            "isActive": payload.get("isActive", True),
            "tag": payload.get("tag"),
            "sender": payload.get("sender") or {},
            "htmlContent": payload.get("htmlContent"),
        }
        _FakeClient.calls.append(("create", tid))
        return {"id": tid}

    async def update_email_template(self, template_id, payload):
        _FakeClient.calls.append(("update", template_id, payload))
        stored = _FakeClient.templates.setdefault(template_id, {"id": template_id})
        if "templateName" in payload:
            stored["name"] = payload["templateName"]
        if "subject" in payload:
            stored["subject"] = payload["subject"]
        if "htmlContent" in payload:
            stored["htmlContent"] = payload["htmlContent"]

    async def delete_email_template(self, template_id):
        _FakeClient.calls.append(("delete", template_id))
        _FakeClient.templates.pop(template_id, None)

    async def send_test_template(self, template_id, email_to):
        _FakeClient.calls.append(("send_test", template_id, tuple(email_to)))


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeClient.templates = {}
    _FakeClient.next_id = 100
    _FakeClient.calls = []


def _patch_client():
    return patch.multiple(
        "app.integrations.brevo.templates",
        BrevoClient=_FakeClient,
    )


def _patch_api_client():
    return patch("app.api.brevo.BrevoClient", _FakeClient)


def test_create_template_calls_api_and_caches(client: TestClient):
    headers = auth_headers(client, "manager")
    with _patch_api_client():
        response = client.post(
            "/api/brevo/templates",
            json={
                "brevo_account_id": "main",
                "name": "Promo verano",
                "subject": "¡Ofertas!",
                "html_content": "<h1>Hola</h1>",
                "sender_name": "MBO",
                "sender_email": "news@mbolasers.com",
                "tag": "promos",
            },
            headers=headers,
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["brevo_template_id"] == 100
    assert body["name"] == "Promo verano"
    assert ("create", 100) in _FakeClient.calls

    listed = client.get(
        "/api/brevo/templates?account_id=main", headers=headers
    ).json()
    assert len(listed) == 1
    # List view omits the heavy HTML.
    assert listed[0]["html_content"] is None


def test_template_detail_lazy_loads_html(client: TestClient, session_factory):
    headers = auth_headers(client, "manager")
    _FakeClient.templates[55] = {
        "id": 55,
        "name": "Cached",
        "subject": "s",
        "isActive": True,
        "sender": {"name": "X", "email": "x@y.z"},
        "htmlContent": "<p>full body</p>",
    }
    # Seed cache row WITHOUT html (as the list refresh would).
    factory = client.app.dependency_overrides[get_session]
    gen = factory()
    session = next(gen)
    try:
        from datetime import UTC, datetime

        session.add(
            BrevoTemplateCache(
                brevo_account_id="main",
                brevo_template_id=55,
                name="Cached",
                cached_at=datetime.now(UTC),
            )
        )
        session.commit()
        row_id = session.scalar(select(BrevoTemplateCache.id))
    finally:
        gen.close()

    with _patch_client():
        response = client.get(f"/api/brevo/templates/{row_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["html_content"] == "<p>full body</p>"


def test_update_template_updates_both_sides(client: TestClient):
    headers = auth_headers(client, "manager")
    with _patch_api_client():
        created = client.post(
            "/api/brevo/templates",
            json={
                "brevo_account_id": "main",
                "name": "Original",
                "subject": "s",
                "html_content": "<p>x</p>",
                "sender_name": "X",
                "sender_email": "x@y.z",
            },
            headers=headers,
        ).json()
        patched = client.patch(
            f"/api/brevo/templates/{created['id']}",
            json={"name": "Renombrada", "subject": "nuevo"},
            headers=headers,
        )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renombrada"
    update_calls = [c for c in _FakeClient.calls if c[0] == "update"]
    assert update_calls[0][2]["templateName"] == "Renombrada"
    assert _FakeClient.templates[100]["name"] == "Renombrada"


def test_delete_template_removes_both_sides(client: TestClient):
    headers = auth_headers(client, "manager")
    with _patch_api_client():
        created = client.post(
            "/api/brevo/templates",
            json={
                "brevo_account_id": "main",
                "name": "Borrable",
                "subject": "s",
                "html_content": "<p>x</p>",
                "sender_name": "X",
                "sender_email": "x@y.z",
            },
            headers=headers,
        ).json()
        deleted = client.delete(
            f"/api/brevo/templates/{created['id']}", headers=headers
        )
    assert deleted.status_code == 200
    assert 100 not in _FakeClient.templates
    listed = client.get(
        "/api/brevo/templates?account_id=main", headers=headers
    ).json()
    assert listed == []


def test_refresh_pulls_remote_catalogue(client: TestClient):
    headers = auth_headers(client, "manager")
    _FakeClient.templates[200] = {
        "id": 200,
        "name": "Remota",
        "subject": "r",
        "isActive": True,
        "sender": {"name": "X", "email": "x@y.z"},
    }
    with _patch_client():
        listed = client.get(
            "/api/brevo/templates?account_id=main&refresh=true",
            headers=headers,
        ).json()
    assert len(listed) == 1
    assert listed[0]["brevo_template_id"] == 200


def test_send_test_invokes_endpoint(client: TestClient):
    headers = auth_headers(client, "manager")
    with _patch_api_client():
        created = client.post(
            "/api/brevo/templates",
            json={
                "brevo_account_id": "main",
                "name": "T",
                "subject": "s",
                "html_content": "<p>x</p>",
                "sender_name": "X",
                "sender_email": "x@y.z",
            },
            headers=headers,
        ).json()
        response = client.post(
            f"/api/brevo/templates/{created['id']}/send-test",
            json={"emails": ["qa@mbolasers.com"]},
            headers=headers,
        )
    assert response.status_code == 200
    assert ("send_test", 100, ("qa@mbolasers.com",)) in _FakeClient.calls


def test_viewer_cannot_mutate_templates(client: TestClient):
    response = client.post(
        "/api/brevo/templates",
        json={
            "brevo_account_id": "main",
            "name": "X",
            "subject": "s",
            "html_content": "<p>x</p>",
            "sender_name": "X",
            "sender_email": "x@y.z",
        },
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 403
