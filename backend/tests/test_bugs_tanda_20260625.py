"""PR-Bugs-Tanda-2026-06-25 — backend tests para los bugs críticos.

Cubre los fixes de:

- Bug 2: editar tareas importadas de Agile/Brevo cuando el current
  user es owner del contacto.
- Bug 11: migración de orphan `contacts.phone` → `contact_phones`.
- Bug 12 (regression): PATCH /api/contacts/{id} sin `owner_id` NO
  desasigna al comercial (no hace falta tocar nada del endpoint —
  pero verificamos el invariante).
- Bug 6: el endpoint nuevo POST /api/brevo/campaigns/{id}/refresh-stats
  refresca via Brevo service.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.db.session import get_session
from app.main import app
from app.models.brevo import BrevoCampaignCache
from app.models.crm import (
    Base,
    Contact,
    ContactPhone,
    ExternalSystem,
    Task,
    TaskPriority,
    TaskStatus,
    User,
    UserRole,
)
from app.models.integration_settings import IntegrationAccount, IntegrationMode
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sf() as seed:
        seed_test_users(seed)
        seed.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="main",
                display_name="Brevo main",
                enabled=True,
                mode=IntegrationMode.LIVE,
                api_key_encrypted=crypto.encrypt("dummy"),
            )
        )
        seed.commit()
    yield sf
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _user_id(session, role: str) -> str:
    return session.scalar(
        select(User.id).where(User.role == UserRole(role))
    )


# ---------------------------------------------------------------------------
# Bug 2 — Task edit perms for imported tasks
# ---------------------------------------------------------------------------


def test_task_edit_works_for_user_who_owns_contact_even_if_external_task(
    client, factory
):
    """Tarea importada de Agile/Brevo se queda assigned al admin
    (system user). El comercial USER es el owner del contacto.
    Antes: 403. Ahora: 200, puede cambiar fecha / marcar completada."""
    with factory() as session:
        admin_id = _user_id(session, "admin")
        user_id = _user_id(session, "user")
        contact = Contact(
            id=str(uuid4()),
            first_name="Marny",
            email="marny@x.com",
            owner_user_id=user_id,
            is_active=True,
        )
        session.add(contact)
        task = Task(
            title="Llamar al cliente",
            status=TaskStatus.PENDING,
            priority=TaskPriority.HIGH,
            assigned_user_id=admin_id,
            created_by_user_id=admin_id,
            contact_id=contact.id,
            external_system="agilecrm",
            external_account_id="default",
            external_id="42",
        )
        session.add(task)
        session.commit()
        task_id = task.id

    # User (no admin/manager, no assignee, no creator, pero SÍ owner
    # del contacto) edita la tarea → debe funcionar.
    response = client.patch(
        f"/api/tasks/{task_id}",
        headers=auth_headers(client, "user"),
        json={"status": "done"},
    )
    assert response.status_code == 200, response.text


def test_task_edit_blocked_for_unrelated_user(client, factory):
    """Defensa: un user que NO es admin/manager, ni assignee, ni
    creator, ni owner del contacto, sigue recibiendo 403."""
    with factory() as session:
        admin_id = _user_id(session, "admin")
        manager_id = _user_id(session, "manager")
        # Contact owned by manager
        contact = Contact(
            id=str(uuid4()),
            first_name="X",
            email="x@x.com",
            owner_user_id=manager_id,
            is_active=True,
        )
        session.add(contact)
        task = Task(
            title="task",
            status=TaskStatus.PENDING,
            priority=TaskPriority.MEDIUM,
            assigned_user_id=admin_id,
            created_by_user_id=admin_id,
            contact_id=contact.id,
        )
        session.add(task)
        session.commit()
        task_id = task.id

    # `user` no tiene relación con esta tarea ni con su contacto.
    response = client.patch(
        f"/api/tasks/{task_id}",
        headers=auth_headers(client, "user"),
        json={"status": "done"},
    )
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# Bug 11 — orphan contact.phone → contact_phones backfill
# ---------------------------------------------------------------------------


def test_phones_endpoint_returns_unified_data(client, factory):
    """Bug 11 fix: contacto con teléfono legacy (`contact.phone`)
    debe aparecer también en `GET /api/contacts/{id}/phones` después
    de la migración 0069 (que hace el INSERT one-shot para orphans).

    Aquí simulamos manualmente el INSERT que haría la migración (no
    podemos ejecutar Alembic en SQLite in-memory limpio). El test
    valida el endpoint, no el SQL puro."""
    with factory() as session:
        admin_id = _user_id(session, "admin")
        contact = Contact(
            id=str(uuid4()),
            first_name="LegacyOnly",
            email="legacy@x.com",
            phone="0780156600",
            owner_user_id=admin_id,
            is_active=True,
        )
        session.add(contact)
        session.flush()
        # Replicate the migration INSERT: orphan -> contact_phones.
        session.add(
            ContactPhone(
                contact_id=contact.id,
                label="principal",
                number=contact.phone,
                is_primary=True,
                source="legacy",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
        cid = contact.id

    response = client.get(
        f"/api/contacts/{cid}/phones",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert any(
        r["number"] == "0780156600" and r["is_primary"] is True
        for r in rows
    )


def test_patch_contacts_phones_syncs_legacy_phone(client, factory):
    """Comprobar que PATCH /api/contacts/{id} con `phones` sigue
    sincronizando `contact.phone` desde el primario — invariante
    histórico que Bug 11 NO debe romper."""
    with factory() as session:
        admin_id = _user_id(session, "admin")
        contact = Contact(
            id=str(uuid4()),
            first_name="X",
            email="x@x.com",
            owner_user_id=admin_id,
            is_active=True,
        )
        session.add(contact)
        session.commit()
        cid = contact.id

    response = client.patch(
        f"/api/contacts/{cid}",
        headers=auth_headers(client, "admin"),
        json={
            "phones": [
                {"label": "móvil", "number": "+34 600 111 222", "is_primary": True},
                {"label": "centralita", "number": "+34 900 999 999"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    with factory() as session:
        c = session.get(Contact, cid)
        # Legacy column sigue actualizado al primario.
        assert c.phone == "+34 600 111 222"


# ---------------------------------------------------------------------------
# Bug 12 — owner_id no se pierde si se omite del PATCH
# ---------------------------------------------------------------------------


def test_contact_edit_does_not_unassign_owner_when_owner_id_omitted(
    client, factory
):
    """Bug 12 regression: el modal Editar contacto siempre envía
    `owner_id` con el valor del draft. Si por algún error futuro se
    omitiera del payload, el contacto NO debe quedar sin owner. El
    PATCH solo desasigna si el cliente explícitamente envía
    `owner_id: null`."""
    with factory() as session:
        admin_id = _user_id(session, "admin")
        contact = Contact(
            id=str(uuid4()),
            first_name="X",
            email="x@x.com",
            owner_user_id=admin_id,
            is_active=True,
        )
        session.add(contact)
        session.commit()
        cid = contact.id

    # PATCH sin owner_id — solo cambia first_name. Owner SIGUE.
    response = client.patch(
        f"/api/contacts/{cid}",
        headers=auth_headers(client, "admin"),
        json={"first_name": "Y"},
    )
    assert response.status_code == 200, response.text
    with factory() as session:
        c = session.get(Contact, cid)
        assert c.owner_user_id == admin_id


# ---------------------------------------------------------------------------
# Bug 6 — refresh-stats endpoint
# ---------------------------------------------------------------------------


def test_refresh_campaign_stats_calls_brevo_service(client, factory):
    """POST /api/brevo/campaigns/{id}/refresh-stats fuerza un
    `refresh_campaign_row` sobre la cache de la campaña."""
    with factory() as session:
        row = BrevoCampaignCache(
            brevo_account_id="main",
            brevo_campaign_id=12345,
            name="Test",
            status="sent",
            type="classic",
            cached_at=datetime.now(UTC),
            stats_json=json.dumps({"sent": 0, "delivered": 0}),
        )
        session.add(row)
        session.commit()
        campaign_id = row.id

    # Mockear refresh_campaign_row para que NO llame a Brevo real;
    # solo nos interesa que el endpoint la invoque y commit.
    async def fake_refresh(session, row_):
        row_.stats_json = json.dumps({"sent": 100, "delivered": 95})

    with patch(
        "app.integrations.brevo.campaigns.refresh_campaign_row",
        new=AsyncMock(side_effect=fake_refresh),
    ):
        response = client.post(
            f"/api/brevo/campaigns/{campaign_id}/refresh-stats",
            headers=auth_headers(client, "admin"),
        )
    assert response.status_code == 200, response.text

    with factory() as session:
        row = session.get(BrevoCampaignCache, campaign_id)
        stats = json.loads(row.stats_json)
        assert stats["sent"] == 100
