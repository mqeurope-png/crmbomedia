"""CRM · CRM-1.5 — filtros nuevos del panel de contactos (actividad + ERP) y
reorganización del registro de campos.

Los filtros son campos del motor de reglas (lo que usa el panel de /contacts),
compilados a EXISTS sobre datos LOCALES (call_logs, notes, tasks, email_messages,
workflow_runs, companies, orders). Ninguno consulta FACTUSOL.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401  — registra todos los modelos en Base.metadata
from app.db.base import Base
from app.erp.models.orders import Order, PreparationStatus, TransportStatus
from app.models.crm import (
    CallLog,
    Company,
    Contact,
    EmailMessage,
    EmailThread,
    Note,
    Task,
    TaskPriority,
    TaskStatus,
    User,
    UserRole,
)
from app.models.workflows import Workflow, WorkflowRun, WorkflowRunState
from app.services.segments.engine import build_filter
from app.services.segments.fields import FIELD_SPECS


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


def _leaf(field: str, comparator: str, value):
    return {"type": "rule", "field": field, "comparator": comparator, "value": value}


def _match(session: Session, rule: dict) -> set[str]:
    return {c.id for c in session.scalars(
        select(Contact).where(build_filter(rule)))}


def _contact(session: Session, name: str, **over) -> Contact:
    c = Contact(first_name=name, email=f"{name.lower()}@x.com", **over)
    session.add(c)
    session.commit()
    return c


def _now() -> datetime:
    return datetime.now(UTC)


# --- Actividad reciente -----------------------------------------------------


def test_contacts_filter_by_last_interaction_range(factory):
    with factory() as s:
        reciente = _contact(s, "Reciente")
        viejo = _contact(s, "Viejo")
        _contact(s, "Sinnada")
        s.add(CallLog(contact_id=reciente.id, user_id="u1",
                      result_code="contacted", called_at=_now() - timedelta(days=3)))
        s.add(Note(contact_id=viejo.id, body="hace mucho",
                   created_at=_now() - timedelta(days=200)))
        s.commit()

        assert _match(s, _leaf("last_interaction", "in_last_n_days", 30)) == {reciente.id}


def test_contacts_filter_by_days_since_contact(factory):
    with factory() as s:
        activo = _contact(s, "Activo")
        dormido = _contact(s, "Dormido")
        nunca = _contact(s, "Nunca")
        s.add(CallLog(contact_id=activo.id, user_id="u1", result_code="contacted",
                      called_at=_now() - timedelta(days=5)))
        s.add(CallLog(contact_id=dormido.id, user_id="u1", result_code="contacted",
                      called_at=_now() - timedelta(days=90)))
        s.commit()

        # >= 30 días sin contactar: el dormido y el que nunca (incluye null).
        assert _match(s, _leaf("days_since_contact", "gte", 30)) == {dormido.id, nunca.id}
        # <= 30 días: solo el activo.
        assert _match(s, _leaf("days_since_contact", "lte", 30)) == {activo.id}


def test_contacts_filter_by_has_tasks_overdue(factory):
    with factory() as s:
        con_vencida = _contact(s, "Vencida")
        con_pendiente = _contact(s, "Pendiente")
        sin_tareas = _contact(s, "Sintareas")
        user = User(email="a@b.com", full_name="A", password_hash="x",
                    role=UserRole.ADMIN)
        s.add(user)
        s.commit()

        def _task(c, **over):
            s.add(Task(title="t", contact_id=c.id, assigned_user_id=user.id,
                       created_by_user_id=user.id, priority=TaskPriority.MEDIUM,
                       **over))
        _task(con_vencida, status=TaskStatus.PENDING,
              due_at=_now() - timedelta(days=2))
        _task(con_pendiente, status=TaskStatus.PENDING,
              due_at=_now() + timedelta(days=5))
        s.commit()

        assert _match(s, _leaf("has_tasks", "eq", "overdue")) == {con_vencida.id}
        assert _match(s, _leaf("has_tasks", "eq", "pending")) == {
            con_vencida.id, con_pendiente.id}
        assert _match(s, _leaf("has_tasks", "eq", "none")) == {sin_tareas.id}


def test_contacts_filter_by_has_notes_none(factory):
    with factory() as s:
        con_nota = _contact(s, "Connota")
        sin_nota = _contact(s, "Sinnota")
        s.add(Note(contact_id=con_nota.id, body="algo"))
        s.commit()

        assert _match(s, _leaf("has_notes", "eq", "none")) == {sin_nota.id}
        assert _match(s, _leaf("has_notes", "eq", "any")) == {con_nota.id}


def test_contacts_filter_by_has_emails_range(factory):
    with factory() as s:
        con_email = _contact(s, "Conemail")
        _contact(s, "Sinemail")
        thread = EmailThread(subject="hola", gmail_thread_id="t1",
                             gmail_account_user_id="u1",
                             initiated_by_user_id="u1",
                             first_message_at=_now() - timedelta(days=2),
                             last_message_at=_now() - timedelta(days=2))
        s.add(thread)
        s.flush()
        s.add(EmailMessage(
            thread_id=thread.id, gmail_account_user_id="u1", direction="outbound",
            contact_id=con_email.id, sent_at=_now() - timedelta(days=2),
            from_email="me@bomedia.net", to_emails_json="[]"))
        s.commit()

        assert _match(s, _leaf("has_emails", "in_last_n_days", 30)) == {con_email.id}


def test_contacts_filter_by_in_workflow(factory):
    with factory() as s:
        dentro = _contact(s, "Dentro")
        _contact(s, "Fuera")
        wf = Workflow(name="WF", trigger_type="contact.manual")
        s.add(wf)
        s.flush()
        s.add(WorkflowRun(workflow_id=wf.id, contact_id=dentro.id,
                          state=WorkflowRunState.RUNNING,
                          active_dedup_key=f"{wf.id}:{dentro.id}",
                          started_at=_now()))
        s.commit()

        assert _match(s, _leaf("in_workflow", "in", [wf.id])) == {dentro.id}


# --- ERP y FACTUSOL ---------------------------------------------------------


def test_contacts_filter_by_factusol_linked_true(factory):
    with factory() as s:
        linked_co = Company(name="Vinculada", factusol_company_id="3342")
        plain_co = Company(name="Sin vínculo")
        s.add_all([linked_co, plain_co])
        s.commit()
        vinculado = _contact(s, "Vinculado", company_id=linked_co.id)
        no_vinc = _contact(s, "Novinc", company_id=plain_co.id)
        sin_empresa = _contact(s, "Sinempresa")

        assert _match(s, _leaf("factusol_linked", "eq", True)) == {vinculado.id}
        assert _match(s, _leaf("factusol_linked", "eq", False)) == {
            no_vinc.id, sin_empresa.id}


def test_contacts_filter_by_has_orders_in_queue(factory):
    with factory() as s:
        co_cola = Company(name="EnCola")
        co_transito = Company(name="EnTransito")
        s.add_all([co_cola, co_transito])
        s.commit()
        cola = _contact(s, "Cola", company_id=co_cola.id)
        transito = _contact(s, "Transito", company_id=co_transito.id)
        _contact(s, "Sinpedidos")
        s.add(Order(order_number="M-1", external_source="manual",
                    company_id=co_cola.id, total_amount=10,
                    preparation_status=PreparationStatus.IN_QUEUE))
        s.add(Order(order_number="M-2", external_source="manual",
                    company_id=co_transito.id, total_amount=10,
                    transport_status=TransportStatus.IN_TRANSIT))
        s.commit()

        assert _match(s, _leaf("has_orders", "eq", "in_queue")) == {cola.id}
        assert _match(s, _leaf("has_orders", "in",
                               ["in_queue", "in_transit"])) == {cola.id, transito.id}
        assert _match(s, _leaf("has_orders", "eq", "any")) == {cola.id, transito.id}


def test_contacts_filter_combined_activity_and_erp(factory):
    """Los filtros nuevos se combinan con AND como cualquier otro."""
    with factory() as s:
        co = Company(name="Taller", factusol_company_id="9")
        s.add(co)
        s.commit()
        objetivo = _contact(s, "Objetivo", company_id=co.id)
        otro = _contact(s, "Otro", company_id=co.id)  # vinculado pero sin pedido
        s.add(Order(order_number="M-1", external_source="manual",
                    company_id=co.id, total_amount=10,
                    preparation_status=PreparationStatus.IN_QUEUE))
        s.commit()
        # Nota: el pedido cuelga de la empresa, que comparten los dos contactos,
        # así que «vinculado + con pedido en cola» matchea a ambos. Se distingue
        # con una tarea vencida solo en el objetivo.
        user = User(email="a@b.com", full_name="A", password_hash="x",
                    role=UserRole.ADMIN)
        s.add(user)
        s.commit()
        s.add(Task(title="t", contact_id=objetivo.id, assigned_user_id=user.id,
                   created_by_user_id=user.id, priority=TaskPriority.MEDIUM,
                   status=TaskStatus.PENDING, due_at=_now() - timedelta(days=1)))
        s.commit()

        rule = {"operator": "and", "children": [
            _leaf("factusol_linked", "eq", True),
            _leaf("has_orders", "eq", "in_queue"),
            _leaf("has_tasks", "eq", "overdue"),
        ]}
        assert _match(s, rule) == {objetivo.id}
        assert otro.id not in _match(s, rule)


# --- reorganización del registro --------------------------------------------


def test_filter_panel_reorganised_into_seven_groups(factory):
    groups = {s.grouped_under for s in FIELD_SPECS.values() if s.filterable}
    assert groups == {
        "Datos del contacto", "Dirección", "Propiedad y origen", "Pertenencia",
        "Actividad reciente", "Llamadas", "ERP y FACTUSOL",
    }


def test_redundant_filters_are_retired_from_panel_but_stay_as_fields(factory):
    # Retirados del panel…
    for key in ("name", "email", "phone", "first_name", "last_name",
                "linkedin_url", "is_active", "marketing_consent", "id",
                "assigned_users", "origin_system", "created_at"):
        assert FIELD_SPECS[key].filterable is False, key
    # …pero SIGUEN existiendo como campos (columna / param), no se borran.
    assert "email" in FIELD_SPECS
    assert FIELD_SPECS["email"].displayable is True
