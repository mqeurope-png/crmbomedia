"""PR-Fix-Pestaña-Workflows-Y-Humanizar.

Cubre:
- `humanize_error_summary` traduce códigos conocidos, pasa los markers
  por prefijo sin tocarlos, y devuelve el código original cuando no
  está mapeado.
- `_record_history` escribe ya el texto humano (verificado leyendo la
  fila tras ejecutar un step con skip).
- GET `/api/workflows/_contacts/{id}/runs` devuelve los runs del
  contacto (smoke del endpoint que la pestaña usa).
- POST `/api/workflows/{id}/add-contact/{cid}` crea run forzado —
  admin/manager only.
- POST `/api/workflows/runs/{run_id}/cancel` cambia el state.
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
from app.models.crm import Base, Contact, User
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
from app.workflows.error_humanizer import humanize_error_summary
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


# ---------------------------------------------------------------------
# humanize_error_summary unit tests
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected_substring"),
    [
        ("contact_no_owner", "no tiene propietario"),
        ("contact_no_email", "no tiene email"),
        ("email_cap_reached", "Cuota diaria"),
        ("empty_tag", "no tiene tag configurado"),
        ("no_user_id", "user seleccionado"),
        ("owner_has_no_aliases", "aliases configurados"),
        ("wait_for_event_timeout", "esperó el evento"),
    ],
)
def test_humanize_error_summary_returns_human_message_for_known_codes(
    code: str, expected_substring: str
) -> None:
    out = humanize_error_summary(code)
    assert out is not None
    assert expected_substring.lower() in out.lower()


def test_humanize_error_summary_handles_parametrized_codes() -> None:
    assert "eduard@bomedia.net" in humanize_error_summary(
        "gmail_not_ready:eduard@bomedia.net"
    )
    assert "42" in humanize_error_summary("template_not_found:42")


def test_humanize_error_summary_preserves_completed_with_skipped_marker() -> None:
    """El frontend detecta este marker por prefijo — no traducir."""
    assert humanize_error_summary("completed_with_skipped:3") == "completed_with_skipped:3"


def test_humanize_error_summary_preserves_contact_deleted_marker() -> None:
    assert humanize_error_summary("contact_deleted") == "contact_deleted"


def test_humanize_error_summary_unknown_code_passes_through() -> None:
    """Si añadimos un código nuevo y olvidamos su traducción, queremos
    ver el código raw en la UI para detectarlo rápido."""
    assert (
        humanize_error_summary("brand_new_unknown_code")
        == "brand_new_unknown_code"
    )


def test_humanize_error_summary_handles_none() -> None:
    assert humanize_error_summary(None) is None


# ---------------------------------------------------------------------
# Integración: _record_history persiste el texto humano.
# ---------------------------------------------------------------------


def test_step_writes_humanized_summary_to_history(
    session_factory: sessionmaker,
) -> None:
    """End-to-end: ejecutamos un workflow donde send_email se salta
    por contact_no_owner. El history del run debe tener el mensaje
    humano, no el código raw."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()),
            first_name="C",
            email="c@e.com",
            tags="",
            # owner_user_id deliberately None → contact_no_owner.
        )
        session.add(contact)
        wf = Workflow(
            name="Wf",
            trigger_type="contact.created",
            status=WorkflowStatus.ACTIVE,
            created_by_user_id=admin.id,
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
                    "subject": "X",
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

        run = start_run(session, wf, contact)
        advance_run(session, run.id)
        session.commit()

        skipped_rows = list(
            session.scalars(
                select(WorkflowRunHistory).where(
                    WorkflowRunHistory.run_id == run.id,
                    WorkflowRunHistory.status == "skipped",
                )
            )
        )
        assert len(skipped_rows) == 1
        # El history almacenó el mensaje humano, no el código raw.
        assert "propietario" in (skipped_rows[0].error_summary or "").lower()
        assert "contact_no_owner" not in (skipped_rows[0].error_summary or "")


# ---------------------------------------------------------------------
# Endpoints que la pestaña Workflows usa.
# ---------------------------------------------------------------------


def _make_active_workflow(session: Session, admin_id: str) -> Workflow:
    wf = Workflow(
        name="Wf-active",
        trigger_type="contact.created",
        status=WorkflowStatus.ACTIVE,
        created_by_user_id=admin_id,
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
    return wf


def test_get_contact_workflow_runs_returns_runs(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()), first_name="C", email="c@e.com", tags=""
        )
        session.add(contact)
        wf = _make_active_workflow(session, admin.id)
        rid = str(uuid4())
        session.add(
            WorkflowRun(
                id=rid,
                workflow_id=wf.id,
                contact_id=contact.id,
                state=WorkflowRunState.COMPLETED,
                trigger_payload_json="{}",
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                active_dedup_key=f"archived:{rid}",
            )
        )
        session.commit()
        cid = contact.id

    res = client.get(
        f"/api/workflows/_contacts/{cid}/runs",
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["state"] == "completed"


def test_post_add_contact_to_workflow_admin_ok(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """admin OK + el run queda en BD."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()), first_name="C", email="c@e.com", tags=""
        )
        session.add(contact)
        wf = _make_active_workflow(session, admin.id)
        session.commit()
        cid = contact.id
        wid = wf.id

    res = client.post(
        f"/api/workflows/{wid}/add-contact/{cid}",
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code in (200, 201), res.text

    with session_factory() as session:
        runs = list(
            session.scalars(
                select(WorkflowRun).where(WorkflowRun.contact_id == cid)
            )
        )
        assert len(runs) == 1


def test_post_workflow_run_cancel_changes_state(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()), first_name="C", email="c@e.com", tags=""
        )
        session.add(contact)
        wf = _make_active_workflow(session, admin.id)
        rid = str(uuid4())
        session.add(
            WorkflowRun(
                id=rid,
                workflow_id=wf.id,
                contact_id=contact.id,
                state=WorkflowRunState.RUNNING,
                trigger_payload_json="{}",
                started_at=datetime.now(UTC),
                active_dedup_key=f"live:{rid}",
            )
        )
        session.commit()

    res = client.post(
        f"/api/workflows/runs/{rid}/cancel",
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code in (200, 204), res.text

    with session_factory() as session:
        run = session.get(WorkflowRun, rid)
        assert run is not None
        assert run.state in (
            WorkflowRunState.CANCELLED,
            WorkflowRunState.CANCELLING,
        )
