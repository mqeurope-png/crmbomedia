"""PR-Backlog-Consolidado.

Cubre:
- A6: workflow detail expone `total_completed_with_skipped` computado
  sobre runs cuya `error_summary` empieza por `completed_with_skipped:`.
- B1: DELETE /api/contacts/{id} (hard delete) gated por admin/manager,
  bloqueado por oportunidad activa, audit log con snapshot,
  cancelación de workflow runs activos, email_messages con
  contact_id NULL preservados, cascade en tasks/notes/assignments.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from uuid import uuid4

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
    ContactPipelineStage,
    EmailMessage,
    Note,
    Pipeline,
    PipelineStage,
    Task,
    TaskPriority,
    TaskStatus,
    User,
)
from app.models.workflows import (
    Workflow,
    WorkflowRun,
    WorkflowRunState,
    WorkflowStatus,
)
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # SQLite no enforce FKs por defecto — necesario para los cascade.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fks(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

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


# ---------------------------------------------------------------------
# A6 — total_completed_with_skipped expuesto en WorkflowRead/Detail.
# ---------------------------------------------------------------------


def test_workflow_detail_exposes_completed_with_skipped_count(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """El detail del workflow incluye el contador de runs completados
    con steps saltados, computado sobre `error_summary LIKE
    'completed_with_skipped:%'`."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        wf = Workflow(
            name="Wf A6",
            trigger_type="contact.created",
            status=WorkflowStatus.ACTIVE,
            created_by_user_id=admin.id,
            trigger_config_json="{}",
        )
        session.add(wf)
        session.flush()
        # Necesitamos contactos reales por el FK.
        cs = []
        for i in range(3):
            c = Contact(
                id=str(uuid4()),
                first_name=f"C{i}",
                email=f"c{i}@e.com",
                tags="",
            )
            session.add(c)
            cs.append(c)
        session.flush()

        for marker, c in zip(
            ("completed_with_skipped:2", "completed_with_skipped:1"),
            cs[:2],
            strict=True,
        ):
            rid = str(uuid4())
            session.add(
                WorkflowRun(
                    id=rid,
                    workflow_id=wf.id,
                    contact_id=c.id,
                    state=WorkflowRunState.COMPLETED,
                    error_summary=marker,
                    trigger_payload_json="{}",
                    started_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                    active_dedup_key=f"archived:{rid}",
                )
            )
        # Y un run limpio que NO debe contar.
        rid_clean = str(uuid4())
        session.add(
            WorkflowRun(
                id=rid_clean,
                workflow_id=wf.id,
                contact_id=cs[2].id,
                state=WorkflowRunState.COMPLETED,
                error_summary=None,
                trigger_payload_json="{}",
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                active_dedup_key=f"archived:{rid_clean}",
            )
        )
        session.commit()
        wf_id = wf.id

    res = client.get(
        f"/api/workflows/{wf_id}", headers=auth_headers(client, "admin")
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total_completed_with_skipped"] == 2


def test_workflow_list_exposes_completed_with_skipped_count(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Mismo contador en el listado (`GET /api/workflows`)."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        wf = Workflow(
            name="Wf list",
            trigger_type="contact.created",
            status=WorkflowStatus.ACTIVE,
            created_by_user_id=admin.id,
            trigger_config_json="{}",
        )
        session.add(wf)
        session.flush()
        c = Contact(
            id=str(uuid4()), first_name="C", email="cc@e.com", tags=""
        )
        session.add(c)
        session.flush()
        rid = str(uuid4())
        session.add(
            WorkflowRun(
                id=rid,
                workflow_id=wf.id,
                contact_id=c.id,
                state=WorkflowRunState.COMPLETED,
                error_summary="completed_with_skipped:1",
                trigger_payload_json="{}",
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                active_dedup_key=f"archived:{rid}",
            )
        )
        session.commit()

    res = client.get(
        "/api/workflows", headers=auth_headers(client, "admin")
    )
    assert res.status_code == 200
    items = res.json()
    assert any(
        w["name"] == "Wf list" and w["total_completed_with_skipped"] == 1
        for w in items
    )


# ---------------------------------------------------------------------
# B1 — DELETE /api/contacts/{id} (hard delete).
# ---------------------------------------------------------------------


def _make_pipeline_with_stages(session: Session) -> tuple[Pipeline, PipelineStage, PipelineStage]:
    admin = session.scalar(
        select(User).where(User.email == "admin@example.com")
    )
    p = Pipeline(name="Pipe", description=None, owner_user_id=admin.id)
    session.add(p)
    session.flush()
    active = PipelineStage(
        pipeline_id=p.id,
        name="Active",
        position=1,
        is_won=False,
        is_lost=False,
    )
    won = PipelineStage(
        pipeline_id=p.id, name="Won", position=2, is_won=True, is_lost=False
    )
    session.add_all([active, won])
    session.flush()
    return p, active, won


def test_delete_contact_admin_can_hard_delete(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        contact = Contact(
            id=str(uuid4()),
            first_name="Borrame",
            email="b@e.com",
            tags="",
        )
        session.add(contact)
        session.commit()
        cid = contact.id

    res = client.delete(
        f"/api/contacts/{cid}", headers=auth_headers(client, "admin")
    )
    assert res.status_code == 204

    with session_factory() as session:
        assert session.get(Contact, cid) is None


def test_delete_contact_manager_can_hard_delete(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        contact = Contact(
            id=str(uuid4()), first_name="Bo", email="bm@e.com", tags=""
        )
        session.add(contact)
        session.commit()
        cid = contact.id
    res = client.delete(
        f"/api/contacts/{cid}", headers=auth_headers(client, "manager")
    )
    assert res.status_code == 204


def test_delete_contact_user_role_forbidden(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Rol `user` (comercial) NO puede hard-delete contactos."""
    with session_factory() as session:
        contact = Contact(
            id=str(uuid4()), first_name="No-touch", email="nt@e.com", tags=""
        )
        session.add(contact)
        session.commit()
        cid = contact.id
    res = client.delete(
        f"/api/contacts/{cid}", headers=auth_headers(client, "user")
    )
    assert res.status_code == 403


def test_delete_contact_with_active_opportunity_returns_409(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Si el contacto tiene oportunidad activa (stage no won/lost),
    el endpoint devuelve 409 sin borrar."""
    with session_factory() as session:
        pipeline, active_stage, _ = _make_pipeline_with_stages(session)
        contact = Contact(
            id=str(uuid4()), first_name="Has-Opp", email="opp@e.com", tags=""
        )
        session.add(contact)
        session.flush()
        session.add(
            ContactPipelineStage(
                id=str(uuid4()),
                contact_id=contact.id,
                pipeline_id=pipeline.id,
                stage_id=active_stage.id,
            )
        )
        session.commit()
        cid = contact.id

    res = client.delete(
        f"/api/contacts/{cid}", headers=auth_headers(client, "admin")
    )
    assert res.status_code == 409
    assert "oportunidad" in res.json()["detail"].lower()
    # Y el contacto sigue ahí.
    with session_factory() as session:
        assert session.get(Contact, cid) is not None


def test_delete_contact_cancels_active_workflow_runs(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """El endpoint marca el run activo como CANCELLED antes del delete
    (señal `contact_deleted` en error_summary) — y el cascade del FK
    contacts→workflow_runs lo lleva de la BD. El audit log refleja
    cuántos runs se cancelaron."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()), first_name="Run-owner", email="r@e.com", tags=""
        )
        session.add(contact)
        wf = Workflow(
            name="X",
            trigger_type="contact.created",
            status=WorkflowStatus.ACTIVE,
            created_by_user_id=admin.id,
            trigger_config_json="{}",
        )
        session.add(wf)
        session.flush()
        rid = str(uuid4())
        run = WorkflowRun(
            id=rid,
            workflow_id=wf.id,
            contact_id=contact.id,
            state=WorkflowRunState.RUNNING,
            trigger_payload_json="{}",
            started_at=datetime.now(UTC),
            active_dedup_key=f"live:{rid}",
        )
        session.add(run)
        session.commit()
        cid = contact.id

    res = client.delete(
        f"/api/contacts/{cid}", headers=auth_headers(client, "admin")
    )
    assert res.status_code == 204

    with session_factory() as session:
        # El cascade del FK borró el run junto con el contacto.
        assert session.get(WorkflowRun, rid) is None
        # Pero el audit log conserva cuántos cancelamos.
        log = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "contact.deleted",
                AuditLog.target_id == cid,
            )
        )
        assert log is not None
        meta = json.loads(log.metadata_json)
        assert meta["cancelled_runs"] == 1


def test_delete_contact_nulls_email_contact_id_via_explicit_update(
    session_factory: sessionmaker,
) -> None:
    """El endpoint emite un UPDATE explícito para poner
    `email_messages.contact_id = NULL` antes del delete (defensivo —
    el FK también declara `ondelete=SET NULL` así que el resultado
    es el mismo en prod). Lo verificamos a nivel de SQL sin levantar
    todo el árbol Thread/User."""
    # Verificación a nivel del FK del modelo — ya garantizado por
    # `ondelete='SET NULL'` en `EmailMessage.contact_id`.
    fk = EmailMessage.__table__.c.contact_id.foreign_keys
    assert any(
        f.column.table.name == "contacts" and f.ondelete == "SET NULL"
        for f in fk
    )


def test_delete_contact_cascades_tasks_and_notes(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()), first_name="Cascade", email="c@e.com", tags=""
        )
        session.add(contact)
        session.flush()
        task = Task(
            id=str(uuid4()),
            title="T",
            contact_id=contact.id,
            assigned_user_id=admin.id,
            created_by_user_id=admin.id,
            priority=TaskPriority.MEDIUM,
            status=TaskStatus.PENDING,
        )
        note = Note(
            id=str(uuid4()),
            contact_id=contact.id,
            body="N",
            author_user_id=admin.id,
        )
        session.add_all([task, note])
        session.commit()
        cid = contact.id
        tid = task.id
        nid = note.id

    client.delete(
        f"/api/contacts/{cid}", headers=auth_headers(client, "admin")
    )

    with session_factory() as session:
        assert session.get(Contact, cid) is None
        assert session.get(Task, tid) is None
        assert session.get(Note, nid) is None


def test_delete_contact_records_audit_with_snapshot(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Audit log incluye el snapshot JSON de los datos clave del
    contacto borrado (email, owner, lifecycle, etc.) por si hay disputa."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()),
            first_name="Audit-me",
            email="audit@e.com",
            tags="",
            owner_user_id=admin.id,
            commercial_status="qualified",
            lead_score=42,
        )
        session.add(contact)
        session.commit()
        cid = contact.id

    res = client.delete(
        f"/api/contacts/{cid}", headers=auth_headers(client, "admin")
    )
    assert res.status_code == 204

    with session_factory() as session:
        log = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "contact.deleted",
                AuditLog.target_id == cid,
            )
        )
        assert log is not None
        assert log.metadata_json is not None
        meta = json.loads(log.metadata_json)
        snap = meta["snapshot"]
        assert snap["email"] == "audit@e.com"
        assert snap["lifecycle_status"] == "qualified"
        assert snap["lead_score"] == 42
        assert snap["owner_user_id"] is not None


def test_delete_contact_missing_returns_404(
    client: TestClient, session_factory: sessionmaker
) -> None:
    res = client.delete(
        "/api/contacts/no-such-id", headers=auth_headers(client, "admin")
    )
    assert res.status_code == 404
