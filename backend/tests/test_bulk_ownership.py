"""PR-Bulk-Comerciales — acciones masivas de /contacts abiertas a
comerciales sobre SUS contactos.

Un comercial (`user`) puede ejecutar las acciones masivas (excepto
borrar/desactivar) pero solo se aplican a los contactos cuyo
`owner_user_id` es él; los ajenos se ignoran (`skipped_foreign`).
admin/manager procesan todo.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    AuditLog,
    Base,
    Contact,
    ContactTag,
    ExternalSystem,
    Tag,
    User,
    UserRole,
)
from app.models.integration_settings import IntegrationAccount
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as s:
        seed_test_users(s)
        uid = s.scalar(select(User.id).where(User.role == UserRole.USER))
        mid = s.scalar(select(User.id).where(User.role == UserRole.MANAGER))
        # 2 contactos del comercial + 1 del manager (ajeno para el comercial).
        s.add_all([
            Contact(first_name="Mio1", email="mio1@x.com", owner_user_id=uid),
            Contact(first_name="Mio2", email="mio2@x.com", owner_user_id=uid),
            Contact(first_name="Ajeno", email="ajeno@x.com", owner_user_id=mid),
        ])
        s.add(Tag(name="VIP", name_normalized="vip"))
        s.add(IntegrationAccount(
            system=ExternalSystem.BREVO, account_id="main",
            display_name="Brevo", enabled=True,
        ))
        s.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _ids(sf, *, owner_role: UserRole | None = None) -> list[str]:
    with sf() as s:
        stmt = select(Contact.id)
        if owner_role is not None:
            oid = s.scalar(select(User.id).where(User.role == owner_role))
            stmt = stmt.where(Contact.owner_user_id == oid)
        return list(s.scalars(stmt))


def _all_ids(sf) -> list[str]:
    return _ids(sf)


# --- Fake Brevo client -------------------------------------------------------


class _FakeBrevo:
    add_calls: list[tuple[int, list[str]]] = []
    remove_calls: list[tuple[int, list[str]]] = []

    def __init__(self, *_a, **_k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_e):
        return None

    async def add_contacts_to_list(self, list_id, emails):
        _FakeBrevo.add_calls.append((list_id, list(emails)))
        return {"contacts": {"success": list(emails)}}

    async def remove_contacts_from_list(self, list_id, emails):
        _FakeBrevo.remove_calls.append((list_id, list(emails)))
        return {"contacts": {"success": list(emails)}}


@pytest.fixture(autouse=True)
def _reset_brevo() -> None:
    _FakeBrevo.add_calls = []
    _FakeBrevo.remove_calls = []


# --- Preview -----------------------------------------------------------------


def test_bulk_ownership_preview_returns_correct_counts(client, session_factory):
    ids = _all_ids(session_factory)
    r = client.post("/api/contacts/bulk/ownership-preview",
                    json={"contact_ids": ids},
                    headers=auth_headers(client, "user"))
    assert r.status_code == 200, r.text
    assert r.json() == {"total": 3, "owned_by_me": 2, "foreign": 1}
    # admin ve todo como propio (foreign=0).
    r2 = client.post("/api/contacts/bulk/ownership-preview",
                     json={"contact_ids": ids},
                     headers=auth_headers(client, "admin"))
    assert r2.json() == {"total": 3, "owned_by_me": 3, "foreign": 0}


# --- Tag / lifecycle ---------------------------------------------------------


def test_bulk_tag_commercial_skips_foreign_contacts(client, session_factory):
    ids = _all_ids(session_factory)
    with session_factory() as s:
        tag_id = s.scalar(select(Tag.id))
        foreign_id = _ids(session_factory, owner_role=UserRole.MANAGER)[0]
    r = client.post("/api/contacts/bulk-action", json={
        "contact_ids": ids, "action": "add_tag",
        "payload": {"tag_id": tag_id},
    }, headers=auth_headers(client, "user"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["affected_count"] == 2
    assert body["skipped_foreign"] == 1
    with session_factory() as s:
        tagged = {ct.contact_id for ct in s.scalars(select(ContactTag))}
        assert foreign_id not in tagged
        assert len(tagged) == 2


def test_bulk_lifecycle_commercial_skips_foreign_contacts(
    client, session_factory
):
    ids = _all_ids(session_factory)
    foreign_id = _ids(session_factory, owner_role=UserRole.MANAGER)[0]
    r = client.post("/api/contacts/bulk-action", json={
        "contact_ids": ids, "action": "change_lifecycle",
        "payload": {"lifecycle_status": "qualified"},
    }, headers=auth_headers(client, "user"))
    assert r.status_code == 200, r.text
    assert r.json()["affected_count"] == 2
    with session_factory() as s:
        foreign = s.get(Contact, foreign_id)
        assert foreign.commercial_status != "qualified"


# --- Workflow ----------------------------------------------------------------


def _mk_manual_workflow(s: Session) -> str:
    wf = Workflow(name="wf", trigger_type="contact.manual",
                  status=WorkflowStatus.ACTIVE, trigger_config_json="{}")
    s.add(wf)
    s.flush()
    t = WorkflowStep(workflow_id=wf.id, type="trigger", config_json="{}",
                     is_entry=True)
    e = WorkflowStep(workflow_id=wf.id, type="exit_won", config_json="{}",
                     is_entry=False)
    s.add_all([t, e])
    s.flush()
    s.add(WorkflowEdge(workflow_id=wf.id, from_step_id=t.id,
                       to_step_id=e.id, branch_label="default"))
    s.commit()
    return wf.id


def test_bulk_workflow_commercial_skips_foreign_contacts(
    client, session_factory
):
    ids = _all_ids(session_factory)
    with session_factory() as s:
        wfid = _mk_manual_workflow(s)
        owned = set(_ids(session_factory, owner_role=UserRole.USER))
    r = client.post("/api/contacts/bulk-action", json={
        "contact_ids": ids, "action": "add_to_workflow",
        "payload": {"workflow_id": wfid},
    }, headers=auth_headers(client, "user"))
    assert r.status_code == 200, r.text
    assert r.json()["affected_count"] == 2
    with session_factory() as s:
        run_contacts = {
            run.contact_id
            for run in s.scalars(select(WorkflowRun).where(
                WorkflowRun.workflow_id == wfid))
        }
        assert run_contacts == owned


# --- Owner change ------------------------------------------------------------


def test_bulk_owner_change_commercial_can_reassign_own_contacts(
    client, session_factory
):
    ids = _all_ids(session_factory)
    with session_factory() as s:
        admin_id = s.scalar(select(User.id).where(User.role == UserRole.ADMIN))
    r = client.post("/api/contacts/bulk-action", json={
        "contact_ids": ids, "action": "assign_owner",
        "payload": {"owner_user_id": admin_id},
    }, headers=auth_headers(client, "user"))
    assert r.status_code == 200, r.text
    assert r.json()["affected_count"] == 2  # solo los 2 suyos


def test_bulk_owner_change_commercial_cannot_reassign_foreign(
    client, session_factory
):
    ids = _all_ids(session_factory)
    with session_factory() as s:
        admin_id = s.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        manager_id = s.scalar(
            select(User.id).where(User.role == UserRole.MANAGER))
        foreign_id = _ids(session_factory, owner_role=UserRole.MANAGER)[0]
    client.post("/api/contacts/bulk-action", json={
        "contact_ids": ids, "action": "assign_owner",
        "payload": {"owner_user_id": admin_id},
    }, headers=auth_headers(client, "user"))
    with session_factory() as s:
        # El contacto ajeno sigue siendo del manager, no del admin.
        assert s.get(Contact, foreign_id).owner_user_id == manager_id


# --- Delete / deactivate (bloqueado) ----------------------------------------


def test_bulk_delete_commercial_forbidden(client, session_factory):
    ids = _all_ids(session_factory)
    r = client.post("/api/contacts/bulk-action", json={
        "contact_ids": ids, "action": "deactivate", "payload": {},
    }, headers=auth_headers(client, "user"))
    assert r.status_code == 403


# --- Response + audit --------------------------------------------------------


def test_bulk_response_includes_skipped_foreign_count(client, session_factory):
    ids = _all_ids(session_factory)
    with session_factory() as s:
        tag_id = s.scalar(select(Tag.id))
    r = client.post("/api/contacts/bulk-action", json={
        "contact_ids": ids, "action": "add_tag",
        "payload": {"tag_id": tag_id},
    }, headers=auth_headers(client, "user"))
    body = r.json()
    assert body["skipped_foreign"] == 1
    assert len(body["skipped_ids"]) == 1


def test_bulk_audit_metadata_owner_filtered(client, session_factory):
    ids = _all_ids(session_factory)
    with session_factory() as s:
        tag_id = s.scalar(select(Tag.id))
    client.post("/api/contacts/bulk-action", json={
        "contact_ids": ids, "action": "add_tag",
        "payload": {"tag_id": tag_id},
    }, headers=auth_headers(client, "user"))
    with session_factory() as s:
        rows = [
            json.loads(a.metadata_json or "{}")
            for a in s.scalars(select(AuditLog).where(
                AuditLog.action == "contact_tags.bulk_action"))
        ]
    assert rows, "no audit row emitted"
    meta = rows[-1]
    assert meta["via"] == "bulk"
    assert meta["owner_filtered"] is True
    assert meta["skipped_foreign"] == 1


def test_bulk_audit_metadata_admin_not_owner_filtered(client, session_factory):
    ids = _all_ids(session_factory)
    with session_factory() as s:
        tag_id = s.scalar(select(Tag.id))
    client.post("/api/contacts/bulk-action", json={
        "contact_ids": ids, "action": "add_tag",
        "payload": {"tag_id": tag_id},
    }, headers=auth_headers(client, "admin"))
    with session_factory() as s:
        meta = json.loads(list(s.scalars(select(AuditLog).where(
            AuditLog.action == "contact_tags.bulk_action")))[-1].metadata_json)
    assert meta["owner_filtered"] is False
    assert meta["skipped_foreign"] == 0


# --- Brevo list --------------------------------------------------------------


def _emails_of(sf, ids: list[str]) -> set[str]:
    with sf() as s:
        return {
            c.email.lower()
            for c in s.scalars(select(Contact).where(Contact.id.in_(ids)))
        }


def test_bulk_brevo_list_commercial_filters_own_contacts(
    client, session_factory
):
    ids = _all_ids(session_factory)
    owned_emails = _emails_of(
        session_factory, _ids(session_factory, owner_role=UserRole.USER))
    with patch("app.api.brevo.BrevoClient", _FakeBrevo):
        r = client.post(
            "/api/brevo/lists/7/contacts/add?account_id=main",
            json={"contact_ids": ids},
            headers=auth_headers(client, "user"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sent"] == 2
    assert body["skipped_foreign"] == 1
    # Solo se enviaron a Brevo los emails de los contactos propios.
    sent_emails: set[str] = set()
    for _list_id, emails in _FakeBrevo.add_calls:
        sent_emails.update(emails)
    assert sent_emails == owned_emails


def test_bulk_brevo_list_admin_processes_all_including_foreign(
    client, session_factory
):
    ids = _all_ids(session_factory)
    all_emails = _emails_of(session_factory, ids)
    with patch("app.api.brevo.BrevoClient", _FakeBrevo):
        r = client.post(
            "/api/brevo/lists/7/contacts/add?account_id=main",
            json={"contact_ids": ids},
            headers=auth_headers(client, "admin"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sent"] == 3
    assert body["skipped_foreign"] == 0
    sent_emails: set[str] = set()
    for _list_id, emails in _FakeBrevo.add_calls:
        sent_emails.update(emails)
    assert sent_emails == all_emails
