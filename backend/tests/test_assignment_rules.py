"""Sprint Reglas-Assign — PR-C tests.

Motor de reglas + CRUD endpoints + auto-disable + fire-on-create hook
en POST /api/contacts.
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
    User,
    UserRole,
)
from app.repositories import assignments as assignments_repo
from app.services.assignment_rules import (
    evaluate_for_contact,
    run_rule_over_universe,
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


def _seed_rule(
    session: Session,
    *,
    name: str,
    conditions: dict,
    primary_user_id: str,
    creator_id: str,
    priority: int = 100,
    apply_to: str = "unassigned_only",
    stop_on_match: bool = True,
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
        created_by_user_id=creator_id,
    )
    session.add(rule)
    session.flush()
    return rule


# -- evaluator ------------------------------------------------------


def test_evaluate_applies_matching_rule_to_unassigned_contact(
    session_factory: sessionmaker,
) -> None:
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
        contact = Contact(
            first_name="Bart",
            email="bart@example.com",
            address_country="ES",
        )
        session.add(contact)
        session.flush()
        result = evaluate_for_contact(session, contact)
        rule_id = rule.id
        contact_id = contact.id
        session.commit()

    assert len(result.applied) == 1
    assert result.applied[0].rule_id == rule_id
    assert result.auto_disabled == []

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


def test_evaluate_skips_unassigned_only_when_contact_already_assigned(
    session_factory: sessionmaker,
) -> None:
    target_uid = _user_id(session_factory, UserRole.USER)
    existing_uid = _user_id(session_factory, UserRole.MANAGER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        contact = Contact(first_name="X", email="x@x.com", address_country="ES")
        session.add(contact)
        session.flush()
        # contacto YA tiene asignación manual.
        assignments_repo.add_assignment(
            session,
            contact_id=contact.id,
            user_id=existing_uid,
            is_primary=True,
        )
        _seed_rule(
            session,
            name="ES rule",
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
        result = evaluate_for_contact(session, contact)
        contact_id = contact.id
        session.commit()
    assert result.applied == []
    with session_factory() as session:
        # Sigue como estaba — solo el primary manual original.
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_id
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].user_id == existing_uid


def test_evaluate_auto_disables_rule_pointing_to_inactive_user(
    session_factory: sessionmaker,
) -> None:
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    inactive_uid = _user_id(session_factory, UserRole.VIEWER)
    with session_factory() as session:
        u = session.get(User, inactive_uid)
        u.is_active = False
        rule = _seed_rule(
            session,
            name="bad rule",
            conditions={"operator": "AND", "children": []},
            primary_user_id=inactive_uid,
            creator_id=creator_uid,
        )
        contact = Contact(first_name="Y", email="y@y.com")
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
        # No assignment created.
        assert (
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_id
                )
            ).first()
            is None
        )


def test_evaluate_respects_stop_on_match(
    session_factory: sessionmaker,
) -> None:
    target_a = _user_id(session_factory, UserRole.USER)
    target_b = _user_id(session_factory, UserRole.MANAGER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        # Priority 10 (más prioritario) matchea + stops.
        _seed_rule(
            session,
            name="ES → user",
            priority=10,
            stop_on_match=True,
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
            primary_user_id=target_a,
            creator_id=creator_uid,
        )
        # Priority 20 también matchearía pero no debería ejecutarse.
        _seed_rule(
            session,
            name="ES → manager (no debería disparar)",
            priority=20,
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
            primary_user_id=target_b,
            creator_id=creator_uid,
        )
        contact = Contact(
            first_name="Z", email="z@z.com", address_country="ES"
        )
        session.add(contact)
        session.flush()
        result = evaluate_for_contact(session, contact)
        contact_id = contact.id
        session.commit()

    assert len(result.applied) == 1
    assert result.applied[0].primary_user_id == target_a
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_id
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].user_id == target_a


# -- run_rule_over_universe -----------------------------------------


def test_run_over_universe_applies_to_all_unassigned_matches(
    session_factory: sessionmaker,
) -> None:
    target_uid = _user_id(session_factory, UserRole.USER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    other_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        rule = _seed_rule(
            session,
            name="ES → user",
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
        # 2 matches unassigned.
        c1 = Contact(first_name="A", email="a@x.com", address_country="ES")
        c2 = Contact(first_name="B", email="b@x.com", address_country="ES")
        # 1 match pero asignado (no debería re-aplicar con unassigned_only).
        c3 = Contact(first_name="C", email="c@x.com", address_country="ES")
        # No match (FR).
        c4 = Contact(first_name="D", email="d@x.com", address_country="FR")
        session.add_all([c1, c2, c3, c4])
        session.flush()
        assignments_repo.add_assignment(
            session, contact_id=c3.id, user_id=other_uid, is_primary=True
        )
        session.commit()

        c1_id, c2_id = c1.id, c2.id
        summary = run_rule_over_universe(
            session, rule=rule, actor_user_id=creator_uid
        )
        session.commit()
        assert summary["matched"] == 2
        assert summary["applied"] == 2

    with session_factory() as session:
        applied_contacts = {
            r.contact_id
            for r in session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.user_id == target_uid
                )
            )
        }
        assert applied_contacts == {c1_id, c2_id}


def test_dry_run_does_not_persist(session_factory: sessionmaker) -> None:
    target_uid = _user_id(session_factory, UserRole.USER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        rule = _seed_rule(
            session,
            name="ES",
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
        contact = Contact(
            first_name="A", email="a@x.com", address_country="ES"
        )
        session.add(contact)
        session.commit()

        summary = run_rule_over_universe(
            session, rule=rule, actor_user_id=creator_uid, dry_run=True
        )
        session.commit()
        assert summary["matched"] == 1
        assert summary["applied"] == 0

    with session_factory() as session:
        assert (
            session.scalars(select(ContactAssignment)).first() is None
        )


# -- CRUD endpoints --------------------------------------------------


def test_crud_round_trip(
    client: TestClient, session_factory: sessionmaker
) -> None:
    uid = _user_id(session_factory, UserRole.USER)
    payload = {
        "name": "Catalan",
        "conditions": {
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
        "primary_user_id": uid,
        "priority": 50,
        "apply_to": "unassigned_only",
    }
    create = client.post(
        "/api/assignment-rules",
        headers=auth_headers(client, "admin"),
        json=payload,
    )
    assert create.status_code == 201, create.text
    rid = create.json()["id"]

    listing = client.get(
        "/api/assignment-rules", headers=auth_headers(client, "user")
    )
    assert listing.status_code == 200
    assert any(r["id"] == rid for r in listing.json())

    update_payload = dict(payload, name="Catalan v2", priority=20)
    update = client.put(
        f"/api/assignment-rules/{rid}",
        headers=auth_headers(client, "admin"),
        json=update_payload,
    )
    assert update.status_code == 200
    assert update.json()["priority"] == 20
    assert update.json()["name"] == "Catalan v2"

    delete = client.delete(
        f"/api/assignment-rules/{rid}",
        headers=auth_headers(client, "admin"),
    )
    assert delete.status_code == 204


def test_create_rejects_invalid_conditions(
    client: TestClient, session_factory: sessionmaker
) -> None:
    uid = _user_id(session_factory, UserRole.USER)
    response = client.post(
        "/api/assignment-rules",
        headers=auth_headers(client, "admin"),
        json={
            "name": "bad",
            "conditions": {
                "operator": "AND",
                "children": [
                    {
                        "type": "rule",
                        "field": "no_such_field",
                        "comparator": "eq",
                        "value": "x",
                    }
                ],
            },
            "primary_user_id": uid,
        },
    )
    assert response.status_code == 400, response.text
    assert "Condiciones inválidas" in response.json()["detail"]


def test_create_rejects_inactive_target(
    client: TestClient, session_factory: sessionmaker
) -> None:
    inactive_uid = _user_id(session_factory, UserRole.VIEWER)
    with session_factory() as session:
        u = session.get(User, inactive_uid)
        u.is_active = False
        session.commit()
    response = client.post(
        "/api/assignment-rules",
        headers=auth_headers(client, "admin"),
        json={
            "name": "x",
            "conditions": {"operator": "AND", "children": []},
            "primary_user_id": inactive_uid,
        },
    )
    assert response.status_code == 400


def test_run_endpoint_applies_and_audits(
    client: TestClient, session_factory: sessionmaker
) -> None:
    uid = _user_id(session_factory, UserRole.USER)
    with session_factory() as session:
        c = Contact(first_name="X", email="x@x.com", address_country="ES")
        session.add(c)
        session.commit()
    create = client.post(
        "/api/assignment-rules",
        headers=auth_headers(client, "admin"),
        json={
            "name": "ES",
            "conditions": {
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
            "primary_user_id": uid,
        },
    )
    rid = create.json()["id"]

    response = client.post(
        f"/api/assignment-rules/{rid}/run",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["matched"] == 1
    assert body["applied"] == 1

    from app.models.crm import AuditLog  # noqa: PLC0415

    with session_factory() as session:
        actions = list(
            session.scalars(
                select(AuditLog.action).where(AuditLog.target_id == rid)
            )
        )
        assert "assignment_rule.created" in actions
        assert "assignment_rule.run" in actions


def test_dry_run_endpoint_does_not_apply(
    client: TestClient, session_factory: sessionmaker
) -> None:
    uid = _user_id(session_factory, UserRole.USER)
    with session_factory() as session:
        c = Contact(first_name="X", email="x@x.com", address_country="ES")
        session.add(c)
        session.commit()
        contact_id = c.id
    create = client.post(
        "/api/assignment-rules",
        headers=auth_headers(client, "admin"),
        json={
            "name": "ES",
            "conditions": {
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
            "primary_user_id": uid,
        },
    )
    rid = create.json()["id"]

    response = client.post(
        f"/api/assignment-rules/{rid}/dry-run",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 1
    assert body["applied"] == 0
    assert body["dry_run"] is True

    with session_factory() as session:
        assert (
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_id
                )
            ).first()
            is None
        )


# -- fire-on-create hook --------------------------------------------


def test_post_contacts_fires_rule(
    client: TestClient, session_factory: sessionmaker
) -> None:
    # PR-Fix-Creación-Manual-Contacto. Tras el cambio, el creador queda
    # como owner por defecto. Para que la rule SIGA tomando ownership
    # como espera este test, la rule debe ser `override_existing=True`
    # (opt-in explícito a force-reassign). Sin override la rule
    # quedaría como secundario y el admin como primary.
    uid = _user_id(session_factory, UserRole.USER)
    create_rule = client.post(
        "/api/assignment-rules",
        headers=auth_headers(client, "admin"),
        json={
            "name": "ES",
            "conditions": {
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
            "primary_user_id": uid,
            "override_existing": True,
        },
    )
    assert create_rule.status_code == 201

    # Crea un contacto vía API que matchea.
    create_contact = client.post(
        "/api/contacts",
        headers=auth_headers(client, "admin"),
        json={
            "first_name": "Hook",
            "email": "hook@example.com",
            "address_country": "ES",
        },
    )
    assert create_contact.status_code in (200, 201), create_contact.text
    contact_id = create_contact.json()["id"]

    with session_factory() as session:
        # La rule con override_existing=True demota al manual_creator
        # y toma la primary. El primary final es el target de la rule.
        primary = session.scalar(
            select(ContactAssignment).where(
                ContactAssignment.contact_id == contact_id,
                ContactAssignment.is_primary.is_(True),
            )
        )
        assert primary is not None
        assert primary.user_id == uid
        assert primary.source.startswith("rule:")


def test_post_contacts_no_rule_only_creator_assignment(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Contacto sin regla activa que matchee → 1 assignment con
    `source='manual_creator'` (el del PR-Fix-Creación-Manual-Contacto),
    cero filas derivadas de reglas."""
    create_contact = client.post(
        "/api/contacts",
        headers=auth_headers(client, "admin"),
        json={
            "first_name": "Sin reglas",
            "email": "norule@example.com",
        },
    )
    assert create_contact.status_code in (200, 201)
    contact_id = create_contact.json()["id"]
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_id
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].source == "manual_creator"


