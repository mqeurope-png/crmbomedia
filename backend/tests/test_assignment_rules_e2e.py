"""Sprint Reglas-Assign — PR-F (cierre) tests E2E.

Cubre el flujo completo del motor de auto-asignación a través de los
3 triggers de creación de contactos (manual + Brevo + Agile), más los
invariantes documentados en `docs/reglas-assign-multi-comercial.md`.

Mocks mínimos: el upsert Brevo y el upsert Agile se llaman
directamente con un payload sintético — sin Redis, sin HTTP. El motor
ve el contacto recién creado y dispara según las reglas activas; el
test inspecciona BD + audit log para verificar el efecto.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    AssignmentRule,
    AuditLog,
    Base,
    Contact,
    ContactAssignment,
    ExternalReference,
    ExternalSystem,
    User,
    UserRole,
)
from app.models.integration_settings import IntegrationAccount
from app.repositories import assignments as assignments_repo
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
        # Integration accounts: brevo:default y agilecrm:default.
        seed.add_all(
            [
                IntegrationAccount(
                    system=ExternalSystem.BREVO,
                    account_id="default",
                    display_name="Brevo main",
                    enabled=True,
                    credential_status="configured",
                ),
                IntegrationAccount(
                    system=ExternalSystem.AGILECRM,
                    account_id="default",
                    display_name="Agile main",
                    enabled=True,
                    credential_status="configured",
                ),
            ]
        )
        seed.commit()
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


def _seed_rule(
    session: Session,
    *,
    name: str,
    conditions: dict[str, Any],
    primary_user_id: str,
    creator_id: str,
    priority: int = 100,
    apply_to: str = "unassigned_only",
    stop_on_match: bool = True,
    override_existing: bool = False,
    secondaries: list[str] | None = None,
) -> AssignmentRule:
    rule = AssignmentRule(
        name=name,
        conditions_json=json.dumps(conditions),
        primary_user_id=primary_user_id,
        secondary_user_ids_json=json.dumps(secondaries) if secondaries else None,
        priority=priority,
        apply_to=apply_to,
        stop_on_match=stop_on_match,
        override_existing=override_existing,
        created_by_user_id=creator_id,
    )
    session.add(rule)
    session.flush()
    return rule


# ===================================================================
# Trigger: manual via POST /api/contacts
# ===================================================================


def test_manual_create_dispatches_rule_and_audits(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """POST /api/contacts con condiciones que matchean → contacto
    queda asignado al primary del rule, owner_user_id cacheado, audit
    `assignment_rule.applied` escrito."""
    target_uid = _user_id(session_factory, UserRole.USER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        rule = _seed_rule(
            session,
            name="Catalan VIPs",
            conditions={
                "operator": "AND",
                "children": [
                    {
                        "type": "rule",
                        "field": "address_country",
                        "comparator": "eq",
                        "value": "ES",
                    }
                ],
            },
            primary_user_id=target_uid,
            creator_id=creator_uid,
        )
        session.commit()
        rule_id = rule.id

    resp = client.post(
        "/api/contacts",
        headers=auth_headers(client, "admin"),
        json={
            "first_name": "Lead",
            "email": "lead@es.com",
            "address_country": "ES",
        },
    )
    assert resp.status_code in (200, 201), resp.text
    contact_id = resp.json()["id"]

    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_id
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].user_id == target_uid
        assert rows[0].is_primary is True
        assert rows[0].source == f"rule:{rule_id}"
        assert session.get(Contact, contact_id).owner_user_id == target_uid

        # Audit: assignment_rule.applied con target_id=contact_id.
        applied_rows = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "assignment_rule.applied",
                    AuditLog.target_id == contact_id,
                )
            )
        )
        assert len(applied_rows) == 1
        meta = json.loads(applied_rows[0].metadata_json or "{}")
        assert meta["rule_id"] == rule_id
        assert meta["rule_name"] == "Catalan VIPs"


def test_manual_create_no_rule_no_assignment(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Sin reglas activas que matcheen → contacto queda sin asignar
    (paridad con `unattended-leads` widget)."""
    resp = client.post(
        "/api/contacts",
        headers=auth_headers(client, "admin"),
        json={"first_name": "Sin", "email": "sin@x.com"},
    )
    assert resp.status_code in (200, 201)
    cid = resp.json()["id"]
    with session_factory() as session:
        assert (
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == cid
                )
            ).first()
            is None
        )


# ===================================================================
# Trigger: Brevo upsert (created vs updated)
# ===================================================================


def _make_brevo_payload(email: str) -> dict[str, Any]:
    """Minimal Brevo /contacts payload — el mapper sólo usa los
    campos que aparecen en el ContactRecord."""
    return {
        "id": 12345,
        "email": email,
        "emailBlacklisted": False,
        "smsBlacklisted": False,
        "attributes": {
            "FIRSTNAME": "Brevo",
            "LASTNAME": "Lead",
            "SMS": "+34111222333",
        },
        "listIds": [],
        "createdAt": "2026-01-01T10:00:00Z",
        "modifiedAt": "2026-01-02T10:00:00Z",
    }


