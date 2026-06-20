"""PR-Fix-Assign-Owner-Completo.

El step `action_assign_owner` antes sólo escribía `contact.owner_user_id`
y dejaba `contact_assignments` vacía. El frontend (cabecera de ficha,
sidebar comerciales asignados, modal Editar) lee de esa tabla, así
que la asignación parecía no funcionar pese a que internamente sí
estaba puesta.

Tras el fix, el step replica lo que hace `PATCH /api/contacts/{id}`
con `owner_id`: upsert primary en `contact_assignments`, demote del
primary anterior si existía, recompute del cache `owner_user_id`.

Además: cuando un run termina COMPLETED pero algún step quedó
`skipped`, marcamos `error_summary='completed_with_skipped:N'` para
que el frontend pueda distinguirlo del completado limpio.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import patch as mock_patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    ContactAssignment,
    User,
)
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowRun,
    WorkflowRunHistory,
    WorkflowRunState,
    WorkflowStatus,
    WorkflowStep,
)
from app.workflows.engine import advance_run, start_run
from app.workflows.steps import _step_assign_owner
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


def _make_run() -> WorkflowRun:
    return WorkflowRun(
        id="r1",
        workflow_id="w1",
        contact_id="c1",
        trigger_payload_json="{}",
    )


def _make_step(cfg: dict, *, step_type: str = "action_assign_owner") -> WorkflowStep:
    return WorkflowStep(
        id="s1",
        workflow_id="w1",
        type=step_type,
        position_x=0,
        position_y=0,
        is_entry=False,
        config_json=json.dumps(cfg),
    )


# ---------------------------------------------------------------------
# Bug crítico: el step debe poblar contact_assignments.
# ---------------------------------------------------------------------


def test_action_assign_owner_populates_contact_assignments(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert admin is not None
        contact = Contact(
            id=str(uuid4()),
            first_name="C",
            email="c@e.com",
            tags="",
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)

        step = _make_step({"user_id": admin.id})
        result = _step_assign_owner(session, _make_run(), step, contact)
        session.commit()

        assert result.status == "ok"
        # Cache desnormalizado correcto.
        session.refresh(contact)
        assert contact.owner_user_id == admin.id
        # Y — el bug — la fila relacional debe estar.
        assignments = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact.id
                )
            )
        )
        assert len(assignments) == 1
        assert assignments[0].user_id == admin.id
        assert assignments[0].is_primary is True
        assert assignments[0].source == "workflow"


def test_action_assign_owner_replaces_existing_primary(
    session_factory: sessionmaker,
) -> None:
    """Si el contacto ya tenía otro primary, el step lo reemplaza
    (idempotente, sin duplicar filas)."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        manager = session.scalar(
            select(User).where(User.email == "manager@example.com")
        )
        assert admin is not None and manager is not None
        contact = Contact(
            id=str(uuid4()),
            first_name="C",
            email="c@e.com",
            tags="",
        )
        session.add(contact)
        session.flush()

        # Seed: contact ya tiene manager como primary (vía add_assignment).
        from app.repositories import assignments as _assignments

        _assignments.add_assignment(
            session,
            contact_id=contact.id,
            user_id=manager.id,
            is_primary=True,
            assigned_by_user_id=manager.id,
            source="manual",
        )
        session.commit()
        session.refresh(contact)
        assert contact.owner_user_id == manager.id

        # Ejecuta el step asignando a admin.
        step = _make_step({"user_id": admin.id})
        result = _step_assign_owner(session, _make_run(), step, contact)
        session.commit()

        assert result.status == "ok"
        session.refresh(contact)
        assert contact.owner_user_id == admin.id

        # Debe haber 2 assignments (manager demoted + admin primary), no
        # duplicación del admin.
        assignments = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact.id
                )
            )
        )
        primaries = [a for a in assignments if a.is_primary]
        assert len(primaries) == 1
        assert primaries[0].user_id == admin.id


def test_action_assign_owner_same_as_patch_endpoint(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """El estado final tras el step debe ser idéntico al del PATCH
    /api/contacts/{id} manual con el mismo owner_id."""
    # Caso A: PATCH manual.
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact_a = Contact(
            id=str(uuid4()),
            first_name="A",
            email="a@e.com",
            tags="",
        )
        session.add(contact_a)
        session.commit()
        ca_id = contact_a.id
        admin_id = admin.id

    res = client.patch(
        f"/api/contacts/{ca_id}",
        json={"owner_id": admin_id},
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200, res.text

    # Caso B: step del workflow.
    with session_factory() as session:
        contact_b = Contact(
            id=str(uuid4()),
            first_name="B",
            email="b@e.com",
            tags="",
        )
        session.add(contact_b)
        session.commit()
        session.refresh(contact_b)

        step = _make_step({"user_id": admin_id})
        _step_assign_owner(session, _make_run(), step, contact_b)
        session.commit()

    with session_factory() as session:
        # Estado final del contact A (vía PATCH).
        contact_a = session.get(Contact, ca_id)
        assignments_a = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == ca_id
                )
            )
        )
        # Estado final del contact B (vía step).
        contact_b = session.scalar(
            select(Contact).where(Contact.first_name == "B")
        )
        assignments_b = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == contact_b.id
                )
            )
        )

        # Misma estructura: 1 fila primary apuntando al mismo user.
        assert contact_a.owner_user_id == contact_b.owner_user_id == admin_id
        assert len(assignments_a) == len(assignments_b) == 1
        assert assignments_a[0].user_id == assignments_b[0].user_id
        assert assignments_a[0].is_primary == assignments_b[0].is_primary is True


