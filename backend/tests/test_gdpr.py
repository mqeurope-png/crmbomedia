"""Tests for the GDPR / RGPD subject-rights endpoints + processing."""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.audit import Action
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.main import app
from app.models.crm import (
    AuditLog,
    Base,
    ConsentStatus,
    Contact,
    GdprRequest,
    GdprRequestStatus,
    GdprRequestType,
    Note,
    Task,
    User,
    UserRole,
)
from tests._test_helpers import DEFAULT_PASSWORD, auth_headers, seed_test_users


@pytest.fixture()
def export_root(tmp_path: Path) -> Path:
    return tmp_path / "gdpr_exports"


@pytest.fixture()
def stack(
    export_root: Path,
) -> Generator[tuple[TestClient, sessionmaker, Path], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as seed:
        seed_test_users(seed)

    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    def override_settings() -> Settings:
        # Reuse the real Settings but reroute the export directory to a
        # tmp_path so each test gets a clean filesystem.
        base = get_settings()
        return base.model_copy(update={"gdpr_export_root": str(export_root)})

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    with TestClient(app) as client:
        yield client, session_factory, export_root
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(stack: tuple[TestClient, sessionmaker, Path]) -> TestClient:
    return stack[0]


def _seed_subject(session_factory: sessionmaker, email: str = "subject@example.com") -> str:
    """Insert a contact + one note + one task + one matching audit row.
    Returns the contact id."""
    with session_factory() as session:
        contact = Contact(
            first_name="Sub",
            last_name="Ject",
            email=email,
            phone="+34 600 000 000",
            marketing_consent=ConsentStatus.GRANTED,
        )
        session.add(contact)
        session.flush()
        session.add(Note(body="sample", contact_id=contact.id))
        admin_id = session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN).limit(1)
        )
        session.add(
            Task(
                title="follow up",
                contact_id=contact.id,
                assigned_user_id=admin_id,
                created_by_user_id=admin_id,
            )
        )
        session.add(
            AuditLog(
                actor_email=email,
                action="auth.login_success",
                target_type="user",
                target_id=contact.id,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
        return contact.id


def _create_request(
    client: TestClient,
    *,
    headers: dict[str, str],
    request_type: str,
    email: str = "subject@example.com",
    notes: str | None = None,
) -> dict:
    body: dict[str, str | None] = {
        "subject_email": email,
        "request_type": request_type,
    }
    if notes is not None:
        body["notes"] = notes
    response = client.post("/api/gdpr/requests", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Access control: only admins can touch /api/gdpr/*
# ---------------------------------------------------------------------------


def test_gdpr_requests_require_admin(client: TestClient):
    manager = auth_headers(client, "manager")
    create = client.post(
        "/api/gdpr/requests",
        json={"subject_email": "x@example.com", "request_type": "access"},
        headers=manager,
    )
    assert create.status_code == 403

    listing = client.get("/api/gdpr/requests", headers=manager)
    assert listing.status_code == 403


def test_create_and_list_request(client: TestClient):
    headers = auth_headers(client, "admin")
    created = _create_request(
        client, headers=headers, request_type="access", email="alice@example.com"
    )
    assert created["status"] == GdprRequestStatus.PENDING.value
    assert created["request_type"] == GdprRequestType.ACCESS.value

    listing = client.get("/api/gdpr/requests", headers=headers)
    assert listing.status_code == 200
    assert listing.headers["x-total-count"] == "1"
    items = listing.json()
    assert len(items) == 1
    assert items[0]["subject_email"] == "alice@example.com"


def test_filter_by_type_and_status(client: TestClient):
    headers = auth_headers(client, "admin")
    _create_request(client, headers=headers, request_type="access", email="a@example.com")
    _create_request(
        client, headers=headers, request_type="erasure", email="b@example.com"
    )

    only_erasure = client.get(
        "/api/gdpr/requests", params={"request_type": "erasure"}, headers=headers
    )
    assert only_erasure.status_code == 200
    assert {row["request_type"] for row in only_erasure.json()} == {"erasure"}

    pending = client.get(
        "/api/gdpr/requests", params={"status": "pending"}, headers=headers
    )
    assert pending.status_code == 200
    assert {row["status"] for row in pending.json()} == {"pending"}


# ---------------------------------------------------------------------------
# PATCH + audit
# ---------------------------------------------------------------------------


def test_patch_updates_notes_and_audits(client: TestClient, stack):
    headers = auth_headers(client, "admin")
    request = _create_request(client, headers=headers, request_type="access")
    response = client.patch(
        f"/api/gdpr/requests/{request['id']}",
        json={"status": "in_progress", "notes": "Llamada al titular"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["notes"] == "Llamada al titular"

    _, session_factory, _ = stack
    with session_factory() as session:
        rows = (
            session.query(AuditLog)
            .filter(AuditLog.action == Action.GDPR_REQUEST_UPDATED)
            .all()
        )
    assert rows, "expected gdpr.request_updated audit row"


# ---------------------------------------------------------------------------
# Process: access
# ---------------------------------------------------------------------------


def test_process_access_writes_json_and_audits(
    client: TestClient, stack, export_root: Path
):
    _, session_factory, _ = stack
    _seed_subject(session_factory, "subject@example.com")

    headers = auth_headers(client, "admin")
    request = _create_request(
        client, headers=headers, request_type="access", email="subject@example.com"
    )
    process = client.post(
        f"/api/gdpr/requests/{request['id']}/process", headers=headers
    )
    assert process.status_code == 200, process.text
    body = process.json()
    assert body["status"] == GdprRequestStatus.COMPLETED.value
    assert body["evidence_path"]
    evidence = Path(body["evidence_path"])
    assert evidence.exists(), f"expected JSON export at {evidence}"
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["subject_email"] == "subject@example.com"
    assert payload["contact"]["email"] == "subject@example.com"
    assert len(payload["notes"]) == 1
    assert len(payload["tasks"]) == 1
    assert payload["audit_logs"], "expected matching audit rows in export"

    with session_factory() as session:
        rows = (
            session.query(AuditLog)
            .filter(AuditLog.action == Action.GDPR_EXPORT_GENERATED)
            .all()
        )
    assert rows, "expected gdpr.export_generated audit row"


def test_process_access_without_contact_still_succeeds(
    client: TestClient, stack, export_root: Path
):
    headers = auth_headers(client, "admin")
    request = _create_request(
        client, headers=headers, request_type="access", email="ghost@example.com"
    )
    process = client.post(
        f"/api/gdpr/requests/{request['id']}/process", headers=headers
    )
    assert process.status_code == 200
    body = process.json()
    assert body["payload"]["counts"]["contact_found"] is False
    assert Path(body["evidence_path"]).exists()


# ---------------------------------------------------------------------------
# Process: portability (JSON + CSV)
# ---------------------------------------------------------------------------


def test_process_portability_writes_json_and_csv(
    client: TestClient, stack, export_root: Path
):
    _, session_factory, _ = stack
    _seed_subject(session_factory, "port@example.com")

    headers = auth_headers(client, "admin")
    request = _create_request(
        client, headers=headers, request_type="portability", email="port@example.com"
    )
    process = client.post(
        f"/api/gdpr/requests/{request['id']}/process", headers=headers
    )
    assert process.status_code == 200, process.text
    body = process.json()
    assert set(body["payload"]["formats"]) == {"json", "csv"}
    assert Path(body["evidence_path"]).exists()
    assert Path(body["payload"]["evidence_path_csv"]).exists()

    csv_text = Path(body["payload"]["evidence_path_csv"]).read_text(encoding="utf-8")
    assert "section" in csv_text.splitlines()[0]
    assert "contact" in csv_text


# ---------------------------------------------------------------------------
# Process: rectification
# ---------------------------------------------------------------------------


def test_process_rectification_returns_endpoint_list(client: TestClient, stack):
    _, session_factory, _ = stack
    _seed_subject(session_factory, "rect@example.com")
    headers = auth_headers(client, "admin")
    request = _create_request(
        client, headers=headers, request_type="rectification", email="rect@example.com"
    )
    process = client.post(
        f"/api/gdpr/requests/{request['id']}/process", headers=headers
    )
    assert process.status_code == 200
    body = process.json()
    endpoints = body["payload"]["endpoints"]
    paths = {e["path"] for e in endpoints}
    assert "/api/contacts/{contact_id}" in paths
    assert body["payload"]["contact_found"] is True


# ---------------------------------------------------------------------------
# Process: erasure
# ---------------------------------------------------------------------------


def test_process_erasure_deletes_contact_and_anonymizes_audits(
    client: TestClient, stack
):
    _, session_factory, _ = stack
    contact_id = _seed_subject(session_factory, "erase@example.com")

    headers = auth_headers(client, "admin")
    request = _create_request(
        client, headers=headers, request_type="erasure", email="erase@example.com"
    )
    process = client.post(
        f"/api/gdpr/requests/{request['id']}/process", headers=headers
    )
    assert process.status_code == 200, process.text
    body = process.json()
    assert body["payload"]["contact_deleted"] is True
    assert body["payload"]["audit_logs_anonymized"] >= 1
    marker = body["payload"]["marker"]
    assert marker.startswith("[ERASED-")

    with session_factory() as session:
        # Contact is gone, dependent rows cascaded.
        assert session.get(Contact, contact_id) is None
        assert session.scalar(select(Note).where(Note.contact_id == contact_id)) is None
        assert session.scalar(select(Task).where(Task.contact_id == contact_id)) is None
        # No audit row carries the original email any more.
        leftover = list(
            session.scalars(
                select(AuditLog).where(AuditLog.actor_email == "erase@example.com")
            )
        )
        assert leftover == []
        marked = list(
            session.scalars(select(AuditLog).where(AuditLog.actor_email == marker))
        )
        assert marked, "expected at least one audit row with the [ERASED-...] marker"


def test_erasure_request_without_contact_still_anonymizes_audits(
    client: TestClient, stack
):
    """Even when the contact row is gone, residual audit rows for that
    email should still be anonymised so logs can't be reverse-correlated."""
    _, session_factory, _ = stack
    with session_factory() as session:
        session.add(
            AuditLog(
                actor_email="ghost@example.com",
                action="auth.login_success",
                target_type="user",
                target_id="x",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()

    headers = auth_headers(client, "admin")
    request = _create_request(
        client, headers=headers, request_type="erasure", email="ghost@example.com"
    )
    process = client.post(
        f"/api/gdpr/requests/{request['id']}/process", headers=headers
    )
    assert process.status_code == 200
    body = process.json()
    assert body["payload"]["contact_deleted"] is False
    assert body["payload"]["audit_logs_anonymized"] >= 1


# ---------------------------------------------------------------------------
# Process: objection
# ---------------------------------------------------------------------------


def test_process_objection_flips_consent_and_deactivates(
    client: TestClient, stack
):
    _, session_factory, _ = stack
    contact_id = _seed_subject(session_factory, "obj@example.com")

    headers = auth_headers(client, "admin")
    request = _create_request(
        client, headers=headers, request_type="objection", email="obj@example.com"
    )
    process = client.post(
        f"/api/gdpr/requests/{request['id']}/process", headers=headers
    )
    assert process.status_code == 200
    body = process.json()
    assert body["payload"]["marketing_consent"] == ConsentStatus.DENIED.value
    assert body["payload"]["is_active"] is False

    with session_factory() as session:
        contact = session.get(Contact, contact_id)
        assert contact is not None
        assert contact.marketing_consent == ConsentStatus.DENIED
        assert contact.is_active is False


# ---------------------------------------------------------------------------
# Idempotency: a second process call on a completed request is a 400.
# ---------------------------------------------------------------------------


def test_double_process_returns_400(client: TestClient, stack):
    _, session_factory, _ = stack
    _seed_subject(session_factory, "twice@example.com")

    headers = auth_headers(client, "admin")
    request = _create_request(
        client, headers=headers, request_type="access", email="twice@example.com"
    )
    first = client.post(
        f"/api/gdpr/requests/{request['id']}/process", headers=headers
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/gdpr/requests/{request['id']}/process", headers=headers
    )
    assert second.status_code == 400


def test_create_audit_event_recorded(client: TestClient, stack):
    headers = auth_headers(client, "admin")
    _create_request(
        client, headers=headers, request_type="access", email="audit@example.com"
    )
    _, session_factory, _ = stack
    with session_factory() as session:
        rows = (
            session.query(AuditLog)
            .filter(AuditLog.action == Action.GDPR_REQUEST_CREATED)
            .all()
        )
    assert rows
    assert rows[-1].metadata_json is not None
    assert "audit@example.com" in rows[-1].metadata_json


# Ensure unused imports do not slip past ruff.
_ = GdprRequest
_ = DEFAULT_PASSWORD