def test_brevo_create_dispatches_rule(
    session_factory: sessionmaker,
) -> None:
    """Una rama "created" del upsert Brevo dispara el motor →
    contacto recién creado por Brevo queda asignado."""
    from app.integrations.brevo.jobs import upsert_brevo_contact  # noqa: PLC0415

    target_uid = _user_id(session_factory, UserRole.USER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        _seed_rule(
            session,
            name="any new",
            conditions={"operator": "AND", "children": []},
            primary_user_id=target_uid,
            creator_id=creator_uid,
            apply_to="all_matching",
        )
        session.commit()

    with session_factory() as session:
        action, contact_id = upsert_brevo_contact(
            session,
            account_id="default",
            payload=_make_brevo_payload("brevo-lead@example.com"),
        )
        session.commit()
        assert action == "created"

    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_id
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].user_id == target_uid
        assert rows[0].source.startswith("rule:")


def test_brevo_update_does_not_dispatch_rule(
    session_factory: sessionmaker,
) -> None:
    """El segundo upsert del MISMO email → action="updated" → el
    motor NO debe dispararse. fire-on-create only (decisión §2 spec)."""
    from app.integrations.brevo.jobs import upsert_brevo_contact  # noqa: PLC0415

    target_uid = _user_id(session_factory, UserRole.USER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        _seed_rule(
            session,
            name="any new",
            conditions={"operator": "AND", "children": []},
            primary_user_id=target_uid,
            creator_id=creator_uid,
            apply_to="all_matching",
        )
        # Contacto YA existente. NO le ponemos assignment.
        existing = Contact(
            first_name="Bart",
            email="brevo-lead@example.com",
            tags="",
            commercial_status="new",
        )
        session.add(existing)
        session.flush()
        # External reference para que Brevo matchee por id.
        session.add(
            ExternalReference(
                system=ExternalSystem.BREVO,
                account_id="default",
                external_id="12345",
                contact_id=existing.id,
            )
        )
        session.commit()
        existing_id = existing.id

    with session_factory() as session:
        action, contact_id = upsert_brevo_contact(
            session,
            account_id="default",
            payload=_make_brevo_payload("brevo-lead@example.com"),
        )
        session.commit()
        assert action == "updated"
        assert contact_id == existing_id

    with session_factory() as session:
        assignments_after = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == existing_id
                )
            )
        )
        # 0 assignments — la regla NO disparó porque fue update.
        assert assignments_after == []
        applied_rows = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "assignment_rule.applied",
                    AuditLog.target_id == existing_id,
                )
            )
        )
        assert applied_rows == []


# ===================================================================
# Trigger: Agile upsert (created vs updated)
# ===================================================================


def _make_agile_payload(email: str) -> dict[str, Any]:
    return {
        "id": 67890,
        "type": "PERSON",
        "properties": [
            {"name": "first_name", "value": "Agile"},
            {"name": "last_name", "value": "Lead"},
            {"name": "email", "type": "SYSTEM", "value": email, "subtype": "work"},
        ],
        "tags": [],
        "lead_score": 0,
        "created_time": 1700000000000,
        "updated_time": 1700000100000,
    }


def test_agile_create_dispatches_rule(
    session_factory: sessionmaker,
) -> None:
    from app.integrations.agilecrm.jobs import _upsert_contact_for_payload  # noqa: PLC0415

    target_uid = _user_id(session_factory, UserRole.USER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        _seed_rule(
            session,
            name="any new",
            conditions={"operator": "AND", "children": []},
            primary_user_id=target_uid,
            creator_id=creator_uid,
            apply_to="all_matching",
        )
        session.commit()

    with session_factory() as session:
        action, _consol, contact_id, _ext = _upsert_contact_for_payload(
            session,
            account_id="default",
            payload=_make_agile_payload("agile-lead@example.com"),
        )
        session.commit()
        assert action == "created"

    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_id
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].user_id == target_uid
        assert rows[0].source.startswith("rule:")


def test_agile_update_does_not_dispatch_rule(
    session_factory: sessionmaker,
) -> None:
    from app.integrations.agilecrm.jobs import _upsert_contact_for_payload  # noqa: PLC0415

    target_uid = _user_id(session_factory, UserRole.USER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        _seed_rule(
            session,
            name="any new",
            conditions={"operator": "AND", "children": []},
            primary_user_id=target_uid,
            creator_id=creator_uid,
            apply_to="all_matching",
        )
        existing = Contact(
            first_name="Bart",
            email="agile-lead@example.com",
            tags="",
            commercial_status="new",
        )
        session.add(existing)
        session.flush()
        session.add(
            ExternalReference(
                system=ExternalSystem.AGILECRM,
                account_id="default",
                external_id="67890",
                contact_id=existing.id,
            )
        )
        session.commit()
        existing_id = existing.id

    with session_factory() as session:
        action, _consol, contact_id, _ext = _upsert_contact_for_payload(
            session,
            account_id="default",
            payload=_make_agile_payload("agile-lead@example.com"),
        )
        session.commit()
        assert action == "updated"
        assert contact_id == existing_id

    with session_factory() as session:
        assert (
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == existing_id
                )
            ).first()
            is None
        )


