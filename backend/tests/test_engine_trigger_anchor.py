"""PR-Fix-Engine-Trigger-Step.

El step `trigger` es el nodo raíz del grafo del workflow — representa
el evento que dispara el run, no una acción ejecutable. El engine lo
trata como anchor: avanza directo a su sucesor sin invocar handler.

Casos cubiertos:
  - Workflow normal: trigger → action_send_email → exit.
    El run termina en `completed` y el send_email handler fue invocado.
  - Workflow degenerado: solo trigger sin sucesor.
    El run termina en `completed` con `error_summary="workflow_empty"`.
  - Validator: activar workflow con trigger huérfano devuelve 400.
  - Side-effect import: importar `app.workflows.dispatcher` también
    registra los step handlers vía `app.workflows.steps`.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base, Contact, User, UserEmailAliasPref
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowRunState,
    WorkflowStatus,
    WorkflowStep,
)
from app.workflows.engine import advance_run, get_step_handler, start_run
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


def _make_workflow_with_send_email(
    session: Session, owner_id: str
) -> Workflow:
    """trigger → action_send_email → exit_natural."""
    wf = Workflow(
        name="Test wf",
        trigger_type="contact.created",
        status=WorkflowStatus.ACTIVE,
        created_by_user_id=owner_id,
        trigger_config_json="{}",
    )
    session.add(wf)
    session.flush()
    trig = WorkflowStep(
        workflow_id=wf.id,
        type="trigger",
        config_json="{}",
        position_x=0,
        position_y=0,
        is_entry=True,
    )
    send = WorkflowStep(
        workflow_id=wf.id,
        type="action_send_email",
        config_json=json.dumps(
            {
                "mode": "custom",
                "subject": "Test",
                "body_html": "<p>Hi</p>",
                "from_alias_mode": "fixed",
                "from_alias": "info@bomedia.net",
            }
        ),
        position_x=0,
        position_y=150,
        is_entry=False,
    )
    exit_step = WorkflowStep(
        workflow_id=wf.id,
        type="exit_natural",
        config_json="{}",
        position_x=0,
        position_y=300,
        is_entry=False,
    )
    session.add_all([trig, send, exit_step])
    session.flush()
    session.add_all(
        [
            WorkflowEdge(
                workflow_id=wf.id,
                from_step_id=trig.id,
                to_step_id=send.id,
                branch_label="default",
            ),
            WorkflowEdge(
                workflow_id=wf.id,
                from_step_id=send.id,
                to_step_id=exit_step.id,
                branch_label="default",
            ),
        ]
    )
    session.commit()
    return wf


def test_dispatcher_import_registers_step_handlers() -> None:
    """Importar dispatcher debe forzar el import side-effect de steps —
    el RQ worker entra por ahí y necesita los handlers."""
    # Importarlo es responsabilidad del module loader. Si ya está
    # cargado en sys.modules, get_step_handler resuelve.
    import app.workflows.dispatcher  # noqa: F401
    assert get_step_handler("trigger") is not None
    assert get_step_handler("action_send_email") is not None
    assert get_step_handler("exit_natural") is not None


def test_engine_skips_trigger_step_and_executes_successor(
    session_factory: sessionmaker,
) -> None:
    """Workflow trigger → send_email → exit. El run debe completar y
    el send_email handler debe haber sido invocado."""
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
            owner_user_id=admin.id,
        )
        session.add(contact)
        # Seed default alias para el send_email
        session.add(
            UserEmailAliasPref(
                user_id=admin.id,
                alias_email="info@bomedia.net",
                is_allowed=True,
                is_default=True,
                gmail_display_name="Default",
            )
        )
        wf = _make_workflow_with_send_email(session, admin.id)

        run = start_run(
            session, wf, contact, trigger_payload={"event_type": "contact.created"}
        )
        assert run is not None

        with patch(
            "app.integrations.gmail.service.send_email"
        ) as mock_send:
            mock_send.return_value = type("M", (), {"id": "m1"})()
            advance_run(session, run.id)
            session.commit()

        session.refresh(run)
        assert run.state == WorkflowRunState.COMPLETED
        assert mock_send.call_count == 1


def test_engine_workflow_with_only_trigger_completes_immediately(
    session_factory: sessionmaker,
) -> None:
    """Workflow degenerado: solo trigger sin sucesor → run debe
    completar de inmediato con `error_summary='workflow_empty'`."""
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
            owner_user_id=admin.id,
        )
        session.add(contact)
        wf = Workflow(
            name="Solo trigger",
            trigger_type="contact.created",
            status=WorkflowStatus.ACTIVE,
            created_by_user_id=admin.id,
            trigger_config_json="{}",
        )
        session.add(wf)
        session.flush()
        session.add(
            WorkflowStep(
                workflow_id=wf.id,
                type="trigger",
                config_json="{}",
                position_x=0,
                position_y=0,
                is_entry=True,
            )
        )
        session.commit()

        run = start_run(session, wf, contact)
        assert run is not None
        advance_run(session, run.id)
        session.commit()

        session.refresh(run)
        assert run.state == WorkflowRunState.COMPLETED
        assert (run.error_summary or "") == "workflow_empty"


def test_validator_rejects_trigger_without_successor(
    client: TestClient,
) -> None:
    """Activar un workflow donde el trigger no tiene flecha saliente
    debe rechazarse con un mensaje claro."""
    create = client.post(
        "/api/workflows",
        json={"name": "Wf incompleto", "trigger_type": "contact.created"},
        headers=auth_headers(client, "admin"),
    )
    assert create.status_code == 201
    wf_id = create.json()["id"]

    # Solo el trigger, sin edges.
    save = client.put(
        f"/api/workflows/{wf_id}",
        json={
            "steps": [
                {
                    "client_id": "trig",
                    "type": "trigger",
                    "config": {},
                    "position_x": 0,
                    "position_y": 0,
                    "is_entry": True,
                }
            ],
            "edges": [],
        },
        headers=auth_headers(client, "admin"),
    )
    assert save.status_code == 200, save.text

    activate = client.post(
        f"/api/workflows/{wf_id}/activate",
        json={"acknowledged_estimate": True},
        headers=auth_headers(client, "admin"),
    )
    assert activate.status_code == 400
    errors = activate.json()["detail"]["errors"]
    assert any(
        "nodo raíz" in e.lower() and "siguiente paso" in e.lower()
        for e in errors
    ), f"expected trigger-no-successor error; got: {errors}"
