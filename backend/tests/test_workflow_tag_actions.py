"""PR-Hotfix-Ficha-360 Bug 2 — las acciones add_tag/remove_tag del
motor de workflows escriben en la tabla canónica `contact_tags`, no
solo en el CSV legacy `contacts.tags` (el "cisma de tags": la pestaña
Tags lee de la M:N y el tag no aparecía aunque el run reportaba ok).
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app  # noqa: F401 — registra todos los modelos
from app.models.crm import (
    AuditLog,
    Base,
    Contact,
    ContactTag,
    Tag,
)
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowRunState,
    WorkflowStatus,
    WorkflowStep,
)
from app.workflows.engine import advance_run, start_manual_run, start_run
from tests._test_helpers import seed_test_users


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


def _seed_contact(session: Session, *, tags: str = "") -> Contact:
    contact = Contact(first_name="Josep", email="josep@example.com", tags=tags)
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact


def _tag_workflow(
    session: Session, *, step_type: str, tag: str = "testeando"
) -> Workflow:
    """trigger → action_(add|remove)_tag → exit_natural."""
    workflow = Workflow(
        name=f"wf-{step_type}",
        trigger_type="contact.created",
        trigger_config_json="{}",
        cancellation_events_json="[]",
        status=WorkflowStatus.ACTIVE,
    )
    session.add(workflow)
    session.flush()
    trigger = WorkflowStep(
        workflow_id=workflow.id, type="trigger", config_json="{}",
        is_entry=True,
    )
    action = WorkflowStep(
        workflow_id=workflow.id, type=step_type,
        config_json=json.dumps({"tag": tag}),
    )
    exit_ = WorkflowStep(
        workflow_id=workflow.id, type="exit_natural", config_json="{}",
    )
    session.add_all([trigger, action, exit_])
    session.flush()
    session.add_all([
        WorkflowEdge(workflow_id=workflow.id, from_step_id=trigger.id,
                     to_step_id=action.id, branch_label="default"),
        WorkflowEdge(workflow_id=workflow.id, from_step_id=action.id,
                     to_step_id=exit_.id, branch_label="default"),
    ])
    session.commit()
    return workflow


def _run_to_completion(session: Session, workflow: Workflow, contact: Contact):
    run = start_run(session, workflow, contact)
    assert run is not None
    advance_run(session, run.id)
    session.commit()
    return run


def _link_count(session: Session, contact_id: str, tag_name: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(ContactTag)
            .join(Tag, Tag.id == ContactTag.tag_id)
            .where(
                ContactTag.contact_id == contact_id,
                Tag.name_normalized == tag_name.strip().lower(),
            )
        )
        or 0
    )


def test_workflow_add_tag_action_inserts_into_contact_tags_table(
    session_factory: sessionmaker,
) -> None:
    """El caso de Bart: workflow con add_tag `testeando` ejecutado sobre
    un contacto → el tag debe quedar en `contact_tags` (lo que lee la
    pestaña Tags), no solo en el CSV."""
    with session_factory() as session:
        contact = _seed_contact(session)
        workflow = _tag_workflow(session, step_type="action_add_tag")
        run = _run_to_completion(session, workflow, contact)

        assert session.get(type(run), run.id).state == WorkflowRunState.COMPLETED
        assert _link_count(session, contact.id, "testeando") == 1
        link = session.scalar(select(ContactTag).where(
            ContactTag.contact_id == contact.id
        ))
        assert link.source == "workflow"
        # El CSV legacy se mantiene por compat (conditions/gdpr lo leen).
        assert "testeando" in (session.get(Contact, contact.id).tags or "")
        # Audit del patrón PR #263.
        audit = session.scalar(select(AuditLog).where(
            AuditLog.action == "contact_tag.added",
            AuditLog.target_id == contact.id,
        ))
        assert audit is not None
        meta = json.loads(audit.metadata_json or "{}")
        assert meta["via"] == "workflow"
        assert meta["tag_name"] == "testeando"
        assert meta["workflow_id"] == workflow.id


def test_workflow_add_tag_action_creates_tag_if_missing(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        contact = _seed_contact(session)
        workflow = _tag_workflow(session, step_type="action_add_tag")
        assert session.scalar(select(func.count()).select_from(Tag)) == 0
        _run_to_completion(session, workflow, contact)

        tag = session.scalar(select(Tag))
        assert tag is not None
        assert tag.name == "testeando"
        assert tag.name_normalized == "testeando"
        # Color determinista asignado (paleta compartida con la ficha).
        assert tag.color


def test_workflow_add_tag_action_reuses_existing_tag_case_insensitive(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        contact = _seed_contact(session)
        session.add(Tag(name="Testeando", name_normalized="testeando"))
        session.commit()
        workflow = _tag_workflow(session, step_type="action_add_tag")
        _run_to_completion(session, workflow, contact)

        assert session.scalar(select(func.count()).select_from(Tag)) == 1
        assert _link_count(session, contact.id, "testeando") == 1


def test_workflow_add_tag_action_idempotent_no_duplicate(
    session_factory: sessionmaker,
) -> None:
    """Ejecutar el workflow 2 veces (entrada manual, como hizo Bart)
    no duplica ni el tag ni el link ni el CSV."""
    with session_factory() as session:
        contact = _seed_contact(session)
        workflow = _tag_workflow(session, step_type="action_add_tag")
        for _ in range(2):
            run = start_manual_run(session, workflow, contact)
            advance_run(session, run.id)
            session.commit()

        assert session.scalar(select(func.count()).select_from(Tag)) == 1
        assert _link_count(session, contact.id, "testeando") == 1
        csv = (session.get(Contact, contact.id).tags or "").split(",")
        assert csv.count("testeando") == 1


def test_workflow_remove_tag_action_deletes_from_contact_tags(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        contact = _seed_contact(session, tags="testeando")
        tag = Tag(name="testeando", name_normalized="testeando")
        session.add(tag)
        session.flush()
        session.add(ContactTag(contact_id=contact.id, tag_id=tag.id,
                               source="manual"))
        session.commit()
        workflow = _tag_workflow(session, step_type="action_remove_tag")
        _run_to_completion(session, workflow, contact)

        assert _link_count(session, contact.id, "testeando") == 0
        # El Tag maestro NO se borra (otros contactos pueden usarlo).
        assert session.scalar(select(func.count()).select_from(Tag)) == 1
        assert "testeando" not in (session.get(Contact, contact.id).tags or "")
        audit = session.scalar(select(AuditLog).where(
            AuditLog.action == "contact_tag.removed",
            AuditLog.target_id == contact.id,
        ))
        assert audit is not None
        assert json.loads(audit.metadata_json or "{}")["via"] == "workflow"
