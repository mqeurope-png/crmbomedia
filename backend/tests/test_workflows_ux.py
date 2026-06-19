"""Sprint UX-Workflows-Editor — tests."""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base, Contact
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowStatus,
    WorkflowStep,
)
from app.workflows.hashing import (
    compute_exact_hash,
    compute_similarity_hash,
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


def _create_workflow(client: TestClient, name: str = "Test") -> str:
    res = client.post(
        "/api/workflows",
        json={"name": name, "trigger_type": "contact.created"},
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _save_definition(
    client: TestClient,
    workflow_id: str,
    steps: list[dict],
    edges: list[dict],
) -> dict:
    res = client.put(
        f"/api/workflows/{workflow_id}",
        json={"steps": steps, "edges": edges},
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------
# Templates endpoints
# ---------------------------------------------------------------------


def test_list_templates_returns_3_seed(client: TestClient) -> None:
    res = client.get(
        "/api/workflows/_templates", headers=auth_headers(client, "user")
    )
    assert res.status_code == 200
    items = res.json()
    ids = {t["id"] for t in items}
    assert ids == {"onboarding-lead-nuevo", "cumpleanos", "followup-presupuesto"}


def test_template_endpoint_clones_correctly(client: TestClient) -> None:
    res = client.post(
        "/api/workflows/_templates/cumpleanos/use",
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "draft"
    assert body["trigger_type"] == "contact.date_field"
    assert "(copia)" in body["name"]
    # Steps cloned: trigger + send_email + exit_natural = 3.
    assert len(body["steps"]) == 3
    types = {s["type"] for s in body["steps"]}
    assert "action_send_email" in types
    assert any(s["is_entry"] for s in body["steps"])


def test_template_unknown_returns_404(client: TestClient) -> None:
    res = client.post(
        "/api/workflows/_templates/does-not-exist/use",
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------
# Duplicate workflow
# ---------------------------------------------------------------------


def test_duplicate_workflow_copies_steps_and_edges(
    client: TestClient,
) -> None:
    src_id = _create_workflow(client, name="Source")
    saved = _save_definition(
        client,
        src_id,
        steps=[
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
                "type": "action_add_tag",
                "config": {"tag": "Hot"},
                "position_x": 100,
                "position_y": 100,
                "is_entry": False,
            },
            {
                "client_id": "s3",
                "type": "exit_natural",
                "config": {},
                "position_x": 200,
                "position_y": 200,
                "is_entry": False,
            },
        ],
        edges=[
            {"from_client_id": "s1", "to_client_id": "s2", "branch_label": "default"},
            {"from_client_id": "s2", "to_client_id": "s3", "branch_label": "default"},
        ],
    )
    assert len(saved["steps"]) == 3
    assert len(saved["edges"]) == 2

    res = client.post(
        f"/api/workflows/{src_id}/duplicate",
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 201, res.text
    dup = res.json()
    assert dup["id"] != src_id
    assert dup["status"] == "draft"
    assert "(copia)" in dup["name"]
    assert len(dup["steps"]) == 3
    assert len(dup["edges"]) == 2
    # Misma topología → mismo exact hash.
    assert dup["definition_hash"] == saved["definition_hash"]


# ---------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------


def test_dry_run_returns_actions_without_committing(
    client: TestClient, session_factory: sessionmaker
) -> None:
    src_id = _create_workflow(client)
    _save_definition(
        client,
        src_id,
        steps=[
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
                "type": "action_add_tag",
                "config": {"tag": "DryRunTag"},
                "position_x": 100,
                "position_y": 100,
                "is_entry": False,
            },
            {
                "client_id": "s3",
                "type": "exit_natural",
                "config": {},
                "position_x": 200,
                "position_y": 200,
                "is_entry": False,
            },
        ],
        edges=[
            {"from_client_id": "s1", "to_client_id": "s2", "branch_label": "default"},
            {"from_client_id": "s2", "to_client_id": "s3", "branch_label": "default"},
        ],
    )
    with session_factory() as session:
        contact = Contact(
            first_name="Test", email="t@ex.com", tags=""
        )
        session.add(contact)
        session.commit()
        contact_id = contact.id

    res = client.post(
        f"/api/workflows/{src_id}/dry-run",
        json={"contact_id": contact_id},
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    types = [s["step_type"] for s in body["steps"]]
    assert types == ["trigger", "action_add_tag", "exit_natural"]
    # El contacto NO debe tener el tag — dry-run no commitea.
    with session_factory() as session:
        contact = session.get(Contact, contact_id)
        assert "DryRunTag" not in (contact.tags or "")


def test_dry_run_unknown_contact_returns_error_field(
    client: TestClient,
) -> None:
    src_id = _create_workflow(client)
    res = client.post(
        f"/api/workflows/{src_id}/dry-run",
        json={"contact_id": "nonexistent"},
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["error"] == "contact_not_found"


# ---------------------------------------------------------------------
# definition_hash detección de duplicados
# ---------------------------------------------------------------------


def _build_workflow_in_db(
    session: Session, *, name: str, tag: str
) -> Workflow:
    wf = Workflow(
        name=name,
        trigger_type="contact.created",
        trigger_config_json="{}",
        cancellation_events_json='["contact.unsubscribed"]',
        status=WorkflowStatus.DRAFT,
    )
    session.add(wf)
    session.flush()
    s1 = WorkflowStep(
        workflow_id=wf.id,
        type="trigger",
        config_json="{}",
        is_entry=True,
    )
    s2 = WorkflowStep(
        workflow_id=wf.id,
        type="action_add_tag",
        config_json=json.dumps({"tag": tag}),
        is_entry=False,
    )
    s3 = WorkflowStep(
        workflow_id=wf.id,
        type="exit_natural",
        config_json="{}",
        is_entry=False,
    )
    session.add_all([s1, s2, s3])
    session.flush()
    session.add_all(
        [
            WorkflowEdge(
                workflow_id=wf.id,
                from_step_id=s1.id,
                to_step_id=s2.id,
                branch_label="default",
            ),
            WorkflowEdge(
                workflow_id=wf.id,
                from_step_id=s2.id,
                to_step_id=s3.id,
                branch_label="default",
            ),
        ]
    )
    session.commit()
    session.refresh(wf)
    return wf


def test_definition_hash_detects_exact_duplicate(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        a = _build_workflow_in_db(session, name="A", tag="Hot")
        b = _build_workflow_in_db(session, name="B", tag="Hot")
        a_steps = list(
            session.scalars(
                select(WorkflowStep).where(WorkflowStep.workflow_id == a.id)
            )
        )
        a_edges = list(
            session.scalars(
                select(WorkflowEdge).where(WorkflowEdge.workflow_id == a.id)
            )
        )
        b_steps = list(
            session.scalars(
                select(WorkflowStep).where(WorkflowStep.workflow_id == b.id)
            )
        )
        b_edges = list(
            session.scalars(
                select(WorkflowEdge).where(WorkflowEdge.workflow_id == b.id)
            )
        )
        assert compute_exact_hash(a, a_steps, a_edges) == compute_exact_hash(
            b, b_steps, b_edges
        )


def test_definition_hash_detects_similar_workflow(
    session_factory: sessionmaker,
) -> None:
    """Mismo skeleton (trigger + add_tag + exit) pero distintos tags →
    misma similarity hash, distinta exact hash."""
    with session_factory() as session:
        a = _build_workflow_in_db(session, name="A", tag="Hot")
        b = _build_workflow_in_db(session, name="B", tag="Cold")
        a_steps = list(
            session.scalars(
                select(WorkflowStep).where(WorkflowStep.workflow_id == a.id)
            )
        )
        a_edges = list(
            session.scalars(
                select(WorkflowEdge).where(WorkflowEdge.workflow_id == a.id)
            )
        )
        b_steps = list(
            session.scalars(
                select(WorkflowStep).where(WorkflowStep.workflow_id == b.id)
            )
        )
        b_edges = list(
            session.scalars(
                select(WorkflowEdge).where(WorkflowEdge.workflow_id == b.id)
            )
        )
        assert compute_exact_hash(a, a_steps, a_edges) != compute_exact_hash(
            b, b_steps, b_edges
        )
        assert compute_similarity_hash(
            a, a_steps, a_edges
        ) == compute_similarity_hash(b, b_steps, b_edges)


def test_activate_rejects_exact_duplicate(client: TestClient) -> None:
    """Crear A → activar OK. Crear B con misma definición → activate
    rechaza con 409."""
    a_id = _create_workflow(client, name="A")
    _save_definition(
        client,
        a_id,
        steps=[
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
                "type": "action_add_tag",
                "config": {"tag": "X"},
                "position_x": 0,
                "position_y": 0,
                "is_entry": False,
            },
        ],
        edges=[
            {"from_client_id": "s1", "to_client_id": "s2", "branch_label": "default"},
        ],
    )
    res = client.post(
        f"/api/workflows/{a_id}/activate",
        json={"acknowledged_estimate": True},
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200, res.text

    b_id = _create_workflow(client, name="B")
    _save_definition(
        client,
        b_id,
        steps=[
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
                "type": "action_add_tag",
                "config": {"tag": "X"},
                "position_x": 0,
                "position_y": 0,
                "is_entry": False,
            },
        ],
        edges=[
            {"from_client_id": "s1", "to_client_id": "s2", "branch_label": "default"},
        ],
    )
    res = client.post(
        f"/api/workflows/{b_id}/activate",
        json={"acknowledged_estimate": True},
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "duplicate_exact"


# ---------------------------------------------------------------------
# display_name persistence
# ---------------------------------------------------------------------


def test_display_name_persists_on_step(client: TestClient) -> None:
    wf_id = _create_workflow(client)
    saved = _save_definition(
        client,
        wf_id,
        steps=[
            {
                "client_id": "s1",
                "type": "trigger",
                "config": {},
                "position_x": 0,
                "position_y": 0,
                "is_entry": True,
                "display_name": "Punto de partida",
            },
            {
                "client_id": "s2",
                "type": "exit_natural",
                "config": {},
                "position_x": 0,
                "position_y": 0,
                "is_entry": False,
                "display_name": "Adiós",
            },
        ],
        edges=[
            {"from_client_id": "s1", "to_client_id": "s2", "branch_label": "default"},
        ],
    )
    names = {s["display_name"] for s in saved["steps"]}
    assert "Punto de partida" in names
    assert "Adiós" in names

    # GET → display_name persiste tras roundtrip.
    res = client.get(
        f"/api/workflows/{wf_id}",
        headers=auth_headers(client, "admin"),
    )
    names = {s["display_name"] for s in res.json()["steps"]}
    assert names == {"Punto de partida", "Adiós"}


# ---------------------------------------------------------------------
# WorkflowDetail incluye duplicate_warnings
# ---------------------------------------------------------------------


def test_detail_surfaces_similar_warning(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Dos workflows con mismo skeleton pero distintos tags → GET del
    segundo expone `duplicate_warnings` con kind=similar."""
    a_id = _create_workflow(client, name="A")
    _save_definition(
        client,
        a_id,
        steps=[
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
                "type": "action_add_tag",
                "config": {"tag": "Skel1"},
                "position_x": 0,
                "position_y": 0,
                "is_entry": False,
            },
        ],
        edges=[
            {"from_client_id": "s1", "to_client_id": "s2", "branch_label": "default"},
        ],
    )
    b_id = _create_workflow(client, name="B")
    _save_definition(
        client,
        b_id,
        steps=[
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
                "type": "action_add_tag",
                "config": {"tag": "Skel2"},
                "position_x": 0,
                "position_y": 0,
                "is_entry": False,
            },
        ],
        edges=[
            {"from_client_id": "s1", "to_client_id": "s2", "branch_label": "default"},
        ],
    )
    res = client.get(
        f"/api/workflows/{b_id}", headers=auth_headers(client, "admin")
    )
    warnings = res.json()["duplicate_warnings"]
    assert any(w["kind"] == "similar" and w["workflow_id"] == a_id for w in warnings)
