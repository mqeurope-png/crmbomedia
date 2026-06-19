"""PR-Fixes-Workflows-Pase-5 — tests para los 4 bugs UX nuevos.

Cubre:
- Bug 1: action_set_custom_field acepta campos nativos del contacto
  (lead_score, commercial_status) además de custom_fields.
- Bug 3: action_create_task calcula due_at en modo relativo + weekday.
- Bug 4: action_send_email permite subject_override sobre plantilla.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.email_templates.models import EmailTemplate
from app.main import app
from app.models.crm import Base, Contact, User
from app.models.workflows import WorkflowRun, WorkflowStep
from app.workflows.steps import (
    _resolve_workflow_task_due_at,
    _step_create_task,
    _step_send_email,
    _step_set_custom_field,
)
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


@pytest.fixture()
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _make_step(
    config: dict, *, step_type: str = "action_set_custom_field"
) -> WorkflowStep:
    return WorkflowStep(
        id="s1",
        workflow_id="w1",
        type=step_type,
        position_x=0,
        position_y=0,
        is_entry=False,
        config_json=json.dumps(config),
    )


def _make_run() -> WorkflowRun:
    return WorkflowRun(
        id="r1",
        workflow_id="w1",
        contact_id="c1",
        trigger_payload_json="{}",
    )


# ---------------------------------------------------------------------
# Bug 1 — action_set_custom_field con campos nativos
# ---------------------------------------------------------------------


def test_set_custom_field_native_field_update_lead_score(
    session_factory: sessionmaker,
) -> None:
    """lead_score es nativo + numérico: el step debe coercer el string
    "85" del config a int y persistir en la columna."""
    with session_factory() as session:
        contact = Contact(
            first_name="C",
            email="c@ex.com",
            tags="",
            lead_score=50,
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)

        step = _make_step({"field": "lead_score", "value": "85"})
        result = _step_set_custom_field(session, _make_run(), step, contact)
        assert result.status == "ok"
        assert result.result["new"] == 85
        assert contact.lead_score == 85
        # Importante: el custom_fields JSON queda intacto.
        assert contact.custom_fields in (None, "")


def test_set_custom_field_native_field_update_lifecycle_status(
    session_factory: sessionmaker,
) -> None:
    """commercial_status es nativo enum. El step debe persistir el
    nuevo estado y devolver el viejo en el result."""
    with session_factory() as session:
        contact = Contact(
            first_name="C",
            email="c@ex.com",
            tags="",
            commercial_status="new",
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)

        step = _make_step(
            {"field": "commercial_status", "value": "qualified"}
        )
        result = _step_set_custom_field(session, _make_run(), step, contact)
        assert result.status == "ok"
        assert result.result["old"] == "new"
        assert result.result["new"] == "qualified"
        assert contact.commercial_status == "qualified"


def test_set_custom_field_required_native_field_empty_fails(
    session_factory: sessionmaker,
) -> None:
    """Vaciar `first_name` (required) → el step falla con mensaje
    explícito en vez de pisar la columna con NULL."""
    with session_factory() as session:
        contact = Contact(
            first_name="Bart", email="c@ex.com", tags=""
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)

        step = _make_step({"field": "first_name", "value": ""})
        result = _step_set_custom_field(session, _make_run(), step, contact)
        assert result.status == "failed"
        assert "required_field_empty" in (result.error or "")
        assert contact.first_name == "Bart"


def test_set_custom_field_custom_field_still_writes_to_json(
    session_factory: sessionmaker,
) -> None:
    """Compat: un field no nativo sigue yendo al JSON custom_fields."""
    with session_factory() as session:
        contact = Contact(
            first_name="C",
            email="c@ex.com",
            tags="",
            custom_fields=json.dumps({"other": "x"}),
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)

        step = _make_step({"field": "sector", "value": "industrial"})
        result = _step_set_custom_field(session, _make_run(), step, contact)
        assert result.status == "ok"
        stored = json.loads(contact.custom_fields)
        assert stored["sector"] == "industrial"
        assert stored["other"] == "x"


# ---------------------------------------------------------------------
# Bug 3 — vencimiento flexible de la tarea
# ---------------------------------------------------------------------


def test_create_task_due_relative_with_hour(
    session_factory: sessionmaker,
) -> None:
    """due_mode=relative + duration_amount=2 days + duration_hhmm=09:00
    → ahora + 2 días, hora 09:00."""
    now = datetime(2026, 6, 19, 14, 30, tzinfo=UTC)
    cfg = {
        "due_mode": "relative",
        "duration_amount": 2,
        "duration_unit": "days",
        "duration_hhmm": "09:00",
    }
    due_at, all_day = _resolve_workflow_task_due_at(cfg, now=now)
    assert due_at == datetime(2026, 6, 21, 9, 0, tzinfo=UTC)
    assert all_day is False


def test_create_task_due_relative_hours(
    session_factory: sessionmaker,
) -> None:
    """Sin hora explícita en modo relativo → vencimiento a la misma
    hora del día calculada."""
    now = datetime(2026, 6, 19, 14, 0, tzinfo=UTC)
    cfg = {
        "due_mode": "relative",
        "duration_amount": 4,
        "duration_unit": "hours",
    }
    due_at, all_day = _resolve_workflow_task_due_at(cfg, now=now)
    assert due_at == datetime(2026, 6, 19, 18, 0, tzinfo=UTC)
    assert all_day is True


def test_create_task_due_next_weekday(
    session_factory: sessionmaker,
) -> None:
    """Jueves 2026-06-18, pedir 'próximo lunes' (target_weekday=0)
    a las 09:00 → 2026-06-22 09:00."""
    now = datetime(2026, 6, 18, 14, 0, tzinfo=UTC)  # jueves
    cfg = {
        "due_mode": "weekday",
        "target_weekday": 0,  # lunes
        "weekday_hhmm": "09:00",
    }
    due_at, all_day = _resolve_workflow_task_due_at(cfg, now=now)
    assert due_at == datetime(2026, 6, 22, 9, 0, tzinfo=UTC)
    assert all_day is False


def test_create_task_due_next_weekday_today_without_hour(
    session_factory: sessionmaker,
) -> None:
    """Hoy es lunes y pido 'próximo lunes' sin hora → siguiente lunes
    (+7 días), no hoy."""
    # 2026-06-15 es lunes.
    now = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
    cfg = {"due_mode": "weekday", "target_weekday": 0}
    due_at, _ = _resolve_workflow_task_due_at(cfg, now=now)
    assert due_at.weekday() == 0
    assert due_at.date() == datetime(2026, 6, 22).date()


def test_create_task_due_next_weekday_today_with_past_hour(
    session_factory: sessionmaker,
) -> None:
    """Hoy es lunes 14:00, pido 'próximo lunes 09:00' → siguiente
    lunes (la hora de hoy ya pasó)."""
    now = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
    cfg = {
        "due_mode": "weekday",
        "target_weekday": 0,
        "weekday_hhmm": "09:00",
    }
    due_at, _ = _resolve_workflow_task_due_at(cfg, now=now)
    assert due_at == datetime(2026, 6, 22, 9, 0, tzinfo=UTC)


def test_create_task_due_legacy_due_in_days_still_works(
    session_factory: sessionmaker,
) -> None:
    """Drafts viejos sin due_mode pero con due_in_days siguen
    funcionando (backward compat)."""
    now = datetime(2026, 6, 19, 14, 0, tzinfo=UTC)
    cfg = {"due_in_days": 3, "event_time_hhmm": "10:00"}
    due_at, _ = _resolve_workflow_task_due_at(cfg, now=now)
    assert due_at == datetime(2026, 6, 22, 10, 0, tzinfo=UTC)


def test_create_task_step_with_relative_due(
    session_factory: sessionmaker,
) -> None:
    """End-to-end: el step _step_create_task usa el nuevo formato y
    persiste la tarea con due_at calculado."""
    with session_factory() as session:
        owner = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert owner is not None
        contact = Contact(
            first_name="C",
            email="c@ex.com",
            tags="",
            owner_user_id=owner.id,
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)

        step = _make_step(
            {
                "title": "T",
                "due_mode": "relative",
                "duration_amount": 1,
                "duration_unit": "weeks",
            },
            step_type="action_create_task",
        )
        result = _step_create_task(session, _make_run(), step, contact)
        assert result.status == "ok"


# ---------------------------------------------------------------------
# Bug 4 — asunto override con plantilla
# ---------------------------------------------------------------------


def test_send_email_template_with_subject_override(
    session_factory: sessionmaker,
) -> None:
    """En modo plantilla, si cfg.subject_override está relleno, el
    asunto del envío es ese — el body sigue siendo el de la plantilla."""
    with session_factory() as session:
        owner = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert owner is not None
        contact = Contact(
            first_name="C",
            email="c@ex.com",
            tags="",
            owner_user_id=owner.id,
        )
        session.add(contact)
        template = EmailTemplate(
            name="T1",
            subject="Asunto plantilla",
            body_html="<p>Cuerpo plantilla</p>",
        )
        session.add(template)
        session.commit()
        session.refresh(contact)
        session.refresh(template)

        step = WorkflowStep(
            id="s1",
            workflow_id="w1",
            type="action_send_email",
            position_x=0,
            position_y=0,
            is_entry=False,
            config_json=json.dumps(
                {
                    "template_id": template.id,
                    "subject_override": "Recordatorio FESPA",
                    "from_alias_mode": "fixed",
                    "from_alias": "info@bomedia.net",
                }
            ),
        )
        with patch(
            "app.integrations.gmail.service.send_email"
        ) as mock_send:
            mock_send.return_value = type("M", (), {"id": "m1"})()
            result = _step_send_email(session, _make_run(), step, contact)
        assert result.status == "ok"
        # Asunto override gana.
        assert (
            mock_send.call_args.kwargs.get("subject") == "Recordatorio FESPA"
        )
        # Body sigue siendo de la plantilla.
        assert "Cuerpo plantilla" in mock_send.call_args.kwargs.get(
            "body_html"
        )


def test_send_email_template_without_override_uses_template_subject(
    session_factory: sessionmaker,
) -> None:
    """Si subject_override es vacío o ausente, el asunto es el de la
    plantilla (comportamiento existente)."""
    with session_factory() as session:
        owner = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert owner is not None
        contact = Contact(
            first_name="C",
            email="c@ex.com",
            tags="",
            owner_user_id=owner.id,
        )
        session.add(contact)
        template = EmailTemplate(
            name="T1",
            subject="Asunto plantilla",
            body_html="<p>Cuerpo plantilla</p>",
        )
        session.add(template)
        session.commit()
        session.refresh(contact)
        session.refresh(template)

        step = WorkflowStep(
            id="s1",
            workflow_id="w1",
            type="action_send_email",
            position_x=0,
            position_y=0,
            is_entry=False,
            config_json=json.dumps(
                {
                    "template_id": template.id,
                    "subject_override": "",
                    "from_alias_mode": "fixed",
                    "from_alias": "info@bomedia.net",
                }
            ),
        )
        with patch(
            "app.integrations.gmail.service.send_email"
        ) as mock_send:
            mock_send.return_value = type("M", (), {"id": "m1"})()
            _step_send_email(session, _make_run(), step, contact)
        assert (
            mock_send.call_args.kwargs.get("subject") == "Asunto plantilla"
        )
