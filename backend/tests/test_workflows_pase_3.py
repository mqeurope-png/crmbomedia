"""PR-Fixes-Workflows-Pase-3 — tests para los bugs bloqueantes.

Cubre:
- Bug 1: validador estructural rechaza condition/wait_for_event/switch
  con ramas huérfanas.
- Bug 6: endpoint /api/contacts/custom-field-keys devuelve las claves
  vistas en custom_fields con tipo inferido.
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base, Contact
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


def _create_workflow(client: TestClient, *, trigger_type: str = "contact.created") -> str:
    res = client.post(
        "/api/workflows",
        json={"name": "T", "trigger_type": trigger_type},
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 201
    return res.json()["id"]


def _save(client: TestClient, wf_id: str, steps: list[dict], edges: list[dict]) -> dict:
    res = client.put(
        f"/api/workflows/{wf_id}",
        json={"steps": steps, "edges": edges},
        headers=auth_headers(client, "admin"),
    )
    assert res.status_code == 200, res.text
    return res.json()


def _activate(client: TestClient, wf_id: str) -> tuple[int, dict]:
    res = client.post(
        f"/api/workflows/{wf_id}/activate",
        json={"acknowledged_estimate": True},
        headers=auth_headers(client, "admin"),
    )
    return res.status_code, res.json()


# ---------------------------------------------------------------------
# Bug 1 — handles huérfanos
# ---------------------------------------------------------------------


def test_condition_with_only_true_branch_fails_validation(
    client: TestClient,
) -> None:
    """Condición con rama Sí conectada pero rama No huérfana → activar
    devuelve 400 con mensaje específico."""
    wf_id = _create_workflow(client)
    _save(
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
            },
            {
                "client_id": "s2",
                "type": "condition",
                "config": {
                    "condition": {
                        "field": "contact.lead_score",
                        "op": "gt",
                        "value": 50,
                    }
                },
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
            {"from_client_id": "s2", "to_client_id": "s3", "branch_label": "true"},
            # rama "false" sin conectar.
        ],
    )
    code, body = _activate(client, wf_id)
    assert code == 400
    errors = body["detail"]["errors"]
    assert any("«No» sin conectar" in e for e in errors)


def test_wait_for_event_with_only_matched_branch_fails(
    client: TestClient,
) -> None:
    wf_id = _create_workflow(client)
    _save(
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
            },
            {
                "client_id": "s2",
                "type": "wait_for_event",
                "config": {"event_type": "email.crm.opened", "timeout_minutes": 60},
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
            {"from_client_id": "s2", "to_client_id": "s3", "branch_label": "matched"},
            # rama "timeout" sin conectar.
        ],
    )
    code, body = _activate(client, wf_id)
    assert code == 400
    errors = body["detail"]["errors"]
    assert any("«Timeout» sin conectar" in e for e in errors)


def test_switch_with_missing_default_fails(
    client: TestClient,
) -> None:
    wf_id = _create_workflow(client)
    _save(
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
            },
            {
                "client_id": "s2",
                "type": "switch",
                "config": {
                    "field": "contact.lifecycle_status",
                    "cases": ["new", "qualified"],
                },
                "position_x": 100,
                "position_y": 100,
                "is_entry": False,
            },
            {
                "client_id": "s3",
                "type": "exit_natural",
                "config": {},
                "position_x": 0,
                "position_y": 200,
                "is_entry": False,
            },
        ],
        edges=[
            {"from_client_id": "s1", "to_client_id": "s2", "branch_label": "default"},
            {"from_client_id": "s2", "to_client_id": "s3", "branch_label": "case_0"},
            {"from_client_id": "s2", "to_client_id": "s3", "branch_label": "case_1"},
            # rama "default" (Otros) sin conectar.
        ],
    )
    code, body = _activate(client, wf_id)
    assert code == 400
    errors = body["detail"]["errors"]
    assert any("«Otros» sin conectar" in e for e in errors)


def test_condition_with_both_branches_connected_activates(
    client: TestClient,
) -> None:
    wf_id = _create_workflow(client)
    _save(
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
            },
            {
                "client_id": "s2",
                "type": "condition",
                "config": {
                    "condition": {
                        "field": "contact.lead_score",
                        "op": "gt",
                        "value": 50,
                    }
                },
                "position_x": 0,
                "position_y": 100,
                "is_entry": False,
            },
            {
                "client_id": "s3",
                "type": "exit_won",
                "config": {},
                "position_x": 100,
                "position_y": 200,
                "is_entry": False,
            },
            {
                "client_id": "s4",
                "type": "exit_lost",
                "config": {},
                "position_x": -100,
                "position_y": 200,
                "is_entry": False,
            },
        ],
        edges=[
            {"from_client_id": "s1", "to_client_id": "s2", "branch_label": "default"},
            {"from_client_id": "s2", "to_client_id": "s3", "branch_label": "true"},
            {"from_client_id": "s2", "to_client_id": "s4", "branch_label": "false"},
        ],
    )
    code, _body = _activate(client, wf_id)
    assert code == 200


# ---------------------------------------------------------------------
# Bug 6 — custom-field-keys endpoint
# ---------------------------------------------------------------------


def test_custom_field_keys_returns_union_with_inferred_types(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        session.add(
            Contact(
                first_name="A",
                email="a@ex.com",
                tags="",
                custom_fields=json.dumps(
                    {
                        "sector": "industrial",
                        "lead_age": 7,
                        "demo_date": "2026-07-15",
                        "is_vip": True,
                    }
                ),
            )
        )
        session.add(
            Contact(
                first_name="B",
                email="b@ex.com",
                tags="",
                custom_fields=json.dumps(
                    {
                        "sector": "retail",
                        "extra_note": "Lorem",
                    }
                ),
            )
        )
        session.commit()

    res = client.get(
        "/api/contacts/custom-field-keys",
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 200
    rows = res.json()
    by_key = {r["key"]: r["type"] for r in rows}
    assert by_key["sector"] == "text"
    assert by_key["lead_age"] == "number"
    assert by_key["demo_date"] == "date"
    assert by_key["is_vip"] == "boolean"
    assert by_key["extra_note"] == "text"


def test_custom_field_keys_empty_returns_empty(
    client: TestClient, session_factory: sessionmaker
) -> None:
    res = client.get(
        "/api/contacts/custom-field-keys",
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 200
    assert res.json() == []