# -- PR-E: preview endpoint + new apply_to options ------------------


def test_preview_endpoint_returns_matched_without_persisting(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """`POST /api/assignment-rules/preview` calcula matches sin
    persistir nada: no aparece la regla en la BD y los contactos
    siguen sin asignación."""
    uid = _user_id(session_factory, UserRole.USER)
    with session_factory() as session:
        c = Contact(first_name="Z", email="z@z.com", address_country="ES")
        session.add(c)
        session.commit()

    payload = {
        "name": "Preview (no save)",
        "conditions": {
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
        "primary_user_id": uid,
        "secondary_user_ids": [],
        "priority": 100,
        "apply_to": "unassigned_only",
        "override_existing": False,
        "stop_on_match": True,
    }
    resp = client.post(
        "/api/assignment-rules/preview",
        headers=auth_headers(client, "admin"),
        json=payload,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matched"] == 1
    assert body["applied"] == 0
    assert body["dry_run"] is True

    with session_factory() as session:
        assert (
            session.scalars(select(AssignmentRule)).first() is None
        )
        assert (
            session.scalars(select(ContactAssignment)).first() is None
        )


def test_preview_rejects_invalid_conditions(
    client: TestClient,
) -> None:
    uid = "0" * 32
    resp = client.post(
        "/api/assignment-rules/preview",
        headers=auth_headers(client, "admin"),
        json={
            "name": "bad",
            "conditions": {
                "operator": "AND",
                "children": [
                    {
                        "type": "rule",
                        "field": "no_such_field",
                        "comparator": "eq",
                        "value": "x",
                    }
                ],
            },
            "primary_user_id": uid,
            "secondary_user_ids": [],
            "priority": 100,
            "apply_to": "unassigned_only",
            "override_existing": False,
            "stop_on_match": True,
        },
    )
    assert resp.status_code == 400, resp.text


def test_apply_to_new_only_filters_by_rule_created_at(
    session_factory: sessionmaker,
) -> None:
    """apply_to=new_only sólo afecta a contactos creados después de
    la creación de la regla. Útil cuando el operador no quiere
    reasignar la cartera existente al introducir la regla."""
    import datetime as _dt  # noqa: PLC0415

    target_uid = _user_id(session_factory, UserRole.USER)
    creator_uid = _user_id(session_factory, UserRole.ADMIN)
    with session_factory() as session:
        old = Contact(
            first_name="Old", email="old@x.com", address_country="ES"
        )
        old.created_at = _dt.datetime(2020, 1, 1, tzinfo=_dt.UTC)
        session.add(old)
        session.flush()
        old_id = old.id

        rule = _seed_rule(
            session,
            name="ES new_only",
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
            apply_to="new_only",
        )
        new = Contact(
            first_name="New", email="new@x.com", address_country="ES"
        )
        session.add(new)
        session.commit()
        new_id = new.id

        summary = run_rule_over_universe(
            session, rule=rule, actor_user_id=creator_uid
        )
        session.commit()
        assert summary["matched"] == 1
        assert summary["applied"] == 1

    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.user_id == target_uid
                )
            )
        )
        assigned = {r.contact_id for r in rows}
        assert assigned == {new_id}
        assert old_id not in assigned
