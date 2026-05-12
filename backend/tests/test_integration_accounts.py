"""End-to-end CRUD tests for the multi-account integration module."""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.audit import Action
from app.db.session import get_session
from app.main import app
from app.models.crm import Base, Contact, ExternalReference, ExternalSystem
from app.models.integration_settings import IntegrationAccount
from tests._test_helpers import auth_headers, seed_test_users


@dataclass
class Stack:
    client: TestClient
    engine: Engine


def _seed_default_accounts(session: Session) -> None:
    """Mirror the production data preservation step: one row per system
    with `account_id='default'` so the legacy flow still works."""
    for system, name in {
        ExternalSystem.AGILECRM: "AgileCRM",
        ExternalSystem.BREVO: "Brevo",
        ExternalSystem.FRESHDESK: "Freshdesk",
        ExternalSystem.FACTUSOL: "FactuSOL",
    }.items():
        session.add(
            IntegrationAccount(
                system=system,
                account_id="default",
                display_name=name,
            )
        )
    session.commit()


@pytest.fixture()
def stack() -> Generator[Stack, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with factory() as seed:
        seed_test_users(seed)
        _seed_default_accounts(seed)

    def override_session() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield Stack(client=client, engine=engine)
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(stack: Stack) -> TestClient:
    return stack.client


# ---------------------------------------------------------------------------
# Listing + filtering
# ---------------------------------------------------------------------------


def test_list_returns_seeded_default_accounts(client: TestClient):
    headers = auth_headers(client, "manager")
    response = client.get("/api/integration-accounts", headers=headers)
    assert response.status_code == 200
    rows = response.json()
    by_system = {row["system"] for row in rows}
    assert by_system == {"agilecrm", "brevo", "freshdesk", "factusol"}
    assert all(row["account_id"] == "default" for row in rows)
    assert response.headers["x-total-count"] == "4"


def test_filter_by_system(client: TestClient):
    headers = auth_headers(client, "admin")
    client.post(
        "/api/integration-accounts/agilecrm",
        json={"account_id": "es", "display_name": "AgileCRM España"},
        headers=headers,
    )
    response = client.get(
        "/api/integration-accounts",
        params={"system": "agilecrm"},
        headers=headers,
    )
    assert response.status_code == 200
    rows = response.json()
    assert {row["account_id"] for row in rows} == {"default", "es"}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_requires_admin(client: TestClient):
    response = client.post(
        "/api/integration-accounts/agilecrm",
        json={"account_id": "uk", "display_name": "AgileCRM UK"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 403


def test_create_persists_quota_fields(client: TestClient):
    headers = auth_headers(client, "admin")
    response = client.post(
        "/api/integration-accounts/agilecrm",
        json={
            "account_id": "es",
            "display_name": "AgileCRM España",
            "mode": "live",
            "api_base_url": "https://es.agilecrm.com",
            "account_label": "Producción ES",
            "quota_max_contacts": 800,
            "quota_strategy": "keep_newest",
            "sync_priority": 10,
            "notes": "Cuenta principal mercado español",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["system"] == "agilecrm"
    assert body["account_id"] == "es"
    assert body["quota_max_contacts"] == 800
    assert body["quota_strategy"] == "keep_newest"
    assert body["sync_priority"] == 10
    assert body["has_api_key"] is False


def test_create_rejects_duplicate_account_id(client: TestClient):
    headers = auth_headers(client, "admin")
    response = client.post(
        "/api/integration-accounts/agilecrm",
        json={"account_id": "default", "display_name": "Duplicate"},
        headers=headers,
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "raw_id", ["Has Space", "UPPER", "trailing-", "-leading", "weird/slash"]
)
def test_create_rejects_invalid_account_id_format(client: TestClient, raw_id: str):
    headers = auth_headers(client, "admin")
    response = client.post(
        "/api/integration-accounts/agilecrm",
        json={"account_id": raw_id, "display_name": "x"},
        headers=headers,
    )
    assert response.status_code == 422


def test_create_audits_with_system_and_account_id(client: TestClient):
    headers = auth_headers(client, "admin")
    client.post(
        "/api/integration-accounts/freshdesk",
        json={"account_id": "soporte", "display_name": "Freshdesk Soporte"},
        headers=headers,
    )
    audit = client.get(
        "/api/audit-logs",
        params={"action": Action.INTEGRATION_ACCOUNT_CREATED},
        headers=headers,
    )
    assert audit.status_code == 200
    rows = audit.json()
    assert rows
    metadata = rows[0]["metadata"]
    assert metadata["system"] == "freshdesk"
    assert metadata["account_id"] == "soporte"


# ---------------------------------------------------------------------------
# Read / Update
# ---------------------------------------------------------------------------


def test_read_specific_account(client: TestClient):
    headers = auth_headers(client, "manager")
    response = client.get(
        "/api/integration-accounts/brevo/default", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Brevo"


def test_update_fields(client: TestClient):
    headers = auth_headers(client, "admin")
    response = client.patch(
        "/api/integration-accounts/brevo/default",
        json={"display_name": "Brevo Producción", "enabled": True},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Brevo Producción"
    assert body["enabled"] is True


def test_update_audit_includes_changed_fields(client: TestClient):
    headers = auth_headers(client, "admin")
    client.patch(
        "/api/integration-accounts/brevo/default",
        json={"notes": "updated"},
        headers=headers,
    )
    audit = client.get(
        "/api/audit-logs",
        params={"action": Action.INTEGRATION_ACCOUNT_UPDATED},
        headers=headers,
    )
    assert audit.status_code == 200
    rows = audit.json()
    assert rows
    assert "notes" in rows[0]["metadata"]["changed_fields"]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_account_without_references(client: TestClient):
    headers = auth_headers(client, "admin")
    client.post(
        "/api/integration-accounts/freshdesk",
        json={"account_id": "ventas", "display_name": "Freshdesk Ventas"},
        headers=headers,
    )
    delete = client.delete(
        "/api/integration-accounts/freshdesk/ventas", headers=headers
    )
    assert delete.status_code == 200, delete.text
    # Subsequent read returns 404.
    missing = client.get(
        "/api/integration-accounts/freshdesk/ventas", headers=headers
    )
    assert missing.status_code == 404


def test_delete_account_with_references_requires_force(stack: Stack):
    """If there are external_references for the same system, the delete
    refuses to drop the account until the caller passes `?force=true`."""
    headers = auth_headers(stack.client, "admin")
    with Session(stack.engine) as session:
        contact = Contact(first_name="F", email="fake@example.com")
        session.add(contact)
        session.flush()
        session.add(
            ExternalReference(
                system=ExternalSystem.AGILECRM,
                account_id="default",
                external_id="x",
                contact_id=contact.id,
            )
        )
        session.commit()

    response = stack.client.delete(
        "/api/integration-accounts/agilecrm/default", headers=headers
    )
    assert response.status_code == 409, response.text

    forced = stack.client.delete(
        "/api/integration-accounts/agilecrm/default?force=true", headers=headers
    )
    assert forced.status_code == 200, forced.text


def test_delete_requires_admin(client: TestClient):
    response = client.delete(
        "/api/integration-accounts/brevo/default",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Deprecated alias
# ---------------------------------------------------------------------------


def test_legacy_integration_settings_namespace_is_gone(client: TestClient):
    headers = auth_headers(client, "manager")
    for method in ("get", "put", "patch", "delete"):
        response = getattr(client, method)(
            "/api/integration-settings",
            headers=headers,
        )
        assert response.status_code == 410, (method, response.text)
        assert "integration-accounts" in response.text
