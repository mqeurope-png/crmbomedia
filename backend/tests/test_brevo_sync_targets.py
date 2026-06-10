"""Brevo write engine — push targets, membership delta, API CRUD."""
from __future__ import annotations

import json
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
from app.integrations.brevo.sync_targets import run_brevo_target
from app.integrations.errors import IntegrationDuplicateError
from app.main import app
from app.models.brevo import BrevoSyncTarget, BrevoTargetMembership
from app.models.crm import Contact, ExternalSystem, Segment
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
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory) -> Generator[TestClient, None, None]:
    with session_factory() as seed_session:
        seed_test_users(seed_session)

    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_target(
    session: Session,
    *,
    list_id: str | None = "7",
    emails: tuple[str, ...] = ("ana@example.com", "boris@example.com", "carla@example.com"),
    consent: str = "granted",
) -> BrevoSyncTarget:
    session.add(
        IntegrationAccount(
            system=ExternalSystem.BREVO,
            account_id="main",
            display_name="Brevo",
            enabled=True,
        )
    )
    for email in emails:
        session.add(
            Contact(
                first_name=email.split("@")[0].title(),
                email=email,
                marketing_consent=consent,
            )
        )
    # Owner row for the segment FK.
    from app.models.crm import User, UserRole

    owner = User(
        email="owner@example.com",
        full_name="Owner",
        password_hash="x",
        role=UserRole.MANAGER,
        is_active=True,
    )
    session.add(owner)
    session.flush()
    segment = Segment(
        name="Consent OK",
        owner_user_id=owner.id,
        rules_json=json.dumps(
            {
                "type": "rule",
                "field": "marketing_consent",
                "comparator": "eq",
                "value": "granted",
            }
        ),
        is_dynamic=True,
    )
    session.add(segment)
    session.flush()
    target = BrevoSyncTarget(
        brevo_account_id="main",
        name="Push consentidos",
        segment_id=segment.id,
        brevo_list_id=list_id,
    )
    session.add(target)
    session.commit()
    return target


class _FakeClient:
    """Records calls; raises duplicate for emails in `existing`."""

    existing: set[str] = set()
    calls: list[tuple[str, Any]] = []

    def __init__(self, session, account_id, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def create_contact(self, payload):
        if payload["email"] in _FakeClient.existing:
            raise IntegrationDuplicateError("dup", system="brevo")
        _FakeClient.calls.append(("create", payload["email"]))
        return {"id": 1}

    async def update_contact(self, identifier, payload):
        _FakeClient.calls.append(("update", identifier))

    async def add_contacts_to_list(self, list_id, emails):
        _FakeClient.calls.append(("add_to_list", (list_id, tuple(emails))))
        return {}

    async def remove_contacts_from_list(self, list_id, emails):
        _FakeClient.calls.append(("remove_from_list", (list_id, tuple(emails))))
        return {}


def _run(session, target, *, dry_run=False):
    with patch(
        "app.integrations.brevo.sync_targets.BrevoClient", _FakeClient
    ):
        return run_brevo_target(session, target, dry_run=dry_run)


def test_push_creates_contacts_and_adds_to_list(session_factory):
    _FakeClient.existing = set()
    _FakeClient.calls = []
    with session_factory() as session:
        target = _seed_target(session)
        stats = _run(session, target)
        session.commit()
        assert stats["pushed_new"] == 3
        assert stats["pushed_updated"] == 0
        assert stats["added_to_list"] == 3
        add_calls = [c for c in _FakeClient.calls if c[0] == "add_to_list"]
        assert add_calls[0][1][0] == 7
        memberships = list(session.scalars(select(BrevoTargetMembership)))
        assert len(memberships) == 3


def test_push_existing_contact_falls_back_to_update(session_factory):
    _FakeClient.existing = {"ana@example.com"}
    _FakeClient.calls = []
    with session_factory() as session:
        target = _seed_target(session)
        stats = _run(session, target)
        assert stats["pushed_new"] == 2
        assert stats["pushed_updated"] == 1
        assert ("update", "ana@example.com") in _FakeClient.calls


def test_contact_leaving_segment_is_removed_from_list(session_factory):
    """A contact that matched on run #1 but lost consent before run #2
    must be removed from the Brevo list (and only from the list)."""
    _FakeClient.existing = set()
    _FakeClient.calls = []
    with session_factory() as session:
        target = _seed_target(session)
        _run(session, target)
        session.commit()

        ana = session.scalar(
            select(Contact).where(Contact.email == "ana@example.com")
        )
        ana.marketing_consent = "unsubscribed"
        session.commit()

        _FakeClient.calls = []
        stats = _run(session, target)
        session.commit()
        removes = [c for c in _FakeClient.calls if c[0] == "remove_from_list"]
        assert removes == [("remove_from_list", (7, ("ana@example.com",)))]
        assert stats["removed_from_list"] == 1
        memberships = {
            m.contact_id for m in session.scalars(select(BrevoTargetMembership))
        }
        assert ana.id not in memberships
        assert len(memberships) == 2


def test_dry_run_never_calls_brevo(session_factory):
    _FakeClient.calls = []
    with session_factory() as session:
        target = _seed_target(session)
        stats = _run(session, target, dry_run=True)
        assert stats["dry_run"] is True
        assert stats["matched"] == 3
        assert sorted(stats["would_push"]) == [
            "ana@example.com",
            "boris@example.com",
            "carla@example.com",
        ]
        assert _FakeClient.calls == []
        assert session.scalar(select(BrevoTargetMembership)) is None


def test_contacts_without_email_are_excluded(session_factory):
    _FakeClient.existing = set()
    _FakeClient.calls = []
    with session_factory() as session:
        target = _seed_target(session)
        session.add(
            Contact(first_name="NoEmail", email=None, marketing_consent="granted")
        )
        session.commit()
        stats = _run(session, target)
        assert stats["matched"] == 3  # the no-email row never counts


# ---------------------------------------------------------------------------
# API CRUD
# ---------------------------------------------------------------------------


def _seed_account_and_segment(client: TestClient) -> tuple[str, str]:
    factory = client.app.dependency_overrides[get_session]
    gen = factory()
    session = next(gen)
    try:
        session.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="main",
                display_name="Brevo",
                enabled=True,
            )
        )
        session.commit()
    finally:
        gen.close()
    segment = client.post(
        "/api/segments",
        json={
            "name": "Todos",
            "rules": {
                "type": "rule",
                "field": "is_active",
                "comparator": "eq",
                "value": True,
            },
        },
        headers=auth_headers(client, "manager"),
    ).json()
    return "main", segment["id"]


