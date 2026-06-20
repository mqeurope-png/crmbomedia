"""PR-Fix-Añadir-Manual-Workflow.

El endpoint POST `/api/workflows/{id}/add-contact/{cid}` antes usaba
`start_run` que delegaba en `_entry_step` → `is_entry=True`. Bart
reportó "El workflow no pudo arrancar (revisa que tenga step de
entrada)" para un workflow que SÍ tenía trigger marcado is_entry=1
correctamente — el mensaje engañaba.

Refactor: nuevo helper `start_manual_run` localiza el trigger por
TYPE (no por flag), busca su sucesor, y arranca el run directo en él
saltándose el ciclo del trigger-anchor. Si el trigger no tiene
sucesor, error específico 422 con mensaje claro.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import patch
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
    User,
    UserEmailAliasPref,
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
from app.workflows.engine import (
    ManualStartError,
    start_manual_run,
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


def _make_workflow_with_email(session: Session, owner_id: str) -> Workflow:
    """trigger → action_send_email → exit_won (imita el `bbbb` real de Bart)."""
    wf = Workflow(
        name="bbbb",
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
                "subject": "Hola",
                "body_html": "<p>x</p>",
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
        type="exit_won",
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


# ---------------------------------------------------------------------
# Helper unit-level
# ---------------------------------------------------------------------


def test_manual_workflow_run_starts_at_trigger_successor(
    session_factory: sessionmaker,
) -> None:
    """Verifica que el run arranca en el SUCESOR del trigger
    (action_send_email), no en el trigger mismo."""
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
        # Seed default alias por si advance_run llega al email.
        session.add(
            UserEmailAliasPref(
                user_id=admin.id,
                alias_email="info@bomedia.net",
                is_allowed=True,
                is_default=True,
                gmail_display_name="Default",
            )
        )
        wf = _make_workflow_with_email(session, admin.id)
        session.refresh(contact)

        send_step = session.scalar(
            select(WorkflowStep).where(
                WorkflowStep.workflow_id == wf.id,
                WorkflowStep.type == "action_send_email",
            )
        )
        assert send_step is not None

        run = start_manual_run(
            session, wf, contact, actor_user_id=admin.id
        )
        assert run is not None
        # Arranca directamente en el sucesor del trigger.
        assert run.current_step_id == send_step.id


def test_manual_workflow_run_workflow_empty_returns_specific_error(
    session_factory: sessionmaker,
) -> None:
    """Workflow degenerado solo con trigger sin sucesor → ManualStartError
    con código `workflow_empty` y mensaje específico."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()),
            first_name="C",
            email="c@e.com",
            tags="",
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
        # Solo trigger, ninguna edge saliente.
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
        session.refresh(contact)

        with pytest.raises(ManualStartError) as exc_info:
            start_manual_run(session, wf, contact, actor_user_id=admin.id)
        assert exc_info.value.code == "workflow_empty"
        assert "vacío" in exc_info.value.message.lower()