# ===================================================================
# Stop on match (prioridad cascade)
# ===================================================================


def test_stop_on_match_blocks_lower_priority_rule(
    session_factory: sessionmaker,
) -> None:
    """2 reglas activas: la de prioridad 10 (mayor prioridad) matchea
    con stop_on_match=True → la regla 20 NO se evalúa aunque también
    matchearía."""
    target_a = _user_id(session_factory, UserRole.USER)
    target_b = _user_id(session_factory, UserRole.MANAGER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        _seed_rule(
            session,
            name="prio10 → user",
            priority=10,
            stop_on_match=True,
            apply_to="all_matching",
            conditions={"operator": "AND", "children": []},
            primary_user_id=target_a,
            creator_id=creator_uid,
        )
        _seed_rule(
            session,
            name="prio20 → manager",
            priority=20,
            apply_to="all_matching",
            conditions={"operator": "AND", "children": []},
            primary_user_id=target_b,
            creator_id=creator_uid,
        )
        contact = Contact(first_name="Y", email="y@y.com")
        session.add(contact)
        session.flush()
        result = evaluate_for_contact(session, contact)
        cid = contact.id
        session.commit()
    assert len(result.applied) == 1
    assert result.applied[0].primary_user_id == target_a

    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == cid
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].user_id == target_a


# ===================================================================
# Override existing
# ===================================================================


def test_override_false_skips_already_assigned_contacts(
    session_factory: sessionmaker,
) -> None:
    """override_existing=False (default) + apply_to=unassigned_only
    → contactos ya asignados manualmente NO son tocados por el motor."""
    target_uid = _user_id(session_factory, UserRole.USER)
    existing_uid = _user_id(session_factory, UserRole.MANAGER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        contact = Contact(first_name="X", email="x@x.com")
        session.add(contact)
        session.flush()
        assignments_repo.add_assignment(
            session,
            contact_id=contact.id,
            user_id=existing_uid,
            is_primary=True,
        )
        _seed_rule(
            session,
            name="block",
            conditions={"operator": "AND", "children": []},
            primary_user_id=target_uid,
            creator_id=creator_uid,
            apply_to="unassigned_only",
            override_existing=False,
        )
        result = evaluate_for_contact(session, contact)
        cid = contact.id
        session.commit()
    assert result.applied == []
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == cid
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].user_id == existing_uid


def test_override_true_promotes_new_primary(
    session_factory: sessionmaker,
) -> None:
    """override_existing=True + apply_to=all_matching → la regla aplica
    aunque ya haya asignación, promoviendo el target de la regla a
    primary y degradando al anterior primary."""
    target_uid = _user_id(session_factory, UserRole.USER)
    existing_uid = _user_id(session_factory, UserRole.MANAGER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        contact = Contact(first_name="X", email="x@x.com")
        session.add(contact)
        session.flush()
        assignments_repo.add_assignment(
            session,
            contact_id=contact.id,
            user_id=existing_uid,
            is_primary=True,
        )
        _seed_rule(
            session,
            name="override",
            conditions={"operator": "AND", "children": []},
            primary_user_id=target_uid,
            creator_id=creator_uid,
            apply_to="all_matching",
            override_existing=True,
        )
        result = evaluate_for_contact(session, contact)
        cid = contact.id
        session.commit()
    assert len(result.applied) == 1
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment)
                .where(ContactAssignment.contact_id == cid)
                .order_by(ContactAssignment.is_primary.desc())
            )
        )
        primaries = [r for r in rows if r.is_primary]
        assert len(primaries) == 1
        assert primaries[0].user_id == target_uid
        # owner_user_id cache reflejado.
        assert session.get(Contact, cid).owner_user_id == target_uid


# ===================================================================
# Auto-disable cuando primary queda inactivo
# ===================================================================


def test_rule_with_inactive_primary_auto_disables_on_evaluate(
    session_factory: sessionmaker,
) -> None:
    """evaluate_for_contact detecta primary inactivo → desactiva la
    regla y no aplica nada. La UI mostrará la regla en gris."""
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    inactive_uid = _user_id(session_factory, UserRole.VIEWER)
    with session_factory() as session:
        # Desactivamos el usuario destino.
        u = session.get(User, inactive_uid)
        u.is_active = False
        rule = _seed_rule(
            session,
            name="bad target",
            conditions={"operator": "AND", "children": []},
            primary_user_id=inactive_uid,
            creator_id=creator_uid,
            apply_to="all_matching",
        )
        contact = Contact(first_name="Z", email="z@z.com")
        session.add(contact)
        session.flush()
        result = evaluate_for_contact(session, contact)
        rule_id = rule.id
        contact_id = contact.id
        session.commit()
    assert rule_id in result.auto_disabled

    with session_factory() as session:
        rule_after = session.get(AssignmentRule, rule_id)
        assert rule_after.is_active is False
        assert (
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_id
                )
            ).first()
            is None
        )
