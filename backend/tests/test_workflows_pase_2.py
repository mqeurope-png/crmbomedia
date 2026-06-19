"""PR-Fixes-Workflows-Pase-2 — tests para los fixes que no entraron
al PR previo o que necesitan nueva verificación.

Cubre:
- Bug D: TRIGGER_CATALOG renombra cron.recurring a "Horario fijo".
- Bug B: el evaluador de condiciones acepta el formato segments
  (`{operator, children}` + `{type: "rule", field, comparator, value}`)
  además del legacy `{op, children}`.
- Bug E: el trigger sub-config (`workflow.trigger_config_json`) se
  persiste correctamente al guardar.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base, Contact
from app.workflows import conditions
from app.workflows.dispatcher import TRIGGER_CATALOG
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
# Bug D — TRIGGER_CATALOG label
# ---------------------------------------------------------------------


def test_trigger_catalog_renamed_cron_to_horario_fijo() -> None:
    cron_entry = next(
        t for t in TRIGGER_CATALOG if t["type"] == "cron.recurring"
    )
    assert cron_entry["label"] == "Horario fijo"


def test_catalog_endpoint_exposes_human_label(client: TestClient) -> None:
    res = client.get(
        "/api/workflows/_catalog", headers=auth_headers(client, "user")
    )
    assert res.status_code == 200
    body = res.json()
    cron = next(t for t in body["triggers"] if t["type"] == "cron.recurring")
    assert cron["label"] == "Horario fijo"


# ---------------------------------------------------------------------
# Bug B — evaluator acepta formato segments
# ---------------------------------------------------------------------


def test_evaluator_accepts_segments_logical_operator(
    session_factory: sessionmaker,
) -> None:
    """El árbol IR del filtro de Contactos usa `operator` minúsculas
    (and/or). El evaluador del workflow debe aceptarlo."""
    with session_factory() as session:
        contact = Contact(
            first_name="Hot", email="h@ex.com", lead_score=80, tags="vip"
        )
        session.add(contact)
        session.commit()
        ctx = conditions.EvalContext(session=session, contact=contact)
        tree = {
            "operator": "and",
            "children": [
                {
                    "type": "rule",
                    "field": "contact.lead_score",
                    "comparator": "gt",
                    "value": 50,
                },
                {
                    "type": "rule",
                    "field": "contact.tags",
                    "comparator": "contains",
                    "value": "vip",
                },
            ],
        }
        assert conditions.evaluate(tree, ctx) is True


def test_evaluator_accepts_segments_leaf_with_comparator(
    session_factory: sessionmaker,
) -> None:
    """Una hoja en formato segments: `{type, field, comparator, value}`."""
    with session_factory() as session:
        contact = Contact(
            first_name="A", email="", tags=""
        )
        session.add(contact)
        session.commit()
        ctx = conditions.EvalContext(session=session, contact=contact)
        # Email vacío → comparator "is_null" mapea a "empty".
        tree = {
            "type": "rule",
            "field": "contact.email",
            "comparator": "is_null",
        }
        assert conditions.evaluate(tree, ctx) is True
        # Operador "neq" → "ne" en workflow.
        tree2 = {
            "type": "rule",
            "field": "contact.first_name",
            "comparator": "neq",
            "value": "Z",
        }
        assert conditions.evaluate(tree2, ctx) is True


def test_evaluator_still_accepts_workflow_legacy_format(
    session_factory: sessionmaker,
) -> None:
    """No rompemos el formato legacy ya en uso en workflows creados
    antes del fix."""
    with session_factory() as session:
        contact = Contact(
            first_name="A", email="a@ex.com", lead_score=90, tags=""
        )
        session.add(contact)
        session.commit()
        ctx = conditions.EvalContext(session=session, contact=contact)
        tree = {
            "op": "AND",
            "children": [
                {
                    "field": "contact.lead_score",
                    "op": "gt",
                    "value": 50,
                },
            ],
        }
        assert conditions.evaluate(tree, ctx) is True


# ---------------------------------------------------------------------
# Bug E — trigger_config persiste sub-parámetros via PUT
# ---------------------------------------------------------------------


def test_update_workflow_persists_trigger_config(
    client: TestClient,
) -> None:
    # Crear workflow vacío.
    res = client.post(
        "/api/workflows",
        json={"name": "T", "trigger_type": "email.brevo.opened"},
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 201
    wf_id = res.json()["id"]

    # PUT con sub-config + filtro.
    res = client.put(
        f"/api/workflows/{wf_id}",
        json={
            "trigger_config": {
                "campaign_id": "br-camp-99",
                "account_id": "boprint",
                "filter": {
                    "operator": "and",
                    "children": [
                        {
                            "type": "rule",
                            "field": "contact.lead_score",
                            "comparator": "gt",
                            "value": 50,
                        }
                    ],
                },
            },
        },
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["trigger_config"]["campaign_id"] == "br-camp-99"
    assert body["trigger_config"]["filter"]["operator"] == "and"


def test_update_workflow_persists_trigger_config_with_steps(
    client: TestClient,
) -> None:
    """Caso real: el editor manda steps + edges + trigger_config en
    el mismo PUT."""
    res = client.post(
        "/api/workflows",
        json={"name": "T", "trigger_type": "contact.created"},
        headers=auth_headers(client, "admin"),
    )
    wf_id = res.json()["id"]

    res = client.put(
        f"/api/workflows/{wf_id}",
        json={
            "trigger_config": {
                "source": "manual",
                "filter": {
                    "operator": "or",
                    "children": [
                        {
                            "type": "rule",
                            "field": "contact.tags",
                            "comparator": "contains",
                            "value": "FESPA-2026",
                        },
                    ],
                },
            },
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
                    "type": "exit_natural",
                    "config": {},
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
    assert body["trigger_config"]["source"] == "manual"
    assert (
        body["trigger_config"]["filter"]["children"][0]["value"]
        == "FESPA-2026"
    )
    assert len(body["steps"]) == 2
    assert len(body["edges"]) == 1
