"""Sprint Workflows Bloque 1 — tests del motor.

Cubre:
- Modelos y migración (create_all funciona).
- Engine: start_run + advance_run con steps simples.
- Reentry guard.
- Condition tree.
- Variable interpolation Jinja2.
- Wait_time genera wake_at + scheduler resume.
- Cancel propaga via cancellation_events.
- API CRUD + activate + cost-estimate.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

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
    WorkflowExitKind,
    WorkflowRun,
    WorkflowRunHistory,
    WorkflowRunState,
    WorkflowStatus,
    WorkflowStep,
)
from app.workflows import conditions, variables
from app.workflows.dispatcher import process_event_inline
from app.workflows.engine import advance_run, start_run
from app.workflows.scheduler import run_tick
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
# Helpers
# ---------------------------------------------------------------------


def _user_id(session: Session, role: UserRole) -> str:
    return session.scalar(select(User.id).where(User.role == role))


def _seed_contact(
    session: Session,
    *,
    email: str = "lead@example.com",
    first_name: str = "Lead",
    owner_id: str | None = None,
    tags: str = "",
    lead_score: int = 0,
) -> Contact:
    contact = Contact(
        first_name=first_name,
        email=email,
        tags=tags,
        lead_score=lead_score,
        owner_user_id=owner_id,
    )
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact


def _seed_workflow(
    session: Session,
    *,
    name: str = "Test",
    trigger_type: str = "contact.created",
    trigger_filter: dict | None = None,
    cancellation_events: list[str] | None = None,
    status: WorkflowStatus = WorkflowStatus.ACTIVE,
) -> Workflow:
    trigger_cfg = {}
    if trigger_filter:
        trigger_cfg["filter"] = trigger_filter
    cancellation = json.dumps(
        cancellation_events or ["contact.unsubscribed"]
    )
    workflow = Workflow(
        name=name,
        trigger_type=trigger_type,
        trigger_config_json=json.dumps(trigger_cfg),
        cancellation_events_json=cancellation,
        status=status,
    )
    session.add(workflow)
    session.commit()
    session.refresh(workflow)
    return workflow


def _add_step(
    session: Session,
    workflow: Workflow,
    *,
    step_type: str,
    config: dict | None = None,
    is_entry: bool = False,
) -> WorkflowStep:
    step = WorkflowStep(
        workflow_id=workflow.id,
        type=step_type,
        config_json=json.dumps(config or {}),
        is_entry=is_entry,
    )
    session.add(step)
    session.commit()
    session.refresh(step)
    return step


def _add_edge(
    session: Session,
    workflow: Workflow,
    *,
    from_step: WorkflowStep,
    to_step: WorkflowStep,
    branch_label: str = "default",
) -> WorkflowEdge:
    edge = WorkflowEdge(
        workflow_id=workflow.id,
        from_step_id=from_step.id,
        to_step_id=to_step.id,
        branch_label=branch_label,
    )
    session.add(edge)
    session.commit()
    return edge


# ---------------------------------------------------------------------
# Engine — basic flow
# ---------------------------------------------------------------------


def test_engine_advance_runs_linear_chain(
    session_factory: sessionmaker,
) -> None:
    """Trigger → add_tag → exit_natural."""
    with session_factory() as session:
        contact = _seed_contact(session)
        workflow = _seed_workflow(session, trigger_type="contact.created")
        entry = _add_step(session, workflow, step_type="trigger", is_entry=True)
        tag = _add_step(
            session,
            workflow,
            step_type="action_add_tag",
            config={"tag": "WorkflowTouched"},
        )
        exit_ = _add_step(session, workflow, step_type="exit_natural")
        _add_edge(session, workflow, from_step=entry, to_step=tag)
        _add_edge(session, workflow, from_step=tag, to_step=exit_)

        run = start_run(session, workflow, contact)
        assert run is not None
        advance_run(session, run.id)
        session.commit()

        contact = session.get(Contact, contact.id)
        assert "WorkflowTouched" in (contact.tags or "")
        run = session.get(WorkflowRun, run.id)
        assert run.state == WorkflowRunState.COMPLETED
        assert run.exit_kind == WorkflowExitKind.NATURAL


def test_reentry_guard_blocks_second_start(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        contact = _seed_contact(session)
        workflow = _seed_workflow(session)
        entry = _add_step(session, workflow, step_type="trigger", is_entry=True)
        wait = _add_step(
            session,
            workflow,
            step_type="wait_time",
            config={"duration_minutes": 60},
        )
        _add_edge(session, workflow, from_step=entry, to_step=wait)

        first = start_run(session, workflow, contact)
        assert first is not None
        advance_run(session, first.id)
        session.commit()

        # El primer run quedó en waiting. Segunda entrada debe bloquearse.
        second = start_run(session, workflow, contact)
        assert second is None


def test_reentry_allowed_when_workflow_opt_in(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        contact = _seed_contact(session)
        workflow = _seed_workflow(session)
        workflow.allow_reentry = True
        session.commit()
        entry = _add_step(session, workflow, step_type="trigger", is_entry=True)
        wait = _add_step(
            session,
            workflow,
            step_type="wait_time",
            config={"duration_minutes": 60},
        )
        _add_edge(session, workflow, from_step=entry, to_step=wait)

        first = start_run(session, workflow, contact)
        advance_run(session, first.id)
        session.commit()
        second = start_run(session, workflow, contact)
        assert second is not None
        assert second.id != first.id


def test_condition_branches_true_false(
    session_factory: sessionmaker,
) -> None:
    """Condition step: contacto con lead_score > 50 va por rama true."""
    with session_factory() as session:
        hot = _seed_contact(session, email="hot@example.com", lead_score=80)
        cold = _seed_contact(session, email="cold@example.com", lead_score=10)
        workflow = _seed_workflow(session)
        entry = _add_step(session, workflow, step_type="trigger", is_entry=True)
        cond = _add_step(
            session,
            workflow,
            step_type="condition",
            config={
                "condition": {
                    "field": "contact.lead_score",
                    "op": "gt",
                    "value": 50,
                }
            },
        )
        hot_tag = _add_step(
            session,
            workflow,
            step_type="action_add_tag",
            config={"tag": "Hot"},
        )
        cold_tag = _add_step(
            session,
            workflow,
            step_type="action_add_tag",
            config={"tag": "Cold"},
        )
        exit_ = _add_step(session, workflow, step_type="exit_natural")
        _add_edge(session, workflow, from_step=entry, to_step=cond)
        _add_edge(
            session, workflow, from_step=cond, to_step=hot_tag, branch_label="true"
        )
        _add_edge(
            session, workflow, from_step=cond, to_step=cold_tag, branch_label="false"
        )
        _add_edge(session, workflow, from_step=hot_tag, to_step=exit_)
        _add_edge(session, workflow, from_step=cold_tag, to_step=exit_)

        for contact in (hot, cold):
            run = start_run(session, workflow, contact)
            assert run is not None
            advance_run(session, run.id)
        session.commit()

        hot = session.get(Contact, hot.id)
        cold = session.get(Contact, cold.id)
        assert "Hot" in (hot.tags or "")
        assert "Cold" in (cold.tags or "")


def test_wait_time_persists_wake_at_and_resumes(
    session_factory: sessionmaker,
) -> None:
    """wait_time → run pasa a WAITING. Scheduler con wake_at <= now lo
    resume."""
    with session_factory() as session:
        contact = _seed_contact(session)
        workflow = _seed_workflow(session)
        entry = _add_step(session, workflow, step_type="trigger", is_entry=True)
        wait = _add_step(
            session,
            workflow,
            step_type="wait_time",
            config={"duration_minutes": 1},
        )
        tag = _add_step(
            session,
            workflow,
            step_type="action_add_tag",
            config={"tag": "Resumed"},
        )
        _add_edge(session, workflow, from_step=entry, to_step=wait)
        _add_edge(session, workflow, from_step=wait, to_step=tag)

        run = start_run(session, workflow, contact)
        advance_run(session, run.id)
        session.commit()

        run = session.get(WorkflowRun, run.id)
        assert run.state == WorkflowRunState.WAITING
        assert run.wake_at is not None

        # Force wake_at al pasado para que el scheduler lo recoja YA.
        run.wake_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

        stats = run_tick(session, limit=10)
        assert stats["runs_resumed"] >= 1

        contact = session.get(Contact, contact.id)
        assert "Resumed" in (contact.tags or "")


def test_cancel_for_contact_on_unsubscribe(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        contact = _seed_contact(session)
        workflow = _seed_workflow(
            session, cancellation_events=["contact.unsubscribed"]
        )
        entry = _add_step(session, workflow, step_type="trigger", is_entry=True)
        wait = _add_step(
            session,
            workflow,
            step_type="wait_time",
            config={"duration_minutes": 60},
        )
        _add_edge(session, workflow, from_step=entry, to_step=wait)

        run = start_run(session, workflow, contact)
        advance_run(session, run.id)
        session.commit()
        assert session.get(WorkflowRun, run.id).state == WorkflowRunState.WAITING

        process_event_inline(
            session,
            "contact.unsubscribed",
            contact.id,
            {"event_type": "contact.unsubscribed"},
        )
        session.commit()
        # cancel_run en estado WAITING cierra directo (no requiere
        # boundary).
        run = session.get(WorkflowRun, run.id)
        assert run.state == WorkflowRunState.CANCELLED


def test_dispatcher_starts_workflow_on_event(
    session_factory: sessionmaker,
) -> None:
    """process_event_inline + start_run + advance encadenados."""
    with session_factory() as session:
        contact = _seed_contact(session, tags="FESPA-2026")
        workflow = _seed_workflow(
            session,
            trigger_type="contact.created",
            trigger_filter={
                "field": "contact.tags",
                "op": "contains",
                "value": "FESPA-2026",
            },
        )
        entry = _add_step(session, workflow, step_type="trigger", is_entry=True)
        tag = _add_step(
            session,
            workflow,
            step_type="action_add_tag",
            config={"tag": "Onboarding"},
        )
        _add_edge(session, workflow, from_step=entry, to_step=tag)

        process_event_inline(
            session,
            "contact.created",
            contact.id,
            {"source": "test"},
        )
        session.commit()

        runs = list(
            session.scalars(
                select(WorkflowRun).where(
                    WorkflowRun.contact_id == contact.id
                )
            )
        )
        assert len(runs) == 1
        contact = session.get(Contact, contact.id)
        assert "Onboarding" in (contact.tags or "")


def test_dispatcher_skips_workflow_when_filter_doesnt_match(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        contact = _seed_contact(session, tags="OTHER")
        workflow = _seed_workflow(
            session,
            trigger_filter={
                "field": "contact.tags",
                "op": "contains",
                "value": "FESPA",
            },
        )
        _add_step(session, workflow, step_type="trigger", is_entry=True)

        process_event_inline(
            session,
            "contact.created",
            contact.id,
            {"source": "test"},
        )
        session.commit()
        runs = list(
            session.scalars(
                select(WorkflowRun).where(
                    WorkflowRun.contact_id == contact.id
                )
            )
        )
        assert runs == []


# ---------------------------------------------------------------------
# Condition evaluator unit tests
# ---------------------------------------------------------------------


def test_condition_evaluator_basic_ops(session_factory: sessionmaker) -> None:
    with session_factory() as session:
        contact = _seed_contact(
            session, lead_score=75, tags="vip,fespa", first_name="Anna"
        )
        ctx = conditions.EvalContext(session=session, contact=contact)

        assert conditions.evaluate(
            {"field": "contact.lead_score", "op": "gt", "value": 50}, ctx
        ) is True
        assert conditions.evaluate(
            {"field": "contact.lead_score", "op": "lt", "value": 50}, ctx
        ) is False
        assert conditions.evaluate(
            {"field": "contact.tags", "op": "contains", "value": "vip"}, ctx
        ) is True
        assert conditions.evaluate(
            {
                "op": "AND",
                "children": [
                    {"field": "contact.lead_score", "op": "gte", "value": 75},
                    {"field": "contact.first_name", "op": "eq", "value": "Anna"},
                ],
            },
            ctx,
        ) is True
        assert conditions.evaluate(
            {"field": "contact.last_name", "op": "empty"}, ctx
        ) is True


def test_condition_evaluator_unknown_field_returns_false(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        contact = _seed_contact(session)
        ctx = conditions.EvalContext(session=session, contact=contact)
        # Anti-injection: campo no whitelisteado nunca matchea.
        assert conditions.evaluate(
            {"field": "contact.password", "op": "eq", "value": "anything"},
            ctx,
        ) is False


# ---------------------------------------------------------------------
# Variable interpolation
# ---------------------------------------------------------------------


def test_variable_interpolation_basic(session_factory: sessionmaker) -> None:
    with session_factory() as session:
        contact = _seed_contact(
            session, first_name="Bart", email="bart@bomedia.net"
        )
        ctx = variables.build_context(session=session, contact=contact)
        rendered = variables.render(
            "Hola {{ contact.first_name }}, tu email es {{ contact.email }}",
            ctx,
        )
        assert "Hola Bart" in rendered
        assert "bart@bomedia.net" in rendered


def test_variable_interpolation_html_autoescape(
    session_factory: sessionmaker,
) -> None:
    """En modo HTML, las variables se escapan automáticamente — un
    contacto con `<script>` en el nombre no inyecta el script."""
    with session_factory() as session:
        contact = _seed_contact(
            session, first_name="<script>alert(1)</script>"
        )
        ctx = variables.build_context(session=session, contact=contact)
        rendered = variables.render(
            "Hola {{ contact.first_name }}", ctx, is_html=True
        )
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered


def test_variable_company_null_renders_empty(
    session_factory: sessionmaker,
) -> None:
    """Contacto sin empresa: `{{ company.name }}` → "" sin crashear."""
    with session_factory() as session:
        contact = _seed_contact(session)
        ctx = variables.build_context(session=session, contact=contact)
        rendered = variables.render("Empresa: {{ company.name }}", ctx)
        assert rendered == "Empresa: "


# ---------------------------------------------------------------------
# API CRUD
# ---------------------------------------------------------------------


def test_api_create_workflow(client: TestClient) -> None:
    headers = auth_headers(client, "admin")
    res = client.post(
        "/api/workflows",
        json={
            "name": "Onboarding FESPA",
            "trigger_type": "contact.created",
            "trigger_config": {
                "filter": {
                    "field": "contact.tags",
                    "op": "contains",
                    "value": "FESPA-2026",
                }
            },
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["name"] == "Onboarding FESPA"
    assert body["status"] == "draft"
    # Entry step seed automático.
    assert len(body["steps"]) == 1
    assert body["steps"][0]["type"] == "trigger"
    assert body["steps"][0]["is_entry"] is True


def test_api_activate_other_users_workflow_forbidden(client: TestClient) -> None:
    """PR-Hotfix-Workflows-Pipelines-Permisos. Activar es owner+admin
    (antes admin-only — bloqueaba al comercial que creaba su propio
    workflow). El 403 ahora se gatilla cuando OTRO user intenta
    activar un workflow privado ajeno."""
    headers_mgr = auth_headers(client, "manager")
    res = client.post(
        "/api/workflows",
        json={"name": "X", "trigger_type": "contact.created"},
        headers=headers_mgr,
    )
    wf_id = res.json()["id"]
    # Otro user no-admin no puede activar el workflow privado del manager.
    res = client.post(
        f"/api/workflows/{wf_id}/activate",
        json={"acknowledged_estimate": True},
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 403


def test_api_catalog_lists_triggers_and_steps(
    client: TestClient,
) -> None:
    res = client.get(
        "/api/workflows/_catalog", headers=auth_headers(client, "user")
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["triggers"]) >= 15
    assert len(body["steps"]) >= 15
    assert "contact.first_name" in body["fields"]
    assert "contact.full_name" in body["variables"]


def test_api_contact_runs_endpoint(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        contact = _seed_contact(session)
        workflow = _seed_workflow(session)
        _add_step(session, workflow, step_type="trigger", is_entry=True)
        run = start_run(session, workflow, contact)
        advance_run(session, run.id)
        session.commit()
        contact_id = contact.id
        workflow_id = workflow.id

    res = client.get(
        f"/api/workflows/_contacts/{contact_id}/runs",
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200
    runs = res.json()
    assert len(runs) == 1
    assert runs[0]["workflow_id"] == workflow_id


def test_api_cost_estimate(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        _seed_contact(session, email="a@x.test", tags="FESPA-2026")
        _seed_contact(session, email="b@x.test", tags="OTHER")

    headers = auth_headers(client, "admin")
    res = client.post(
        "/api/workflows",
        json={
            "name": "Est",
            "trigger_type": "contact.created",
            "trigger_config": {
                "filter": {
                    "field": "contact.tags",
                    "op": "contains",
                    "value": "FESPA-2026",
                }
            },
        },
        headers=headers,
    )
    wf_id = res.json()["id"]
    res = client.post(
        f"/api/workflows/{wf_id}/cost-estimate", headers=headers
    )
    assert res.status_code == 200
    body = res.json()
    # PR-Fixes #4. `contact.created` es un trigger de evento puntual:
    # `matching_contacts_now` siempre 0 — el workflow solo se aplica
    # a futuros, no a contactos ya existentes que cumplían el filtro.
    assert body["matching_contacts_now"] == 0


# ---------------------------------------------------------------------
# History audit
# ---------------------------------------------------------------------


def test_run_history_appended_per_step(
    session_factory: sessionmaker,
) -> None:
    """Cada step ejecutado deja una fila en workflow_run_history."""
    with session_factory() as session:
        contact = _seed_contact(session)
        workflow = _seed_workflow(session)
        entry = _add_step(session, workflow, step_type="trigger", is_entry=True)
        tag = _add_step(
            session,
            workflow,
            step_type="action_add_tag",
            config={"tag": "A"},
        )
        exit_ = _add_step(session, workflow, step_type="exit_natural")
        _add_edge(session, workflow, from_step=entry, to_step=tag)
        _add_edge(session, workflow, from_step=tag, to_step=exit_)
        run = start_run(session, workflow, contact)
        advance_run(session, run.id)
        session.commit()
        history = list(
            session.scalars(
                select(WorkflowRunHistory)
                .where(WorkflowRunHistory.run_id == run.id)
                .order_by(WorkflowRunHistory.executed_at.asc())
            )
        )
        types = [h.step_type for h in history]
        # trigger → action_add_tag → exit_natural.
        assert types == ["trigger", "action_add_tag", "exit_natural"]
        assert all(h.status == "ok" for h in history)