def test_manual_workflow_run_records_trigger_anchor_history(
    session_factory: sessionmaker,
) -> None:
    """El run debe persistir history del trigger con `manual_entry=True`
    para auditoría."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()),
            first_name="C",
            email="c@e.com",
            tags="",
            owner_user_id=admin.id,
        )
        session.add(contact)
        session.add(
            UserEmailAliasPref(
                user_id=admin.id,
                alias_email="info@bomedia.net",
                is_allowed=True,
                is_default=True,
                gmail_display_name="Default",
            )
        )
        wf = _make_workflow_with_email(session, admin.id)
        session.refresh(contact)

        run = start_manual_run(
            session, wf, contact, actor_user_id=admin.id
        )
        session.commit()

        trigger_history = session.scalar(
            select(WorkflowRunHistory).where(
                WorkflowRunHistory.run_id == run.id,
                WorkflowRunHistory.step_type == "trigger",
            )
        )
        assert trigger_history is not None
        assert trigger_history.status == "ok"
        result = json.loads(trigger_history.result_json or "{}")
        assert result.get("manual_entry") is True


def test_manual_workflow_run_workflow_not_active(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()), first_name="C", email="c@e.com", tags=""
        )
        session.add(contact)
        wf = _make_workflow_with_email(session, admin.id)
        wf.status = WorkflowStatus.PAUSED
        session.commit()
        session.refresh(contact)

        with pytest.raises(ManualStartError) as exc_info:
            start_manual_run(session, wf, contact, actor_user_id=admin.id)
        assert exc_info.value.code == "workflow_not_active"


# ---------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------


def test_manual_add_endpoint_creates_run_and_advances(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """End-to-end del endpoint: el run se crea, se avanza, y el
    send_email se invoca (mockeado)."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()),
            first_name="C",
            email="c@e.com",
            tags="",
            owner_user_id=admin.id,
        )
        session.add(contact)
        session.add(
            UserEmailAliasPref(
                user_id=admin.id,
                alias_email="info@bomedia.net",
                is_allowed=True,
                is_default=True,
                gmail_display_name="Default",
            )
        )
        wf = _make_workflow_with_email(session, admin.id)
        session.commit()
        cid = contact.id
        wid = wf.id

    with patch("app.integrations.gmail.service.send_email") as mock_send:
        mock_send.return_value = type("M", (), {"id": "m1"})()
        res = client.post(
            f"/api/workflows/{wid}/add-contact/{cid}",
            headers=auth_headers(client, "admin"),
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "run_id" in body
    # send_email fue invocado, prueba de que el run avanzó del
    # sucesor del trigger.
    assert mock_send.call_count == 1


def test_manual_add_endpoint_records_audit_with_manual_marker(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """El audit log de la entrada manual debe llevar la marca
    `manual_entry=True` para auditoría posterior."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()),
            first_name="C",
            email="c@e.com",
            tags="",
            owner_user_id=admin.id,
        )
        session.add(contact)
        session.add(
            UserEmailAliasPref(
                user_id=admin.id,
                alias_email="info@bomedia.net",
                is_allowed=True,
                is_default=True,
                gmail_display_name="D",
            )
        )
        wf = _make_workflow_with_email(session, admin.id)
        session.commit()
        cid = contact.id
        wid = wf.id

    with patch("app.integrations.gmail.service.send_email") as mock_send:
        mock_send.return_value = type("M", (), {"id": "m1"})()
        res = client.post(
            f"/api/workflows/{wid}/add-contact/{cid}",
            headers=auth_headers(client, "admin"),
        )
    assert res.status_code == 200

    with session_factory() as session:
        log = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "workflow.contact_added_manually"
            )
        )
        assert log is not None
        meta = json.loads(log.metadata_json or "{}")
        assert meta.get("manual_entry") is True


def test_manual_add_endpoint_workflow_empty_returns_422(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Workflow degenerado → 422 con mensaje específico (no el genérico
    "no entry step" que confundía)."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()), first_name="C", email="c@e.com", tags=""
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
        cid = contact.id
        wid = wf.id

    res = client.post(
        f"/api/workflows/{wid}/add-contact/{cid}",
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 422
    assert "vacío" in res.json()["detail"].lower()


def test_manual_add_endpoint_forbidden_for_user_role(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Rol `user` no puede usar el endpoint — solo admin/manager."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()), first_name="C", email="c@e.com", tags=""
        )
        session.add(contact)
        wf = _make_workflow_with_email(session, admin.id)
        session.commit()
        cid = contact.id
        wid = wf.id

    res = client.post(
        f"/api/workflows/{wid}/add-contact/{cid}",
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 403


def test_manual_add_endpoint_works_even_if_is_entry_flag_wrong(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """REGRESSION del bug original de Bart: con un workflow cuyo
    `is_entry` esté mal seteado (ningún step tiene is_entry=True), el
    endpoint manual debe seguir funcionando porque busca el trigger
    por TYPE, no por flag."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()),
            first_name="C",
            email="c@e.com",
            tags="",
            owner_user_id=admin.id,
        )
        session.add(contact)
        session.add(
            UserEmailAliasPref(
                user_id=admin.id,
                alias_email="info@bomedia.net",
                is_allowed=True,
                is_default=True,
                gmail_display_name="D",
            )
        )
        wf = _make_workflow_with_email(session, admin.id)
        # Simula el bug: borra is_entry de TODOS los steps.
        for s in session.scalars(
            select(WorkflowStep).where(WorkflowStep.workflow_id == wf.id)
        ):
            s.is_entry = False
        session.commit()
        cid = contact.id
        wid = wf.id

    with patch("app.integrations.gmail.service.send_email") as mock_send:
        mock_send.return_value = type("M", (), {"id": "m1"})()
        res = client.post(
            f"/api/workflows/{wid}/add-contact/{cid}",
            headers=auth_headers(client, "admin"),
        )
    assert res.status_code == 200, res.text


def test_manual_add_endpoint_allows_reentry_skip(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """El admin puede añadir manualmente aunque el contacto ya tenga
    un run activo del mismo workflow — `skip_dedup` por diseño."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()),
            first_name="C",
            email="c@e.com",
            tags="",
            owner_user_id=admin.id,
        )
        session.add(contact)
        session.add(
            UserEmailAliasPref(
                user_id=admin.id,
                alias_email="info@bomedia.net",
                is_allowed=True,
                is_default=True,
                gmail_display_name="D",
            )
        )
        wf = _make_workflow_with_email(session, admin.id)
        # Seed un run activo previo con el dedup_key viejo.
        prev_run = WorkflowRun(
            id=str(uuid4()),
            workflow_id=wf.id,
            contact_id=contact.id,
            current_step_id=None,
            state=WorkflowRunState.RUNNING,
            active_dedup_key=f"{wf.id}:{contact.id}",
            trigger_payload_json="{}",
            started_at=datetime.now(UTC),
        )
        session.add(prev_run)
        session.commit()
        cid = contact.id
        wid = wf.id

    with patch("app.integrations.gmail.service.send_email") as mock_send:
        mock_send.return_value = type("M", (), {"id": "m1"})()
        res = client.post(
            f"/api/workflows/{wid}/add-contact/{cid}",
            headers=auth_headers(client, "admin"),
        )
    assert res.status_code == 200, res.text
    with session_factory() as session:
        runs = list(
            session.scalars(
                select(WorkflowRun).where(WorkflowRun.contact_id == cid)
            )
        )
        assert len(runs) == 2  # el viejo + el manual nuevo