def test_target_crud_lifecycle(client: TestClient):
    account_id, segment_id = _seed_account_and_segment(client)
    headers = auth_headers(client, "manager")

    with patch("app.api.brevo.schedule_heartbeat"):
        created = client.post(
            "/api/brevo/sync-targets",
            json={
                "brevo_account_id": account_id,
                "name": "Mi target",
                "segment_id": segment_id,
                "brevo_list_id": "12",
                "sync_interval_minutes": 30,
            },
            headers=headers,
        )
    assert created.status_code == 201, created.text
    target = created.json()
    assert target["segment_name"] == "Todos"
    assert target["last_run_status"] == "idle"

    listed = client.get(
        f"/api/brevo/sync-targets?account_id={account_id}", headers=headers
    ).json()
    assert len(listed) == 1

    patched = client.patch(
        f"/api/brevo/sync-targets/{target['id']}",
        json={"is_active": False, "name": "Renombrado"},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renombrado"
    assert patched.json()["is_active"] is False

    deleted = client.delete(
        f"/api/brevo/sync-targets/{target['id']}", headers=headers
    )
    assert deleted.status_code == 200


def test_target_create_rejects_pull_only(client: TestClient):
    account_id, segment_id = _seed_account_and_segment(client)
    response = client.post(
        "/api/brevo/sync-targets",
        json={
            "brevo_account_id": account_id,
            "name": "X",
            "segment_id": segment_id,
            "sync_direction": "pull_only",
        },
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 422


def test_target_dry_run_returns_preview(client: TestClient):
    account_id, segment_id = _seed_account_and_segment(client)
    headers = auth_headers(client, "manager")
    client.post(
        "/api/contacts",
        json={"first_name": "Ana", "email": "ana@example.com"},
        headers=headers,
    )
    with patch("app.api.brevo.schedule_heartbeat"):
        target = client.post(
            "/api/brevo/sync-targets",
            json={
                "brevo_account_id": account_id,
                "name": "T",
                "segment_id": segment_id,
            },
            headers=headers,
        ).json()
    response = client.post(
        f"/api/brevo/sync-targets/{target['id']}/run?dry_run=true",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dry_run"] is True
    assert body["stats"]["matched"] >= 1
    assert "ana@example.com" in body["stats"]["would_push"]


def test_viewer_cannot_create_target(client: TestClient):
    account_id, segment_id = _seed_account_and_segment(client)
    response = client.post(
        "/api/brevo/sync-targets",
        json={
            "brevo_account_id": account_id,
            "name": "X",
            "segment_id": segment_id,
        },
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 403
