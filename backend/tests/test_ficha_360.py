"""Sprint Ficha 360 — llamadas, timeline y trigger manual."""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    ActivityEvent,
    Base,
    CallLog,
    Contact,
    Note,
    Task,
    User,
    UserRole,
)
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
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(Contact(first_name="Sergio", email="s@x.com", tags=""))
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _cid(sf) -> str:
    with sf() as s:
        return s.scalar(select(Contact.id))


def _mk_manual_wf(s: Session, owner: str | None = None) -> Workflow:
    wf = Workflow(
        name="manual-wf", trigger_type="contact.manual",
        status=WorkflowStatus.ACTIVE, trigger_config_json="{}",
        owner_user_id=owner,
    )
    s.add(wf)
    s.flush()
    t = WorkflowStep(workflow_id=wf.id, type="trigger", config_json="{}",
                     position_x=0, position_y=0, is_entry=True)
    e = WorkflowStep(workflow_id=wf.id, type="exit_won", config_json="{}",
                     position_x=0, position_y=100, is_entry=False)
    s.add_all([t, e])
    s.flush()
    s.add(WorkflowEdge(workflow_id=wf.id, from_step_id=t.id,
                       to_step_id=e.id, branch_label="default"))
    s.commit()
    return wf


# --- Bloque 1: call logs ---


