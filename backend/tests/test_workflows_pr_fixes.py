"""Sprint PR-Fixes-Workflows-Editor — tests para los fixes."""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base, Contact
from app.models.workflows import (
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


def _create_workflow(
    client: TestClient,
    *,
    name: str = "Test",
    trigger_type: str = "contact.created",
    trigger_config: dict | None = None,
) -> str:
    res = client.post(
        "/api/workflows",
        json={
            "name": name,
            "trigger_type": trigger_type,
            "trigger_config": trigger_config or {},
        },
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


# ---------------------------------------------------------------------
# Bug 4 — estimator
# ---------------------------------------------------------------------


def test_estimator_returns_zero_for_event_triggers(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Trigger `contact.created` no aplica retroactivo — el contador
    `matching_contacts_now` debe ser 0 aunque haya contactos."""
    # Seed contactos previos.
    with session_factory() as session:
        for i in range(5):
            session.add(
                Contact(first_name=f"X{i}", email=f"x{i}@ex.com", tags="")
            )
        session.commit()

    wf_id = _create_workflow(client, trigger_type="contact.created")
    res = client.post(
        f"/api/workflows/{wf_id}/cost-estimate",
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["matching_contacts_now"] == 0


def test_estimator_returns_count_for_state_triggers(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Trigger `contact.date_field` SÍ cuenta contactos activos
    (los que cumplen el field hoy)."""
    with session_factory() as session:
        for i in range(3):
            session.add(
                Contact(first_name=f"Y{i}", email=f"y{i}@ex.com", tags="")
            )
        session.commit()

    wf_id = _create_workflow(client, trigger_type="contact.date_field")
    res = client.post(
        f"/api/workflows/{wf_id}/cost-estimate",
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["matching_contacts_now"] == 3


def test_estimator_event_trigger_projects_from_history(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Para triggers de evento, `estimated_runs_30d` proyecta basándose
    en runs históricos del workflow en los últimos 30 días."""
    wf_id = _create_workflow(client, trigger_type="contact.created")
    # Insert runs históricos (~últimos 30d).
    with session_factory() as session:
        c = Contact(first_name="A", email="a@ex.com", tags="")
        session.add(c)
        session.commit()
        now = datetime.now(UTC)
        for i in range(4):
            session.add(
                WorkflowRun(
                    workflow_id=wf_id,
                    contact_id=c.id,
                    state=WorkflowRunState.COMPLETED,
                    active_dedup_key=f"archived:{i}",
                    trigger_payload_json="{}",
                    split_buckets_json="{}",
                    started_at=now - timedelta(days=5 + i),
                )
            )
        # Uno fuera de la ventana (40 días atrás) — no cuenta.
        session.add(
            WorkflowRun(
                workflow_id=wf_id,
                contact_id=c.id,
                state=WorkflowRunState.COMPLETED,
                active_dedup_key="archived:old",
                trigger_payload_json="{}",
                split_buckets_json="{}",
                started_at=now - timedelta(days=40),
            )
        )
        session.commit()

    res = client.post(
        f"/api/workflows/{wf_id}/cost-estimate",
        headers=auth_headers(client, "admin"),
    )
    body = res.json()
    assert body["matching_contacts_now"] == 0
    assert body["estimated_runs_30d"] == 4


# ---------------------------------------------------------------------
# Bug 3 — action_set_custom_field requires field + value
# ---------------------------------------------------------------------


def test_set_custom_field_step_persists_field_and_value(
    client: TestClient,
) -> None:
    """Si el operador setea {field: 'sector', value: 'industrial'},
    el step se persiste con ambos en el config."""
    wf_id = _create_workflow(client)
    res = client.put(
        f"/api/workflows/{wf_id}",
        json={
            "steps": [
                {
                    "client_id": "s1",
                    "type": "trigger",
                    "config": {},
                    "position_x": 0,
                    "position_y": 0,
                    "is_entry": True,
                },
                {
                    "client_id": "s2",
                    "type": "action_set_custom_field",
                    "config": {"field": "sector", "value": "industrial"},
                    "position_x": 100,
                    "position_y": 100,
                    "is_entry": False,
                },
            ],
            "edges": [
                {
                    "from_client_id": "s1",
                    "to_client_id": "s2",
                    "branch_label": "default",
                },
            ],
        },
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    set_step = next(
        s for s in body["steps"] if s["type"] == "action_set_custom_field"
    )
    assert set_step["config"]["field"] == "sector"
    assert set_step["config"]["value"] == "industrial"


# ---------------------------------------------------------------------
# Improvement #8 — action_send_email persiste template_id
# ---------------------------------------------------------------------


def test_send_email_step_persists_template_id(
    client: TestClient,
) -> None:
    wf_id = _create_workflow(client)
    res = client.put(
        f"/api/workflows/{wf_id}",
        json={
            "steps": [
                {
                    "client_id": "s1",
                    "type": "trigger",
                    "config": {},
                    "position_x": 0,
                    "position_y": 0,
                    "is_entry": True,
                },
                {
                    "client_id": "s2",
                    "type": "action_send_email",
                    "config": {
                        "template_id": "tpl-uuid-1",
                        "from_alias": "info@bomedia.net",
                    },
                    "position_x": 100,
                    "position_y": 100,
                    "is_entry": False,
                },
            ],
            "edges": [
                {
                    "from_client_id": "s1",
                    "to_client_id": "s2",
                    "branch_label": "default",
                },
            ],
        },
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    send = next(
        s for s in body["steps"] if s["type"] == "action_send_email"
    )
    assert send["config"]["template_id"] == "tpl-uuid-1"
    assert send["config"]["from_alias"] == "info@bomedia.net"


# ---------------------------------------------------------------------
# Bug 1 — humanize hooks are server-side too? They live in frontend
# only (workflowsHumanize.ts). Pero el resumen narrativo del dry-run
# se construye server-side, así que verificamos que el trigger tiene
# label humano allí.
# ---------------------------------------------------------------------


def test_workflow_detail_exposes_trigger_type_for_humanize(
    client: TestClient,
) -> None:
    """El frontend necesita `trigger_type` en el detalle para componer
    el label humano del trigger node."""
    wf_id = _create_workflow(client, trigger_type="email.brevo.opened")
    res = client.get(
        f"/api/workflows/{wf_id}",
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["trigger_type"] == "email.brevo.opened"


# ---------------------------------------------------------------------
# Improvement #7 — trigger_config persiste sub-parámetros
# ---------------------------------------------------------------------


def test_trigger_subparams_persisted(
    client: TestClient,
) -> None:
    """Si el operador crea un workflow con sub-params en el
    trigger_config (campaña concreta, link específico, etc.), se
    persisten y leen en el detail."""
    wf_id = _create_workflow(
        client,
        trigger_type="email.brevo.opened",
        trigger_config={"campaign_id": "br-camp-42"},
    )
    res = client.get(
        f"/api/workflows/{wf_id}",
        headers=auth_headers(client, "admin"),
    )
    body = res.json()
    assert body["trigger_config"]["campaign_id"] == "br-camp-42"


# ---------------------------------------------------------------------
# Backend smoke: workflow status no se rompe al haber action_send_email
# con template_id apuntando a un id inexistente.
# ---------------------------------------------------------------------


def test_dry_run_handles_missing_template_gracefully(
    client: TestClient, session_factory: sessionmaker
) -> None:
    wf_id = _create_workflow(client)
    client.put(
        f"/api/workflows/{wf_id}",
        json={
            "steps": [
                {
                    "client_id": "s1",
                    "type": "trigger",
                    "config": {},
                    "position_x": 0,
                    "position_y": 0,
                    "is_entry": True,
                },
                {
                    "client_id": "s2",
                    "type": "action_send_email",
                    "config": {"template_id": "missing-tpl"},
                    "position_x": 0,
                    "position_y": 0,
                    "is_entry": False,
                },
                {
                    "client_id": "s3",
                    "type": "exit_natural",
                    "config": {},
                    "position_x": 0,
                    "position_y": 0,
                    "is_entry": False,
                },
            ],
            "edges": [
                {
                    "from_client_id": "s1",
                    "to_client_id": "s2",
                    "branch_label": "default",
                },
                {
                    "from_client_id": "s2",
                    "to_client_id": "s3",
                    "branch_label": "default",
                },
            ],
        },
        headers=auth_headers(client, "admin"),
    )
    with session_factory() as session:
        c = Contact(first_name="T", email="t@ex.com", tags="")
        session.add(c)
        session.commit()
        contact_id = c.id

    res = client.post(
        f"/api/workflows/{wf_id}/dry-run",
        json={"contact_id": contact_id},
        headers=auth_headers(client, "admin"),
    )
    # Dry-run no crashea aunque la plantilla no exista — describe el
    # paso pero el motor real lo marca skipped en runtime.
    assert res.status_code == 200
    assert res.json()["error"] is None


def test_workflow_default_workflow_status() -> None:
    """Smoke: el enum WorkflowStatus tiene los valores esperados."""
    assert WorkflowStatus.DRAFT.value == "draft"
    assert WorkflowStatus.ACTIVE.value == "active"
