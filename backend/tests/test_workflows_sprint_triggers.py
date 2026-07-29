"""Sprint Workflows — capa de triggers canónica.

Cubre: filtros de config aplicados en RUNTIME (antes solo estimator),
tenancy de workflows privados, productores nuevos (contact.updated /
lifecycle / task.*), triggers no disponibles, y el trigger custom
`contact.matches_conditions` (transición no-cumple → cumple).
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base, Contact, User, UserRole
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
    WorkflowTriggerMembership,
)
from app.workflows.dispatcher import process_event_inline
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


def _mk_workflow(
    session: Session,
    *,
    trigger_type: str,
    trigger_config: dict | None = None,
    owner_user_id: str | None = None,
    allow_reentry: bool = False,
) -> Workflow:
    wf = Workflow(
        name=f"wf-{trigger_type}",
        trigger_type=trigger_type,
        status=WorkflowStatus.ACTIVE,
        trigger_config_json=json.dumps(trigger_config or {}),
        owner_user_id=owner_user_id,
        allow_reentry=allow_reentry,
    )
    session.add(wf)
    session.flush()
    trig = WorkflowStep(
        workflow_id=wf.id, type="trigger", config_json="{}",
        position_x=0, position_y=0, is_entry=True,
    )
    exit_step = WorkflowStep(
        workflow_id=wf.id, type="exit_won", config_json="{}",
        position_x=0, position_y=100, is_entry=False,
    )
    session.add_all([trig, exit_step])
    session.flush()
    session.add(WorkflowEdge(
        workflow_id=wf.id, from_step_id=trig.id, to_step_id=exit_step.id,
        branch_label="default",
    ))
    session.commit()
    return wf


def _mk_contact(session: Session, **kw) -> Contact:
    c = Contact(first_name="T", email=f"{datetime.now(UTC).timestamp()}@x.com",
                tags="", **kw)
    session.add(c)
    session.commit()
    return c


def _runs(session: Session, wf_id: str) -> int:
    return len(list(session.scalars(
        select(WorkflowRun).where(WorkflowRun.workflow_id == wf_id)
    )))


# --- Runtime aplica los filtros de config (antes solo el estimator) ---


def test_dispatcher_applies_brevo_campaign_filter_runtime(session_factory):
    with session_factory() as s:
        wf = _mk_workflow(
            s, trigger_type="email.brevo.opened",
            trigger_config={"campaign_id": "48"},
        )
        contact = _mk_contact(s)
        process_event_inline(s, "email.brevo.opened", contact.id,
                             {"campaign_brevo_id": 99})
        s.commit()
        assert _runs(s, wf.id) == 0  # otra campaña → no dispara
        process_event_inline(s, "email.brevo.opened", contact.id,
                             {"campaign_brevo_id": 48})
        s.commit()
        assert _runs(s, wf.id) == 1  # la campaña del filtro → dispara


def test_dispatcher_applies_link_filter_runtime(session_factory):
    with session_factory() as s:
        wf = _mk_workflow(
            s, trigger_type="email.brevo.clicked",
            trigger_config={"link_url": "https://a.example"},
        )
        contact = _mk_contact(s)
        process_event_inline(s, "email.brevo.clicked", contact.id,
                             {"link": "https://otro.example"})
        assert _runs(s, wf.id) == 0
        process_event_inline(s, "email.brevo.clicked", contact.id,
                             {"link": "https://a.example"})
        assert _runs(s, wf.id) == 1


def test_private_workflow_only_fires_for_owner_contacts(session_factory):
    """Tenancy: workflow privado de A no dispara por contactos de B."""
    with session_factory() as s:
        user_a = s.scalar(select(User).where(User.role == UserRole.USER))
        user_b = s.scalar(select(User).where(User.role == UserRole.MANAGER))
        wf = _mk_workflow(
            s, trigger_type="contact.created", owner_user_id=user_a.id,
        )
        contact_b = _mk_contact(s, owner_user_id=user_b.id)
        process_event_inline(s, "contact.created", contact_b.id, {})
        assert _runs(s, wf.id) == 0
        contact_a = _mk_contact(s, owner_user_id=user_a.id)
        process_event_inline(s, "contact.created", contact_a.id, {})
        assert _runs(s, wf.id) == 1


# --- Productores nuevos ---


def test_contact_update_dispatches_updated_and_lifecycle(
    client, session_factory, monkeypatch
):
    events: list[tuple] = []
    monkeypatch.setattr(
        "app.workflows.dispatcher.dispatch_event",
        lambda session, event_type, contact_id, payload=None: events.append(
            (event_type, payload)
        ),
    )
    with session_factory() as s:
        contact = _mk_contact(s, commercial_status="new")
        cid = contact.id
    resp = client.patch(
        f"/api/contacts/{cid}",
        json={"commercial_status": "qualified"},
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 200, resp.text
    types = [e[0] for e in events]
    assert "contact.updated" in types
    assert "contact.lifecycle_changed" in types
    lifecycle = next(p for t, p in events if t == "contact.lifecycle_changed")
    assert lifecycle["from_status"] == "new"
    assert lifecycle["to_status"] == "qualified"
    updated = next(p for t, p in events if t == "contact.updated")
    assert "commercial_status" in updated["changed_fields"]


def test_task_create_and_complete_dispatch(client, session_factory, monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(
        "app.workflows.dispatcher.dispatch_event",
        lambda session, event_type, contact_id, payload=None: events.append(
            event_type
        ),
    )
    with session_factory() as s:
        contact = _mk_contact(s)
        cid = contact.id
    created = client.post(
        "/api/tasks",
        json={"title": "Llamar", "contact_id": cid},
        headers=auth_headers(client, "admin"),
    )
    assert created.status_code in (200, 201), created.text
    task_id = created.json()["id"]
    assert "task.created" in events
    done = client.post(
        f"/api/tasks/{task_id}/complete",
        headers=auth_headers(client, "admin"),
    )
    assert done.status_code == 200
    assert "task.completed" in events


# --- Triggers no disponibles ---


def test_activation_blocked_for_unavailable_trigger(client):
    res = client.post(
        "/api/workflows",
        json={"name": "opp", "trigger_type": "opportunity.won",
              "trigger_config": {}},
        headers=auth_headers(client, "admin"),
    )
    wf_id = res.json()["id"]
    client.put(
        f"/api/workflows/{wf_id}",
        json={
            "steps": [
                {"client_id": "t", "type": "trigger", "config": {},
                 "position_x": 0, "position_y": 0, "is_entry": True},
                {"client_id": "e", "type": "exit_won", "config": {},
                 "position_x": 0, "position_y": 100, "is_entry": False},
            ],
            "edges": [{"from_client_id": "t", "to_client_id": "e",
                       "branch_label": "default"}],
        },
        headers=auth_headers(client, "admin"),
    )
    res = client.post(
        f"/api/workflows/{wf_id}/activate",
        json={"acknowledged_estimate": True},
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 400
    assert "No disponible" in str(res.json())


def test_estimator_returns_null_for_unavailable_trigger(client):
    res = client.post(
        "/api/workflows",
        json={"name": "opp2", "trigger_type": "task.overdue",
              "trigger_config": {}},
        headers=auth_headers(client, "admin"),
    )
    wf_id = res.json()["id"]
    est = client.post(
        f"/api/workflows/{wf_id}/cost-estimate",
        headers=auth_headers(client, "admin"),
    )
    assert est.status_code == 200
    body = est.json()
    assert body["estimated_runs_30d"] is None
    assert body["estimated_emails_30d"] is None


def test_catalog_marks_unavailable_triggers(client):
    res = client.get(
        "/api/workflows/_catalog", headers=auth_headers(client, "admin")
    )
    assert res.status_code == 200
    triggers = {t["type"]: t for t in res.json()["triggers"]}
    assert triggers["opportunity.won"]["available"] is False
    assert triggers["contact.date_field"]["available"] is False
    assert triggers["contact.matches_conditions"]["available"] is True
    assert triggers["email.brevo.opened"]["available"] is True


# --- validate_tree acepta el IR del builder (E2) ---


def test_validate_tree_accepts_builder_ir():
    from app.workflows.conditions import validate_tree

    tree = {
        "operator": "AND",
        "children": [
            {"type": "rule", "field": "email", "comparator": "contains",
             "value": "x"},
        ],
    }
    assert validate_tree(tree) == []


# --- Trigger custom contact.matches_conditions ---


def test_matches_conditions_fires_on_transition_only(session_factory):
    from app.workflows.scheduler import _evaluate_matches_conditions

    with session_factory() as s:
        wf = _mk_workflow(
            s, trigger_type="contact.matches_conditions",
            trigger_config={"filter": {
                "operator": "AND",
                "children": [{"type": "rule", "field": "lead_score",
                              "comparator": "gte", "value": 70}],
            }},
        )
        # Contacto que NO cumple → primer sweep no dispara nada.
        low = _mk_contact(s, lead_score=10)
        now = datetime.now(UTC)
        started = _evaluate_matches_conditions(s, now)
        s.commit()
        assert started == 0
        assert _runs(s, wf.id) == 0
        # El contacto PASA a cumplir → sweep dispara 1 run + membresía.
        low.lead_score = 90
        s.commit()
        started = _evaluate_matches_conditions(s, datetime.now(UTC))
        s.commit()
        assert started == 1
        assert _runs(s, wf.id) == 1
        rows = list(s.scalars(select(WorkflowTriggerMembership).where(
            WorkflowTriggerMembership.workflow_id == wf.id
        )))
        assert len(rows) == 1
        # Sigue cumpliendo → NO re-dispara.
        started = _evaluate_matches_conditions(s, datetime.now(UTC))
        s.commit()
        assert started == 0
        assert _runs(s, wf.id) == 1


def test_matches_conditions_reentry_semantics(session_factory):
    from app.workflows.scheduler import _evaluate_matches_conditions

    with session_factory() as s:
        wf = _mk_workflow(
            s, trigger_type="contact.matches_conditions",
            allow_reentry=True,
            trigger_config={"filter": {
                "operator": "AND",
                "children": [{"type": "rule", "field": "lead_score",
                              "comparator": "gte", "value": 70}],
            }},
        )
        c = _mk_contact(s, lead_score=90)
        _evaluate_matches_conditions(s, datetime.now(UTC))
        s.commit()
        assert _runs(s, wf.id) == 1
        # Sale del set → con allow_reentry la fila de membresía se borra.
        c.lead_score = 5
        s.commit()
        _evaluate_matches_conditions(s, datetime.now(UTC))
        s.commit()
        # Vuelve a entrar → re-dispara.
        c.lead_score = 95
        s.commit()
        _evaluate_matches_conditions(s, datetime.now(UTC))
        s.commit()
        assert _runs(s, wf.id) == 2


def test_activation_seeds_baseline_without_firing(client, session_factory):
    """Los contactos que YA cumplen al activar quedan sembrados en la
    membresía y NO disparan; solo transiciones futuras."""
    from app.workflows.scheduler import _evaluate_matches_conditions

    with session_factory() as s:
        _mk_contact(s, lead_score=99)  # ya cumple antes de activar
    res = client.post(
        "/api/workflows",
        json={
            "name": "custom", "trigger_type": "contact.matches_conditions",
            "trigger_config": {"filter": {
                "operator": "AND",
                "children": [{"type": "rule", "field": "lead_score",
                              "comparator": "gte", "value": 70}],
            }},
        },
        headers=auth_headers(client, "admin"),
    )
    wf_id = res.json()["id"]
    client.put(
        f"/api/workflows/{wf_id}",
        json={
            "steps": [
                {"client_id": "t", "type": "trigger", "config": {},
                 "position_x": 0, "position_y": 0, "is_entry": True},
                {"client_id": "e", "type": "exit_won", "config": {},
                 "position_x": 0, "position_y": 100, "is_entry": False},
            ],
            "edges": [{"from_client_id": "t", "to_client_id": "e",
                       "branch_label": "default"}],
        },
        headers=auth_headers(client, "admin"),
    )
    act = client.post(
        f"/api/workflows/{wf_id}/activate",
        json={"acknowledged_estimate": True},
        headers=auth_headers(client, "admin"),
    )
    assert act.status_code == 200, act.text
    with session_factory() as s:
        # Sembrado…
        rows = list(s.scalars(select(WorkflowTriggerMembership).where(
            WorkflowTriggerMembership.workflow_id == wf_id
        )))
        assert len(rows) == 1
        # …y el sweep posterior NO dispara para el ya-cumplidor.
        assert _evaluate_matches_conditions(s, datetime.now(UTC)) == 0
        assert _runs(s, wf_id) == 0
