"""Hardcoded template library + `POST /pipelines/from-template`."""
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base
from app.services import pipeline_templates as templates
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as seed_session:
        seed_test_users(seed_session)

    def override_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def test_template_library_returns_seven_categories(client: TestClient):
    response = client.get(
        "/api/pipeline-templates", headers=auth_headers(client, "viewer")
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 7
    ids = {row["id"] for row in body}
    assert {"sales_b2b", "onboarding", "support", "renewal"} <= ids


def test_build_pipeline_payload_normalises_positions():
    payload = templates.build_pipeline_payload("sales_b2b")
    assert payload is not None
    positions = [stage["position"] for stage in payload["stages"]]
    assert positions == list(range(len(payload["stages"])))
    # The terminal stages survive the translation.
    assert any(stage["is_won"] for stage in payload["stages"])
    assert any(stage["is_lost"] for stage in payload["stages"])


def test_create_pipeline_from_template_persists_full_structure(client: TestClient):
    """The route delegates to `build_pipeline_payload` so the
    persisted row mirrors the template exactly — same stages, same
    target_days, same colours."""
    response = client.post(
        "/api/pipelines/from-template",
        json={"template_id": "support"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Soporte técnico / Tickets"
    stage_names = [stage["name"] for stage in body["stages"]]
    assert stage_names[0] == "Abierto"
    assert stage_names[-1] == "Cerrado sin resolver"
    assert any(stage["is_won"] for stage in body["stages"])
    assert any(stage["is_lost"] for stage in body["stages"])


def test_create_from_template_accepts_custom_name(client: TestClient):
    response = client.post(
        "/api/pipelines/from-template",
        json={"template_id": "sales_b2c", "name": "Pipeline impresoras UV"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Pipeline impresoras UV"


def test_create_from_template_404s_for_unknown_id(client: TestClient):
    response = client.post(
        "/api/pipelines/from-template",
        json={"template_id": "nope"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 404


def test_template_returns_immutable_copy():
    """A caller mutating the returned dict must not corrupt the
    next request's response."""
    first = templates.get_template("sales_b2b")
    assert first is not None
    first["stages"][0]["name"] = "MUTATED"
    second = templates.get_template("sales_b2b")
    assert second is not None
    assert second["stages"][0]["name"] != "MUTATED"
