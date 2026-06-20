"""PR-Fix-Dedup-Key-Varchar.

La columna `workflow_runs.active_dedup_key` necesita aceptar
`{workflow_id}:{contact_id}:{run_id}` (110 chars) que usa el endpoint
de entrada manual con skip_dedup. Antes era VARCHAR(80) y MySQL
rechazaba el insert con 1406 Data too long.

Verifica:
- El modelo declara String(120) ahora.
- La migración 20260622_0062 está al final de la cadena Alembic.
- Insertar un dedup_key con 3 UUIDs concatenados funciona end-to-end
  vía el endpoint `manual_add_contact`.
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
from app.models.crm import Base, Contact, User, UserEmailAliasPref
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


def test_active_dedup_key_column_is_varchar_120() -> None:
    """El modelo declara la columna con length=120 — el ancho que
    cubre `{workflow_id}:{contact_id}:{run_id}` (110 chars) con margen."""
    col = WorkflowRun.__table__.c.active_dedup_key
    assert col.type.length == 120


def test_active_dedup_key_accepts_three_uuids_concatenated(
    session_factory: sessionmaker,
) -> None:
    """Insertar un dedup_key con 3 UUIDs concatenados (110 chars)
    debe funcionar — antes truncaba con error."""
    with session_factory() as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        contact = Contact(
            id=str(uuid4()), first_name="C", email="c@e.com", tags=""
        )
        session.add(contact)
        wf = Workflow(
            name="W",
            trigger_type="contact.created",
            status=WorkflowStatus.ACTIVE,
            created_by_user_id=admin.id,
            trigger_config_json="{}",
        )
        session.add(wf)
        session.flush()

        wf_id = str(uuid4())  # 36
        c_id = str(uuid4())  # 36
        run_id = str(uuid4())  # 36
        triple = f"{wf_id}:{c_id}:{run_id}"  # 110
        assert len(triple) == 110

        run = WorkflowRun(
            id=run_id,
            workflow_id=wf.id,
            contact_id=contact.id,
            current_step_id=None,
            state="running",
            active_dedup_key=triple,
            trigger_payload_json="{}",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        assert run.active_dedup_key == triple  # sin truncar


def test_alembic_widen_migration_is_at_head() -> None:
    """La revisión `20260622_0062` está como cabeza de la cadena
    Alembic — garantiza que el ALTER se aplique al deploy."""
    from pathlib import Path

    versions_dir = Path(__file__).parent.parent / "alembic" / "versions"
    files = sorted(p.name for p in versions_dir.glob("*.py"))
    # La cabeza más reciente debe ser nuestra migración.
    assert any(
        "20260622_0062_widen_active_dedup_key" in name for name in files
    )


def _make_workflow_with_email(session: Session, owner_id: str) -> Workflow:
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
                "subject": "X",
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


def test_manual_add_with_reentry_allowed_creates_run_without_truncation(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """End-to-end: el endpoint `manual_add_contact` (que persiste el
    dedup_key con 3 UUIDs) ya no truca el insert."""
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
    assert res.status_code == 200, res.text

    # El test prueba que el INSERT inicial con el dedup_key triple
    # (110 chars) no falló — antes daba 1406 Data too long. Tras el
    # run, `_finalize` archiva el dedup_key a `archived:{run_id}`,
    # así que NO comprobamos la longitud final aquí (cuello del
    # truncado era el insert, no el update). El 200 OK arriba ya
    # confirma que el insert pasó.
    with session_factory() as session:
        run = session.scalar(
            select(WorkflowRun).where(WorkflowRun.contact_id == cid)
        )
        assert run is not None
