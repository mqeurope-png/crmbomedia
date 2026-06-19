"""PR-Fixes-Workflows-Pase-4 — tests para los nuevos bugs.

Cubre:
- Bug 4: validador del FilterBuilder con regla incompleta produce
  mensaje específico ("Falta el valor en la regla N").
- Bug 6: endpoint /api/contacts/custom-field-keys une definiciones
  manuales + lo inferido, con `source` en cada entry.
- Bug 7: action_create_task con sync_with_google_calendar dispara
  sync_task_to_calendar; sin Calendar conectado no rompe.
- Bug 8: action_send_email con modo "owner_default" / "owner_specific"
  resuelve el alias en runtime; sin aliases falla con mensaje claro.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    CustomFieldDefinition,
    User,
    UserEmailAliasPref,
)
from app.models.workflows import WorkflowRun, WorkflowStep
from app.workflows.engine import StepResult
from app.workflows.steps import (
    _resolve_workflow_from_alias,
    _step_create_task,
    _step_send_email,
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


# ---------------------------------------------------------------------
# Bug 6 — endpoint custom-field-keys con definiciones + inferidas
# ---------------------------------------------------------------------


def test_custom_fields_endpoint_returns_all_origins(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Verifica que el endpoint une definiciones manuales (de
    CustomFieldDefinition) con keys vistas en contacts.custom_fields,
    reportando `source` en cada entry."""
    with session_factory() as session:
        session.add(
            CustomFieldDefinition(
                key="sector_empresa",
                label="Sector",
                field_type="text",
                source="manual",
            )
        )
        session.add(
            Contact(
                first_name="Imported",
                email="imp@ex.com",
                tags="",
                custom_fields=json.dumps({"INTERES": "alto"}),
            )
        )
        session.commit()

    res = client.get(
        "/api/contacts/custom-field-keys",
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 200
    rows = res.json()
    by_key = {r["key"]: r for r in rows}
    assert by_key["sector_empresa"]["source"] == "manual"
    assert by_key["sector_empresa"]["type"] == "text"
    assert by_key["INTERES"]["source"] == "inferred"


def test_admin_can_create_and_delete_custom_field(
    client: TestClient, session_factory: sessionmaker
) -> None:
    create_res = client.post(
        "/api/admin/custom-fields",
        headers=auth_headers(client, "admin"),
        json={
            "key": "tipo_centro",
            "label": "Tipo de centro",
            "type": "text",
        },
    )
    assert create_res.status_code == 201, create_res.text

    list_res = client.get(
        "/api/admin/custom-fields", headers=auth_headers(client, "admin")
    )
    keys = [r["key"] for r in list_res.json()]
    assert "tipo_centro" in keys

    delete_res = client.delete(
        "/api/admin/custom-fields/tipo_centro",
        headers=auth_headers(client, "admin"),
    )
    assert delete_res.status_code == 204

    list_res_2 = client.get(
        "/api/admin/custom-fields", headers=auth_headers(client, "admin")
    )
    assert "tipo_centro" not in [r["key"] for r in list_res_2.json()]


def test_custom_field_create_rejects_duplicate(
    client: TestClient, session_factory: sessionmaker
) -> None:
    client.post(
        "/api/admin/custom-fields",
        headers=auth_headers(client, "admin"),
        json={"key": "demo_field", "type": "text"},
    )
    dup = client.post(
        "/api/admin/custom-fields",
        headers=auth_headers(client, "admin"),
        json={"key": "demo_field", "type": "text"},
    )
    assert dup.status_code == 409


# ---------------------------------------------------------------------
# Bug 7 — sync con Google Calendar al crear tarea
# ---------------------------------------------------------------------


def _make_step(
    config: dict, *, step_type: str = "action_create_task"
) -> WorkflowStep:
    step = WorkflowStep(
        id="s1",
        workflow_id="w1",
        type=step_type,
        position_x=0,
        position_y=0,
        is_entry=False,
        config_json=json.dumps(config),
    )
    return step


def _make_run() -> WorkflowRun:
    """Sin trigger_payload — el step solo lo lee para `variables.render()`."""
    return WorkflowRun(
        id="r1",
        workflow_id="w1",
        contact_id="c1",
        trigger_payload_json="{}",
    )


def test_task_calendar_sync_creates_event_for_owner_with_oauth(
    session_factory: sessionmaker,
) -> None:
    """Cuando sync_with_google_calendar=True el step llama a
    sync_task_to_calendar después de crear la tarea."""
    with session_factory() as session:
        from sqlalchemy import select  # noqa: PLC0415

        owner = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert owner is not None
        owner_id = owner.id
        contact = Contact(
            first_name="C",
            email="c@ex.com",
            tags="",
            owner_user_id=owner_id,
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)

        step = _make_step(
            {"title": "T", "sync_with_google_calendar": True}
        )

        with patch(
            "app.integrations.google_calendar.service.sync_task_to_calendar"
        ) as mock_sync:
            mock_sync.side_effect = lambda s, t, all_day=False: t
            result = _step_create_task(
                session, _make_run(), step, contact
            )

        assert isinstance(result, StepResult)
        assert result.status == "ok"
        assert mock_sync.call_count == 1
        # all_day=True porque no hay event_time_hhmm.
        assert mock_sync.call_args.kwargs.get("all_day") is True


def test_task_calendar_sync_skips_silently_for_owner_without_oauth(
    session_factory: sessionmaker,
) -> None:
    """Si el owner no tiene Google Calendar conectado, el helper
    sync_task_to_calendar es un no-op por diseño (devuelve la task
    intacta). El step debe seguir devolviendo `ok` y NO romperse."""
    with session_factory() as session:
        from sqlalchemy import select  # noqa: PLC0415

        owner = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert owner is not None
        owner_id = owner.id
        contact = Contact(
            first_name="C",
            email="c@ex.com",
            tags="",
            owner_user_id=owner_id,
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)

        step = _make_step(
            {"title": "T", "sync_with_google_calendar": True}
        )
        # Sin parchear nada — el helper real intentará leer
        # UserGoogleIntegration y devolverá no-op porque no existe.
        result = _step_create_task(session, _make_run(), step, contact)
        assert result.status == "ok"


def test_task_calendar_sync_with_event_time_creates_timed_event(
    session_factory: sessionmaker,
) -> None:
    """Si el operador define event_time_hhmm, el evento NO es all-day."""
    with session_factory() as session:
        from sqlalchemy import select  # noqa: PLC0415

        owner = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert owner is not None
        owner_id = owner.id
        contact = Contact(
            first_name="C", email="c@ex.com", tags="", owner_user_id=owner_id
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)

        step = _make_step(
            {
                "title": "T",
                "sync_with_google_calendar": True,
                "event_time_hhmm": "09:30",
            }
        )
        with patch(
            "app.integrations.google_calendar.service.sync_task_to_calendar"
        ) as mock_sync:
            mock_sync.side_effect = lambda s, t, all_day=False: t
            _step_create_task(session, _make_run(), step, contact)
        assert mock_sync.call_args.kwargs.get("all_day") is False


# ---------------------------------------------------------------------
# Bug 8 — modos de alias en action_send_email
# ---------------------------------------------------------------------


def _seed_owner_aliases(
    session: Session,
    owner_id: str,
    aliases: list[tuple[str, bool, str]],
) -> None:
    """Helper: (alias_email, is_default, gmail_display_name)."""
    for email, is_default, display_name in aliases:
        session.add(
            UserEmailAliasPref(
                user_id=owner_id,
                alias_email=email,
                is_allowed=True,
                is_default=is_default,
                gmail_display_name=display_name,
            )
        )
    session.commit()


def test_resolve_alias_owner_default_picks_starred(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        from sqlalchemy import select  # noqa: PLC0415

        owner = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert owner is not None
        owner_id = owner.id
        _seed_owner_aliases(
            session,
            owner_id,
            [
                ("bart@bomedia.net", False, "Bart"),
                ("ventas@bomedia.net", True, "Ventas Bomedia"),
            ],
        )
        alias, warning = _resolve_workflow_from_alias(
            session=session,
            owner_id=owner_id,
            mode="owner_default",
            fixed_alias=None,
            wanted_display_name=None,
        )
        assert alias == "ventas@bomedia.net"
        assert warning is None


def test_send_email_owner_default_alias_resolved_at_runtime(
    session_factory: sessionmaker,
) -> None:
    """En el step real (no helper) el alias del owner ★ se usa al
    invocar gmail send_email."""
    with session_factory() as session:
        from sqlalchemy import select  # noqa: PLC0415

        owner = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert owner is not None
        owner_id = owner.id
        contact = Contact(
            first_name="C",
            email="c@ex.com",
            tags="",
            owner_user_id=owner_id,
        )
        session.add(contact)
        _seed_owner_aliases(
            session,
            owner_id,
            [("default@bomedia.net", True, "Default")],
        )
        session.commit()
        session.refresh(contact)

        step = WorkflowStep(
            id="s1",
            workflow_id="w1",
            type="action_send_email",
            position_x=0,
            position_y=0,
            is_entry=False,
            config_json=json.dumps(
                {
                    "subject": "Hola",
                    "body_html": "<p>Hola</p>",
                    "from_alias_mode": "owner_default",
                }
            ),
        )

        with patch(
            "app.integrations.gmail.service.send_email"
        ) as mock_send:
            mock_send.return_value = type("M", (), {"id": "m1"})()
            result = _step_send_email(session, _make_run(), step, contact)

        assert result.status == "ok"
        assert (
            mock_send.call_args.kwargs.get("from_alias")
            == "default@bomedia.net"
        )


def test_send_email_owner_specific_alias_fallback_to_default(
    session_factory: sessionmaker,
) -> None:
    """Cuando el display_name elegido ya no existe en los aliases del
    owner, el helper devuelve el alias predeterminado + warning."""
    with session_factory() as session:
        from sqlalchemy import select  # noqa: PLC0415

        owner = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert owner is not None
        owner_id = owner.id
        _seed_owner_aliases(
            session,
            owner_id,
            [
                ("a@x.com", True, "A"),
                ("b@x.com", False, "B"),
            ],
        )

        alias, warning = _resolve_workflow_from_alias(
            session=session,
            owner_id=owner_id,
            mode="owner_specific",
            fixed_alias=None,
            wanted_display_name="Display That Does Not Exist",
        )
        assert alias == "a@x.com"
        assert warning is not None and "missing" in warning


def test_send_email_owner_without_aliases_fails_with_clear_message(
    session_factory: sessionmaker,
) -> None:
    """Owner sin ningún UserEmailAliasPref → step falla con mensaje
    que cita al propietario."""
    with session_factory() as session:
        from sqlalchemy import select  # noqa: PLC0415

        owner = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        assert owner is not None
        owner_id = owner.id
        contact = Contact(
            first_name="C",
            email="c@ex.com",
            tags="",
            owner_user_id=owner_id,
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)

        step = WorkflowStep(
            id="s1",
            workflow_id="w1",
            type="action_send_email",
            position_x=0,
            position_y=0,
            is_entry=False,
            config_json=json.dumps(
                {
                    "subject": "Hola",
                    "body_html": "<p>Hola</p>",
                    "from_alias_mode": "owner_default",
                }
            ),
        )
        result = _step_send_email(session, _make_run(), step, contact)
        assert result.status == "failed"
        assert "no tiene aliases" in (result.error or "")
        assert "admin@example.com" in (result.error or "")
