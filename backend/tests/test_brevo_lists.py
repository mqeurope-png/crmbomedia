"""Brevo lists CRUD + contact add/remove endpoints.

Drives the `/api/brevo/lists/*` routes against a fake BrevoClient so
the wire shape (path + JSON) is exercised without hitting the real
API. The tests use the same in-memory SQLite + session-override
pattern as the other route tests in this suite.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.crm import Contact, ExternalSystem
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
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class _FakeClient:
    """Records every call + maintains a tiny in-memory list store so
    the response shapes round-trip realistically."""

    lists: dict[int, dict[str, Any]] = {}
    next_list_id: int = 100
    list_contacts: dict[int, list[dict[str, Any]]] = {}
    add_calls: list[tuple[int, list[str]]] = []
    remove_calls: list[tuple[int, list[str]]] = []
    delete_calls: list[int] = []
    update_calls: list[tuple[int, dict[str, Any]]] = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def list_lists(self, *, limit=50, offset=0):
        rows = list(_FakeClient.lists.values())
        return {"lists": rows[offset : offset + limit], "count": len(rows)}

    async def get_list(self, list_id):
        return _FakeClient.lists.get(int(list_id), {})

    async def create_list(self, name, folder_id=None):
        lid = _FakeClient.next_list_id
        _FakeClient.next_list_id += 1
        _FakeClient.lists[lid] = {
            "id": lid,
            "name": name,
            "folderId": folder_id or 1,
            "totalSubscribers": 0,
            "uniqueSubscribers": 0,
            "totalBlacklisted": 0,
        }
        return _FakeClient.lists[lid]

    async def update_list(self, list_id, *, name=None, folder_id=None):
        _FakeClient.update_calls.append(
            (list_id, {"name": name, "folder_id": folder_id})
        )
        row = _FakeClient.lists.setdefault(list_id, {"id": list_id})
        if name is not None:
            row["name"] = name
        if folder_id is not None:
            row["folderId"] = folder_id

    async def delete_list(self, list_id):
        _FakeClient.delete_calls.append(list_id)
        _FakeClient.lists.pop(list_id, None)

    async def list_list_contacts(self, list_id, *, limit=50, offset=0):
        rows = list(_FakeClient.list_contacts.get(list_id, []))
        return {"contacts": rows[offset : offset + limit], "count": len(rows)}

    async def add_contacts_to_list(self, list_id, emails):
        _FakeClient.add_calls.append((list_id, list(emails)))
        return {"contacts": {"success": list(emails)}}

    async def remove_contacts_from_list(self, list_id, emails):
        _FakeClient.remove_calls.append((list_id, list(emails)))
        return {"contacts": {"success": list(emails)}}


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    _FakeClient.lists = {}
    _FakeClient.next_list_id = 100
    _FakeClient.list_contacts = {}
    _FakeClient.add_calls = []
    _FakeClient.remove_calls = []
    _FakeClient.delete_calls = []
    _FakeClient.update_calls = []


class _patch_api:
    """Patch the BrevoClient ref the lists routes reach."""

    def __enter__(self):
        self.p = patch("app.api.brevo.BrevoClient", _FakeClient)
        self.p.__enter__()
        return self

    def __exit__(self, *exc):
        self.p.__exit__(*exc)


def _seed_contact(client: TestClient, email: str, **overrides) -> dict:
    payload = {
        "first_name": email.split("@")[0].title(),
        "email": email,
        **overrides,
    }
    response = client.post(
        "/api/contacts", json=payload, headers=auth_headers(client, "manager")
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


def test_list_brevo_lists_filters_by_q_substring(client: TestClient):
    """PR-Cg: el endpoint añade `q` server-side. Sin él, el cliente
    cargaba toda la lista y filtraba en cliente cortando alfabéticamente.
    Pin que el subset devuelto matchea solo los nombres que contienen
    `q` (case-insensitive)."""
    _FakeClient.lists = {
        1: {"id": 1, "name": "fespa-2024", "totalSubscribers": 10},
        2: {"id": 2, "name": "mbo-leads", "totalSubscribers": 20},
        3: {"id": 3, "name": "FESPA-warm", "totalSubscribers": 5},
        4: {"id": 4, "name": "artisjet", "totalSubscribers": 1},
    }
    with _patch_api():
        response = client.get(
            "/api/brevo/lists?account_id=main&q=fespa",
            headers=auth_headers(client, "user"),
        )
    assert response.status_code == 200, response.text
    names = sorted(row["name"] for row in response.json())
    assert names == ["FESPA-warm", "fespa-2024"]


def test_list_brevo_lists_caps_with_limit(client: TestClient):
    """PR-Cg: `limit` recorta el listado devuelto. Convención del
    picker: 100 sin q, 50 con q. Endpoint cap a 200."""
    _FakeClient.lists = {
        i: {"id": i, "name": f"lst-{i:03d}", "totalSubscribers": 0}
        for i in range(1, 50)
    }
    with _patch_api():
        response = client.get(
            "/api/brevo/lists?account_id=main&limit=10",
            headers=auth_headers(client, "user"),
        )
    assert response.status_code == 200
    assert len(response.json()) == 10


def test_get_list_detail_surfaces_counters(client: TestClient):
    _FakeClient.lists[42] = {
        "id": 42,
        "name": "Newsletter",
        "totalSubscribers": 1200,
        "uniqueSubscribers": 1180,
        "totalBlacklisted": 20,
        "folderId": 3,
    }
    with _patch_api():
        response = client.get(
            "/api/brevo/lists/42?account_id=main",
            headers=auth_headers(client, "user"),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == 42
    assert body["name"] == "Newsletter"
    assert body["total_subscribers"] == 1200
    assert body["unique_subscribers"] == 1180
    assert body["total_blacklisted"] == 20
    assert body["folder_id"] == 3


def test_get_list_detail_404_when_brevo_returns_empty(client: TestClient):
    with _patch_api():
        response = client.get(
            "/api/brevo/lists/999?account_id=main",
            headers=auth_headers(client, "user"),
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Create / update / delete
# ---------------------------------------------------------------------------


def test_create_list_requires_manager(client: TestClient):
    with _patch_api():
        forbidden = client.post(
            "/api/brevo/lists?account_id=main",
            json={"name": "Boletín"},
            headers=auth_headers(client, "user"),
        )
    assert forbidden.status_code == 403


def test_create_list_returns_detail_with_counters(client: TestClient):
    with _patch_api():
        response = client.post(
            "/api/brevo/lists?account_id=main",
            json={"name": "Boletín", "folder_id": 1},
            headers=auth_headers(client, "manager"),
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Boletín"
    assert body["folder_id"] == 1
    assert body["id"] == 100  # first id from the fake's sequence


def test_update_list_rejects_empty_body(client: TestClient):
    with _patch_api():
        response = client.patch(
            "/api/brevo/lists/42?account_id=main",
            json={},
            headers=auth_headers(client, "manager"),
        )
    assert response.status_code == 400


def test_update_list_renames_and_refolds(client: TestClient):
    _FakeClient.lists[42] = {"id": 42, "name": "Old"}
    with _patch_api():
        body = client.patch(
            "/api/brevo/lists/42?account_id=main",
            json={"name": "Nuevo", "folder_id": 9},
            headers=auth_headers(client, "manager"),
        ).json()
    assert body["name"] == "Nuevo"
    assert body["folder_id"] == 9
    assert _FakeClient.update_calls == [
        (42, {"name": "Nuevo", "folder_id": 9})
    ]


def test_delete_list_returns_204(client: TestClient):
    _FakeClient.lists[42] = {"id": 42, "name": "Vacía"}
    with _patch_api():
        response = client.delete(
            "/api/brevo/lists/42?account_id=main",
            headers=auth_headers(client, "manager"),
        )
    assert response.status_code == 204
    assert _FakeClient.delete_calls == [42]


# ---------------------------------------------------------------------------
# /lists/{id}/contacts — Brevo subscriber list mapped to CRM contacts
# ---------------------------------------------------------------------------


def test_list_contacts_maps_known_emails_to_crm_contacts(client: TestClient):
    """Brevo returns subscriber emails; the route resolves each to a
    CRM contact when one exists (case-insensitive). Unknown emails
    surface with `contact_known=False` so the UI can flag them."""
    ana = _seed_contact(client, "ana@example.com")
    _FakeClient.list_contacts[7] = [
        {"email": "Ana@Example.com"},  # case-different on purpose
        {"email": "stranger@example.com"},
    ]
    with _patch_api():
        body = client.get(
            "/api/brevo/lists/7/contacts?account_id=main",
            headers=auth_headers(client, "user"),
        ).json()
    assert body["total"] == 2
    by_email = {item["email"]: item for item in body["items"]}
    assert by_email["Ana@Example.com"]["contact_id"] == ana["id"]
    assert by_email["Ana@Example.com"]["contact_known"] is True
    assert by_email["stranger@example.com"]["contact_id"] is None
    assert by_email["stranger@example.com"]["contact_known"] is False


# ---------------------------------------------------------------------------
# add / remove contacts — emails + contact_ids both supported
# ---------------------------------------------------------------------------


def test_add_contacts_resolves_contact_ids_to_emails(client: TestClient):
    """Pass `contact_ids` and the route resolves them to emails before
    calling Brevo. Mixing emails + contact_ids de-dupes the result."""
    ana = _seed_contact(client, "ana@example.com")
    boris = _seed_contact(client, "boris@example.com")
    with _patch_api():
        body = client.post(
            "/api/brevo/lists/7/contacts/add?account_id=main",
            json={
                "emails": ["ana@example.com"],
                "contact_ids": [ana["id"], boris["id"]],
            },
            headers=auth_headers(client, "manager"),
        ).json()
    assert body["sent"] == 2  # de-duped
    sent_emails = set(_FakeClient.add_calls[0][1])
    assert sent_emails == {"ana@example.com", "boris@example.com"}


def test_add_contacts_counts_unknown_contact_ids_and_missing_emails(
    client: TestClient,
):
    """Unknown contact_ids and email-less contacts are counted as
    skipped instead of crashing the call."""
    no_email = _seed_contact(client, "stub@example.com")
    # Strip the email post-creation to model the "imported without
    # email" edge case.
    session_factory = app.dependency_overrides[get_session]
    session_gen = session_factory()
    session = next(session_gen)
    try:
        contact = session.get(Contact, no_email["id"])
        contact.email = None
        session.commit()
    finally:
        session_gen.close()

    with _patch_api():
        body = client.post(
            "/api/brevo/lists/7/contacts/add?account_id=main",
            json={
                "contact_ids": [no_email["id"], "00000000-0000-0000-0000-000000000000"],
            },
            headers=auth_headers(client, "manager"),
        ).json()
    assert body["sent"] == 0
    assert body["skipped_missing_email"] == 1
    assert body["skipped_unknown_contact"] == 1
    assert _FakeClient.add_calls == []  # nothing reached Brevo


def test_remove_contacts_strips_and_lowercases_inputs(client: TestClient):
    with _patch_api():
        client.post(
            "/api/brevo/lists/7/contacts/remove?account_id=main",
            json={"emails": ["  Ana@Example.com  ", "ana@example.com"]},
            headers=auth_headers(client, "manager"),
        )
    # Single email reaches Brevo (de-dup + lowercase) — no duplicates.
    assert _FakeClient.remove_calls == [(7, ["ana@example.com"])]


def test_mutation_requires_manager(client: TestClient):
    with _patch_api():
        forbidden = client.post(
            "/api/brevo/lists/7/contacts/add?account_id=main",
            json={"emails": ["a@b.c"]},
            headers=auth_headers(client, "user"),
        )
    assert forbidden.status_code == 403