def test_call_log_create_with_all_actions_success(client, session_factory):
    cid = _cid(session_factory)
    with session_factory() as s:
        from app.models.crm import Pipeline, PipelineStage
        p = Pipeline(name="Ventas")
        s.add(p)
        s.flush()
        s.add(PipelineStage(pipeline_id=p.id, name="Nuevo", position=0))
        wf = _mk_manual_wf(s)
        s.commit()
        pid, wfid = p.id, wf.id
    r = client.post(f"/api/contacts/{cid}/calls", json={
        "result_code": "interested", "subject": "Modelo 6090",
        "notes": "Pide presupuesto", "duration_bucket": "5_to_30min",
        "actions": {
            "pipeline_change": {"pipeline_id": pid},
            "lead_score_delta": 10,
            "follow_up_task": {"title": "Rellamar"},
            "trigger_workflow_ids": [wfid],
        },
    }, headers=auth_headers(client, "user"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["follow_up_task_id"] is not None
    assert body["actions_executed"] == [
        "pipeline_change", "lead_score_delta", "follow_up_task",
        f"workflow:{wfid}",
    ]
    with session_factory() as s:
        assert s.get(Contact, cid).lead_score == 10
        assert s.scalar(select(WorkflowRun.id).where(
            WorkflowRun.workflow_id == wfid)) is not None
        from app.models.crm import AuditLog
        acts = [a.action for a in s.scalars(select(AuditLog))]
        assert "call_log.created" in acts
        assert "call_log.lead_score_adjusted" in acts
        assert "workflow.run_manual" in acts


def test_call_log_result_code_other_requires_custom(client, session_factory):
    cid = _cid(session_factory)
    r = client.post(f"/api/contacts/{cid}/calls",
                    json={"result_code": "other"},
                    headers=auth_headers(client, "user"))
    assert r.status_code == 400
    ok = client.post(f"/api/contacts/{cid}/calls",
                     json={"result_code": "other", "result_custom": "Fax"},
                     headers=auth_headers(client, "user"))
    assert ok.status_code == 201


def test_call_log_custom_max_150_chars(client, session_factory):
    cid = _cid(session_factory)
    r = client.post(f"/api/contacts/{cid}/calls",
                    json={"result_code": "other", "result_custom": "x" * 151},
                    headers=auth_headers(client, "user"))
    assert r.status_code == 422  # pydantic max_length


def test_call_log_delete_only_owner_or_admin(client, session_factory):
    cid = _cid(session_factory)
    created = client.post(f"/api/contacts/{cid}/calls",
                          json={"result_code": "contacted"},
                          headers=auth_headers(client, "user"))
    call_id = created.json()["id"]
    deny = client.delete(f"/api/contacts/{cid}/calls/{call_id}",
                         headers=auth_headers(client, "manager"))
    assert deny.status_code == 403
    ok = client.delete(f"/api/contacts/{cid}/calls/{call_id}",
                       headers=auth_headers(client, "admin"))
    assert ok.status_code == 200
    with session_factory() as s:
        assert s.get(CallLog, call_id) is None


# --- Bloque 2: timeline ---


def _seed_timeline(sf) -> str:
    with sf() as s:
        cid = s.scalar(select(Contact.id))
        uid = s.scalar(select(User.id).where(User.role == UserRole.USER))
        now = datetime.now(UTC)
        s.add(Note(contact_id=cid, body="nota vieja", source="manual",
                   author_user_id=uid))
        s.add(Task(title="Tarea", contact_id=cid, assigned_user_id=uid,
                   created_by_user_id=uid))
        for i, (etype, camp) in enumerate([
            ("email.opened", 48), ("email.opened", 48),
            ("email.clicked", 48), ("email.opened", 99),
        ]):
            s.add(ActivityEvent(
                contact_id=cid, system="brevo", account_id="main",
                external_id=f"tl-{i}", event_type=etype,
                campaign_brevo_id=camp,
                occurred_at=now - timedelta(days=i + 1),
            ))
        s.add(CallLog(contact_id=cid, user_id=uid, result_code="contacted",
                      called_at=now))
        s.commit()
        return cid


def test_timeline_returns_union_from_all_sources(client, session_factory):
    cid = _seed_timeline(session_factory)
    r = client.get(f"/api/contacts/{cid}/timeline",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 200
    types = {e["type"] for e in r.json()["items"]}
    assert {"call", "note", "task", "brevo"} <= types


def test_timeline_brevo_grouped_by_campaign_with_counts(client, session_factory):
    cid = _seed_timeline(session_factory)
    r = client.get(f"/api/contacts/{cid}/timeline?types=brevo",
                   headers=auth_headers(client, "user"))
    items = r.json()["items"]
    assert len(items) == 2  # 2 campañas → 2 eventos agrupados
    by_camp = {e["metadata"]["campaign_brevo_id"]: e for e in items}
    assert by_camp[48]["metadata"]["opens"] == 2
    assert by_camp[48]["metadata"]["clicks"] == 1
    assert by_camp[99]["metadata"]["opens"] == 1


def test_timeline_sort_asc_and_desc(client, session_factory):
    cid = _seed_timeline(session_factory)
    desc = client.get(f"/api/contacts/{cid}/timeline",
                      headers=auth_headers(client, "user")).json()["items"]
    asc = client.get(f"/api/contacts/{cid}/timeline?sort=asc",
                     headers=auth_headers(client, "user")).json()["items"]
    assert [e["id"] for e in asc] == [e["id"] for e in reversed(desc)]


def test_timeline_filter_by_types(client, session_factory):
    cid = _seed_timeline(session_factory)
    r = client.get(f"/api/contacts/{cid}/timeline?types=call,note",
                   headers=auth_headers(client, "user"))
    assert {e["type"] for e in r.json()["items"]} <= {"call", "note"}


def test_notes_order_by_effective_date_desc(client, session_factory):
    cid = _cid(session_factory)
    with session_factory() as s:
        old_ext = Note(contact_id=cid, body="importada-2020", source="agilecrm",
                       external_created_at=datetime(2020, 1, 1, tzinfo=UTC))
        s.add(old_ext)
        s.add(Note(contact_id=cid, body="nativa-hoy", source="manual"))
        s.commit()
    r = client.get(f"/api/contacts/{cid}/notes",
                   headers=auth_headers(client, "user"))
    bodies = [n["content"] for n in r.json()]
    assert bodies.index("nativa-hoy") < bodies.index("importada-2020")


# --- Bloque 3: trigger manual ---


def test_workflow_manual_trigger_only_runs_when_endpoint_called(
    client, session_factory
):
    from app.workflows.dispatcher import process_event_inline

    cid = _cid(session_factory)
    with session_factory() as s:
        wf = _mk_manual_wf(s)
        wfid = wf.id
        # Ningún evento del bus lo dispara…
        process_event_inline(s, "contact.created", cid, {})
        process_event_inline(s, "email.brevo.opened", cid, {})
        s.commit()
        assert s.scalar(select(WorkflowRun.id).where(
            WorkflowRun.workflow_id == wfid)) is None
    # …solo el endpoint manual.
    r = client.post(f"/api/contacts/{cid}/workflows/{wfid}/run",
                    headers=auth_headers(client, "user"))
    assert r.status_code == 200, r.text
    assert r.json()["run_id"]


def test_workflow_manual_endpoint_respects_tenancy(client, session_factory):
    cid = _cid(session_factory)
    with session_factory() as s:
        manager_id = s.scalar(select(User.id).where(User.role == UserRole.MANAGER))
        wf = _mk_manual_wf(s, owner=manager_id)
        wfid = wf.id
    # user normal no ve el workflow privado del manager → 404.
    deny = client.post(f"/api/contacts/{cid}/workflows/{wfid}/run",
                       headers=auth_headers(client, "user"))
    assert deny.status_code == 404
    ok = client.post(f"/api/contacts/{cid}/workflows/{wfid}/run",
                     headers=auth_headers(client, "manager"))
    assert ok.status_code == 200
    listing = client.get("/api/workflows/manual",
                         headers=auth_headers(client, "user")).json()
    assert all(w["id"] != wfid for w in listing)


def test_workflow_manual_estimator_returns_dash(client):
    res = client.post("/api/workflows",
                      json={"name": "m", "trigger_type": "contact.manual",
                            "trigger_config": {}},
                      headers=auth_headers(client, "admin"))
    wfid = res.json()["id"]
    est = client.post(f"/api/workflows/{wfid}/cost-estimate",
                      headers=auth_headers(client, "admin"))
    assert est.status_code == 200
    assert est.json()["estimated_runs_30d"] is None
