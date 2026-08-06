"""CRM · CRM-1.6 — filtros nuevos de llamadas (has_calls, fecha) + endpoint de
workflows activos para el editor «En workflow».
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401  — registra los modelos en Base.metadata
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.crm import CallLog, Contact
from app.models.workflows import Workflow, WorkflowStatus
from app.services.segments.engine import build_filter
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with f() as seed:
        seed_test_users(seed)
    yield f
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _leaf(field, comparator, value):
    return {"type": "rule", "field": field, "comparator": comparator, "value": value}


def _match(session, rule) -> set[str]:
    return {c.id for c in session.scalars(select(Contact).where(build_filter(rule)))}


def _contact(session, name) -> Contact:
    c = Contact(first_name=name, email=f"{name.lower()}@x.com")
    session.add(c)
    session.commit()
    return c


def _call(session, contact, when):
    session.add(CallLog(contact_id=contact.id, user_id="u1",
                        result_code="contacted", called_at=when))
    session.commit()


# --- has_calls --------------------------------------------------------------


def test_contacts_filter_by_has_calls_true(factory):
    with factory() as s:
        con = _contact(s, "Conllamada")
        _contact(s, "Sinllamada")
        _call(s, con, datetime.now(UTC))
        assert _match(s, _leaf("has_calls", "eq", True)) == {con.id}


def test_contacts_filter_by_has_calls_false(factory):
    with factory() as s:
        con = _contact(s, "Conllamada")
        sin = _contact(s, "Sinllamada")
        _call(s, con, datetime.now(UTC))
        assert _match(s, _leaf("has_calls", "eq", False)) == {sin.id}


# --- fecha de llamada (call_date, ya existente desde CRM-1) ------------------


def test_contacts_filter_by_call_registered_at_before(factory):
    with factory() as s:
        vieja = _contact(s, "Vieja")
        nueva = _contact(s, "Nueva")
        _call(s, vieja, datetime(2025, 12, 1, tzinfo=UTC))
        _call(s, nueva, datetime(2026, 6, 1, tzinfo=UTC))
        assert _match(s, _leaf("call_date", "before", "2026-01-01")) == {vieja.id}


def test_contacts_filter_by_call_registered_at_after(factory):
    with factory() as s:
        vieja = _contact(s, "Vieja")
        nueva = _contact(s, "Nueva")
        _call(s, vieja, datetime(2025, 12, 1, tzinfo=UTC))
        _call(s, nueva, datetime(2026, 6, 1, tzinfo=UTC))
        assert _match(s, _leaf("call_date", "after", "2026-01-01")) == {nueva.id}


def test_contacts_filter_by_call_registered_at_between(factory):
    with factory() as s:
        dentro = _contact(s, "Dentro")
        fuera = _contact(s, "Fuera")
        _call(s, dentro, datetime(2026, 6, 15, tzinfo=UTC))
        _call(s, fuera, datetime(2026, 8, 1, tzinfo=UTC))
        rule = _leaf("call_date", "between", ["2026-06-01", "2026-07-01"])
        assert _match(s, rule) == {dentro.id}


# --- endpoint de workflows activos ------------------------------------------


def test_workflow_editor_returns_active_workflows(client, factory):
    with factory() as s:
        s.add(Workflow(name="Bienvenida", trigger_type="contact.manual",
                       status=WorkflowStatus.ACTIVE))
        s.add(Workflow(name="Borrador", trigger_type="contact.manual",
                       status=WorkflowStatus.DRAFT))
        s.commit()

    r = client.get("/api/workflows/active", headers=auth_headers(client))
    assert r.status_code == 200, r.text
    names = [w["name"] for w in r.json()]
    assert names == ["Bienvenida"]
    assert all(set(w) == {"id", "name"} for w in r.json())


def test_workflow_active_not_shadowed_by_detail_route(client, factory):
    """La ruta literal `/active` no debe caer en `GET /{workflow_id}`."""
    r = client.get("/api/workflows/active", headers=auth_headers(client))
    assert r.status_code == 200
    assert isinstance(r.json(), list)
