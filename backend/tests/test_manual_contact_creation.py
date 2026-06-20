"""PR-Fix-Creación-Manual-Contacto tests.

Verifica que:

1. POST /api/contacts asigna automáticamente al user logueado como
   primary owner (`contact_assignments` row + `owner_user_id` cache).
2. POST /api/contacts fija `origin="Manual"` server-side, ignorando
   cualquier `origin` que llegue del cliente.
3. El sync (Agile/Brevo) NO pasa por esta lógica (su path es el
   upsert directo al ORM en jobs.py, sin endpoint).
4. Una rule con `override_existing=True` puede reasignar al
   creator-default; una con `apply_to=unassigned_only` (default) NO.
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    AssignmentRule,
    Base,
    Contact,
    ContactAssignment,
    ExternalSystem,
    User,
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


def _user_id(factory: sessionmaker, role: UserRole) -> str:
    with factory() as session:
        return session.scalar(select(User.id).where(User.role == role))


# ---------------------------------------------------------------------
# 1. Auto-asignación al creador
# ---------------------------------------------------------------------


def test_manual_contact_creation_assigns_owner_to_creator(
    client: TestClient, session_factory: sessionmaker
):
    """El admin logueado crea un contacto → queda con owner_user_id =
    admin.id + una fila primary en contact_assignments con
    source='manual_creator'."""
    admin_id = _user_id(session_factory, UserRole.ADMIN)
    headers = auth_headers(client, "admin")

    response = client.post(
        "/api/contacts",
        json={"first_name": "Lead", "email": "lead@example.com"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    contact_id = response.json()["id"]

    with session_factory() as session:
        contact = session.get(Contact, contact_id)
        assert contact.owner_user_id == admin_id
        assignment = session.scalar(
            select(ContactAssignment).where(
                ContactAssignment.contact_id == contact_id
            )
        )
        assert assignment is not None
        assert assignment.user_id == admin_id
        assert assignment.is_primary is True
        assert assignment.source == "manual_creator"
        assert assignment.assigned_by_user_id == admin_id


def test_different_users_create_contacts_with_different_owners(
    client: TestClient, session_factory: sessionmaker
):
    """Cada user logueado queda como owner del contacto que él crea."""
    manager_id = _user_id(session_factory, UserRole.MANAGER)
    user_id = _user_id(session_factory, UserRole.USER)

    manager_headers = auth_headers(client, "manager")
    user_headers = auth_headers(client, "user")

    r1 = client.post(
        "/api/contacts",
        json={"first_name": "A", "email": "a@example.com"},
        headers=manager_headers,
    )
    r2 = client.post(
        "/api/contacts",
        json={"first_name": "B", "email": "b@example.com"},
        headers=user_headers,
    )
    assert r1.status_code == 201
    assert r2.status_code == 201

    with session_factory() as session:
        c1 = session.get(Contact, r1.json()["id"])
        c2 = session.get(Contact, r2.json()["id"])
        assert c1.owner_user_id == manager_id
        assert c2.owner_user_id == user_id


# ---------------------------------------------------------------------
# 2. origin forzado a "Manual"
# ---------------------------------------------------------------------


def test_manual_contact_creation_sets_origin_to_Manual(
    client: TestClient, session_factory: sessionmaker
):
    """Sin pasar `origin` en el payload, el contacto queda con
    origin='Manual' y origin_account_id=None."""
    headers = auth_headers(client, "admin")
    response = client.post(
        "/api/contacts",
        json={"first_name": "Auto", "email": "auto@example.com"},
        headers=headers,
    )
    assert response.status_code == 201
    contact_id = response.json()["id"]

    with session_factory() as session:
        contact = session.get(Contact, contact_id)
        assert contact.origin == "Manual"
        assert contact.origin_account_id is None


def test_manual_contact_creation_ignores_origin_payload_from_frontend(
    client: TestClient, session_factory: sessionmaker
):
    """Aunque un cliente legacy / external mande `origin='Tel'` o
    `origin='agilecrm:default'`, el backend lo ignora y graba
    'Manual'. La defensa server-side evita ensuciar filtros con
    texto libre."""
    headers = auth_headers(client, "admin")
    response = client.post(
        "/api/contacts",
        json={
            "first_name": "Spoof",
            "email": "spoof@example.com",
            "origin": "agilecrm:fake-account",
        },
        headers=headers,
    )
    assert response.status_code == 201
    contact_id = response.json()["id"]

    with session_factory() as session:
        contact = session.get(Contact, contact_id)
        assert contact.origin == "Manual", (
            "el backend debe sobrescribir el `origin` del payload"
        )
        assert contact.origin_account_id is None


# ---------------------------------------------------------------------
# 3. Sync no pasa por esta lógica
# ---------------------------------------------------------------------


def test_sync_contact_creation_does_not_apply_manual_creator_logic(
    session_factory: sessionmaker,
):
    """Un contacto creado por el sync de Agile no pasa por el endpoint
    POST /api/contacts, así que NO recibe `source='manual_creator'`
    ni `owner_user_id` automático. Esta es la diferencia esencial: el
    sync sigue el flujo del PR #221 (origin_account_id +
    assignment_rules), no el nuevo del PR-Fix-Creación-Manual."""
    from app.integrations.agilecrm.jobs import _upsert_contact_for_payload

    seed_payload = {
        "id": "agile-1",
        "tags": [],
        "properties": [
            {"name": "first_name", "value": "Sync"},
            {"name": "email", "value": "sync@example.com"},
        ],
    }

    with session_factory() as session:
        # _upsert_contact_for_payload necesita account configurada;
        # creamos una mínima para que el helper no falle.
        from app.core import crypto
        from app.models.integration_settings import IntegrationAccount

        session.add(
            IntegrationAccount(
                system=ExternalSystem.AGILECRM,
                account_id="default",
                display_name="Agile default",
                enabled=True,
                credential_status="configured",
                api_key_encrypted=crypto.encrypt("ops@example.com:secret"),
            )
        )
        session.commit()

        action, _, contact_id, _ = _upsert_contact_for_payload(
            session, account_id="default", payload=seed_payload
        )
        session.commit()

        assert action == "created"
        contact = session.get(Contact, contact_id)
        # El sync graba `origin_account_id`, NO "Manual".
        assert contact.origin_account_id == "agilecrm:default"
        # Sin reglas activas, el contacto queda sin owner — comportamiento
        # esperado del flujo de sync (las reglas matchearían después).
        assert contact.owner_user_id is None
        manual_assignments = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_id,
                    ContactAssignment.source == "manual_creator",
                )
            )
        )
        assert manual_assignments == [], (
            "el sync no debe generar manual_creator assignments"
        )


# ---------------------------------------------------------------------
# 4. Rules pueden / no pueden reasignar al creator-default
# ---------------------------------------------------------------------


def test_unassigned_only_rule_does_not_reassign_manual_creator_owner(
    client: TestClient, session_factory: sessionmaker
):
    """Una rule con `apply_to='unassigned_only'` (default histórico)
    NO reasigna porque el creator ya quedó como owner. El comercial
    que dio de alta el lead se queda con él."""
    admin_id = _user_id(session_factory, UserRole.ADMIN)
    manager_id = _user_id(session_factory, UserRole.MANAGER)

    with session_factory() as session:
        rule = AssignmentRule(
            name="reasignar a manager",
            conditions_json=json.dumps({"operator": "AND", "children": []}),
            primary_user_id=manager_id,
            apply_to="unassigned_only",
            stop_on_match=True,
            override_existing=False,
            created_by_user_id=admin_id,
        )
        session.add(rule)
        session.commit()

    headers = auth_headers(client, "admin")
    response = client.post(
        "/api/contacts",
        json={"first_name": "X", "email": "x@example.com"},
        headers=headers,
    )
    assert response.status_code == 201
    contact_id = response.json()["id"]

    with session_factory() as session:
        contact = session.get(Contact, contact_id)
        assert contact.owner_user_id == admin_id, (
            "rule unassigned_only NO debería reasignar; el creator gana"
        )


def test_assignment_rule_override_existing_can_reassign_manual_creator_owner(
    client: TestClient, session_factory: sessionmaker
):
    """Una rule con `override_existing=True` SÍ puede tomar la
    ownership tras el auto-asignación al creador. Es un opt-in
    explícito, por eso el spec lo permite."""
    admin_id = _user_id(session_factory, UserRole.ADMIN)
    manager_id = _user_id(session_factory, UserRole.MANAGER)

    with session_factory() as session:
        rule = AssignmentRule(
            name="override a manager",
            conditions_json=json.dumps({"operator": "AND", "children": []}),
            primary_user_id=manager_id,
            apply_to="unassigned_only",
            stop_on_match=True,
            override_existing=True,
            created_by_user_id=admin_id,
        )
        session.add(rule)
        session.commit()

    headers = auth_headers(client, "admin")
    response = client.post(
        "/api/contacts",
        json={"first_name": "Y", "email": "y@example.com"},
        headers=headers,
    )
    assert response.status_code == 201
    contact_id = response.json()["id"]

    with session_factory() as session:
        contact = session.get(Contact, contact_id)
        assert contact.owner_user_id == manager_id, (
            "override_existing=True debería reasignar al target de la rule"
        )