# ---------------------------------------------------------------------
# Mejora paralela: distinguir completed_with_skipped del clean.
# ---------------------------------------------------------------------


def _build_workflow_with_skipped_send_email(
    session: Session, owner_id: str
) -> tuple[Workflow, Contact]:
    """trigger → action_send_email (con from_alias_mode=owner_default
    sobre contacto SIN owner → skipped) → exit_natural."""
    contact = Contact(
        id=str(uuid4()),
        first_name="C",
        email="c@e.com",
        tags="",
        # owner_user_id deliberately None — provocará el skip.
    )
    session.add(contact)
    wf = Workflow(
        name="Wf",
        trigger_type="contact.created",
        status=WorkflowStatus.ACTIVE,
        created_by_user_id=owner_id,
        trigger_config_json="{}",
    )
    session.add(wf)
    session.flush()
    trig = WorkflowStep(
        workflow_id=wf.id, type="trigger", config_json="{}",
        position_x=0, position_y=0, is_entry=True,
    )
    send = WorkflowStep(
        workflow_id=wf.id, type="action_send_email",
        config_json=json.dumps(
            {
                "mode": "custom",
                "subject": "Hi",
                "body_html": "<p>x</p>",
                "from_alias_mode": "owner_default",
            }
        ),
        position_x=0, position_y=150, is_entry=False,
    )
    exit_step = WorkflowStep(
        workflow_id=wf.id, type="exit_natural", config_json="{}",
        position_x=0, position_y=300, is_entry=False,
    )
    session.add_all([trig, send, exit_step])
    session.flush()
    session.add_all(
        [
            WorkflowEdge(
                workflow_id=wf.id, from_step_id=trig.id,
                to_step_id=send.id, branch_label="default",
            ),
            WorkflowEdge(
                workflow_id=wf.id, from_step_id=send.id,
                to_step_id=exit_step.id, branch_label="default",
            ),
        ]
    )
    session.commit()
    session.refresh(contact)
    return wf, contact


def test_workflow_run_with_skipped_step_marks_completed_with_warning(
    session_factory: sessionmaker,
) -> None:
    """Run con send_email skipped por contact_no_owner → state=COMPLETED
    pero error_summary='completed_with_skipped:1' para que el frontend
    pueda diferenciar."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert admin is not None
        wf, contact = _build_workflow_with_skipped_send_email(session, admin.id)

        run = start_run(session, wf, contact)
        assert run is not None
        # send_email no se llamará externamente porque el step entra en
        # skip antes (contact_no_owner). Pero mockeamos por defensividad.
        with mock_patch("app.integrations.gmail.service.send_email"):
            advance_run(session, run.id)
        session.commit()

        session.refresh(run)
        assert run.state == WorkflowRunState.COMPLETED
        assert (run.error_summary or "").startswith("completed_with_skipped:")
        # Y el history confirma que hubo skipped step.
        hist = list(
            session.scalars(
                select(WorkflowRunHistory).where(
                    WorkflowRunHistory.run_id == run.id,
                    WorkflowRunHistory.status == "skipped",
                )
            )
        )
        assert len(hist) >= 1


def test_workflow_run_clean_completion_has_no_warning_marker(
    session_factory: sessionmaker,
) -> None:
    """Run sin steps saltados → error_summary debe quedar None
    (completed_clean)."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()), first_name="C", email="c@e.com", tags=""
        )
        session.add(contact)
        wf = Workflow(
            name="Wf clean", trigger_type="contact.created",
            status=WorkflowStatus.ACTIVE, created_by_user_id=admin.id,
            trigger_config_json="{}",
        )
        session.add(wf)
        session.flush()
        trig = WorkflowStep(
            workflow_id=wf.id, type="trigger", config_json="{}",
            position_x=0, position_y=0, is_entry=True,
        )
        exit_step = WorkflowStep(
            workflow_id=wf.id, type="exit_natural", config_json="{}",
            position_x=0, position_y=150, is_entry=False,
        )
        session.add_all([trig, exit_step])
        session.flush()
        session.add(
            WorkflowEdge(
                workflow_id=wf.id, from_step_id=trig.id,
                to_step_id=exit_step.id, branch_label="default",
            )
        )
        session.commit()
        session.refresh(contact)

        run = start_run(session, wf, contact)
        advance_run(session, run.id)
        session.commit()

        session.refresh(run)
        assert run.state == WorkflowRunState.COMPLETED
        assert run.error_summary is None
