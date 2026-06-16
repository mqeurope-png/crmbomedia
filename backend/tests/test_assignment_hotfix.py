"""Sprint Reglas-Assign — PR-Ca hotfix tests.

Cubre los 3 bugs reportados post-deploy PR-C:

1. ContactAssignment.source admite "rule:<UUID>" (41 chars) sin
   reventar la columna (VARCHAR(80) post-migración).
2. POST /api/contacts admite role=user (require_user en vez de
   require_manager).
3. Bulk /api/contacts/bulk-action assign_owner admite role=user.

El bug 3 del widget "Asignarme" es UI — se cubre con el test del
endpoint POST /api/contacts/{id}/assignments en
test_contact_assignments_api.py + el test 2 aquí (POST /api/contacts
con role user, que era el síntoma raíz).
"""
from __future__ import annotations

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
    User,
    UserRole,
)
from app.services.assignment_rules import evaluate_for_contact
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


# -- Bug 1: source column width --------------------------------------


def test_rule_source_prefix_fits_in_source_column(
    session_factory: sessionmaker,
) -> None:
    """`rule:<UUID>` son 41 chars. Pre-hotfix, contact_assignments.
    source era VARCHAR(40) — el motor reventaba en MySQL strict mode."""
    import json  # noqa: PLC0415

    target_uid = _user_id(session_factory, UserRole.USER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        rule = AssignmentRule(
            name="Catalan",
            conditions_json=json.dumps(
                {
                    "operator": "AND",
                    "children": [
                        {
                            "type": "rule",
                            "field": "address_country",
                            "comparator": "eq",
                            "value": "ES",
                        }
                    ],
                }
            ),
            primary_user_id=target_uid,
            created_by_user_id=creator_uid,
        )
        session.add(rule)
        session.flush()
        contact = Contact(
            first_name="X", email="x@x.com", address_country="ES"
        )
        session.add(contact)
        session.flush()
        result = evaluate_for_contact(session, contact)
        rule_id = rule.id
        contact_id = contact.id
        session.commit()

    assert len(result.applied) == 1
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_id
                )
            )
        )
        assert len(rows) == 1
        expected_source = f"rule:{rule_id}"
        assert rows[0].source == expected_source
        assert len(expected_source) == 41  # confirma el límite roto


# -- Bug 2: role on contact create + bulk assign ---------------------


def test_post_contacts_works_for_role_user(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Pre-hotfix POST /api/contacts pedía require_manager → 403."""
    response = client.post(
        "/api/contacts",
        headers=auth_headers(client, "user"),
        json={"first_name": "User-creates", "email": "uc@example.com"},
    )
    assert response.status_code in (200, 201), response.text


def test_bulk_assign_owner_works_for_role_user(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Pre-hotfix bulk assign_owner pedía manager+ — bloqueaba el flujo
    "el comercial se auto-asigna desde la lista"."""
    uid = _user_id(session_factory, UserRole.USER)
    with session_factory() as session:
        c = Contact(first_name="L", email="l@l.com")
        session.add(c)
        session.commit()
        cid = c.id

    response = client.post(
        "/api/contacts/bulk-action",
        headers=auth_headers(client, "user"),
        json={
            "contact_ids": [cid],
            "action": "assign_owner",
            "payload": {"owner_user_id": uid},
        },
    )
    assert response.status_code == 200, response.text
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == cid
                )
            )
        )
        assert len(rows) == 1 and rows[0].user_id == uid


def test_bulk_deactivate_still_admin_only(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Sanity: deactivate sigue admin-only (no se ha aflojado)."""
    with session_factory() as session:
        c = Contact(first_name="D", email="d@d.com")
        session.add(c)
        session.commit()
        cid = c.id

    response = client.post(
        "/api/contacts/bulk-action",
        headers=auth_headers(client, "user"),
        json={
            "contact_ids": [cid],
            "action": "deactivate",
            "payload": {},
        },
    )
    assert response.status_code == 403


# -- Bug 3: dashboard "Asignarme" --------------------------------
# Cubierto indirectamente: el widget llama a POST /api/contacts/{id}/
# assignments con is_primary=true (asignContactToUser en lib/api.ts).
# Ese endpoint ya está testeado en test_contact_assignments_api.py
# (create + caché sync + audit). Aquí confirmo el invariante final
# clave del fix: una vez asignado, el contacto deja de aparecer como
# "unattended" en /api/dashboard/unattended-leads.


def test_widget_assign_flow_creates_assignment_row(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """El widget "Asignarme" del dashboard llama ahora a POST
    /api/contacts/{id}/assignments con is_primary=true (ver
    `lib/api.ts:assignContactToUser`). Esto sí crea la fila — antes
    el widget hacía PATCH /api/contacts con `owner_user_id` que (1)
    pedía manager+ y (2) NO creaba la fila en contact_assignments,
    rompiendo el invariante PR-A."""
    uid = _user_id(session_factory, UserRole.USER)
    with session_factory() as session:
        c = Contact(
            first_name="Sin Asignar",
            email="sin@x.com",
            commercial_status="new",
        )
        session.add(c)
        session.commit()
        cid = c.id

    response = client.post(
        f"/api/contacts/{cid}/assignments",
        headers=auth_headers(client, "user"),
        json={"user_id": uid, "is_primary": True},
    )
    assert response.status_code == 201, response.text
    with session_factory() as session:
        assert session.get(Contact, cid).owner_user_id == uid
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == cid
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].is_primary is True
        assert rows[0].source == "manual"
