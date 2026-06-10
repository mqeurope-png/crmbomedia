"""AI-assisted pipeline generation: `POST /pipelines/generate-ai`.

The Anthropic client is monkeypatched at the `_invoke_claude` boundary
so the tests exercise the route + JSON parsing + audit + rate-limit
without making real API calls.
"""
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.session import get_session
from app.main import app
from app.models.crm import Base
from app.services import llm as llm_service
from tests._test_helpers import auth_headers, seed_test_users

VALID_PROPOSAL_JSON = """
{
  "name": "Pipeline IA",
  "description": "Generado para test",
  "color": "#3b82f6",
  "stages": [
    {"name": "Lead", "target_days": 1, "is_won": false, "is_lost": false},
    {"name": "Cualificado", "target_days": 5, "is_won": false, "is_lost": false},
    {"name": "Propuesta", "target_days": 10, "is_won": false, "is_lost": false},
    {"name": "Cerrado", "is_won": true, "is_lost": false},
    {"name": "Perdido", "is_won": false, "is_lost": true}
  ]
}
"""


@pytest.fixture()
def client(monkeypatch) -> Generator[TestClient, None, None]:
    """Spin up the test client with AI features turned on so the
    endpoint isn't 503'd. Reset the rate-limit bucket between tests
    so each test starts with a fresh budget."""
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

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    llm_service.reset_rate_limit()

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_health_endpoint_exposes_ai_flag(client: TestClient):
    """The frontend reads `ai_features_enabled` from health to decide
    whether to render the AI CTA. With ANTHROPIC_API_KEY set the
    flag flips to true."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ai_features_enabled"] is True


def test_generate_returns_proposal_without_persisting(
    client: TestClient, monkeypatch
):
    """The endpoint deliberately does NOT create a pipeline row — the
    operator inspects and confirms before saving."""
    from app.models.crm import Pipeline

    monkeypatch.setattr(
        llm_service,
        "_invoke_claude",
        lambda **_kwargs: VALID_PROPOSAL_JSON,
    )

    response = client.post(
        "/api/pipelines/generate-ai",
        json={"description": "Vendo impresoras UV a empresas textiles"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Pipeline IA"
    assert len(body["stages"]) == 5
    assert body["stages"][-2]["is_won"] is True

    # Check no pipeline row was persisted.
    session_factory = app.dependency_overrides[get_session]
    gen = session_factory()
    session = next(gen)
    try:
        assert session.query(Pipeline).count() == 0
    finally:
        gen.close()


def test_generate_strips_markdown_fence(client: TestClient, monkeypatch):
    """Claude sometimes wraps JSON in a ```json``` fence even when the
    system prompt forbids it. The normaliser must strip it before
    json.loads."""
    fenced = f"```json\n{VALID_PROPOSAL_JSON}\n```"
    monkeypatch.setattr(llm_service, "_invoke_claude", lambda **_kwargs: fenced)
    response = client.post(
        "/api/pipelines/generate-ai",
        json={"description": "test"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200


def test_generate_502s_on_invalid_json(client: TestClient, monkeypatch):
    monkeypatch.setattr(llm_service, "_invoke_claude", lambda **_kwargs: "not json")
    response = client.post(
        "/api/pipelines/generate-ai",
        json={"description": "test"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 502


def test_generate_502s_on_too_few_stages(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        llm_service,
        "_invoke_claude",
        lambda **_kwargs: '{"name": "X", "stages": [{"name": "A"}]}',
    )
    response = client.post(
        "/api/pipelines/generate-ai",
        json={"description": "test"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 502


def test_generate_429s_after_local_rate_limit(client: TestClient, monkeypatch):
    """Local sliding window caps the user at 5 generations per hour."""
    monkeypatch.setattr(
        llm_service,
        "_invoke_claude",
        lambda **_kwargs: VALID_PROPOSAL_JSON,
    )
    headers = auth_headers(client, "manager")
    for _ in range(5):
        ok = client.post(
            "/api/pipelines/generate-ai",
            json={"description": "x"},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
    blocked = client.post(
        "/api/pipelines/generate-ai",
        json={"description": "x"},
        headers=headers,
    )
    assert blocked.status_code == 429


def test_generate_503_when_ai_disabled(monkeypatch):
    """When `ANTHROPIC_API_KEY` is unset the endpoint must 503 so the
    frontend hides the CTA."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as seed_session:
        seed_test_users(seed_session)

    def override_session():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/api/pipelines/generate-ai",
                json={"description": "x"},
                headers=auth_headers(test_client, "manager"),
            )
            assert response.status_code == 503
            health = test_client.get("/api/health").json()
            assert health["ai_features_enabled"] is False
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        get_settings.cache_clear()  # type: ignore[attr-defined]


def test_generate_audit_does_not_log_raw_description(
    client: TestClient, monkeypatch
):
    """Audit row carries metadata only — description length + stage
    count. The free text the operator typed never lands in the audit
    log because it can contain PII or customer secrets."""
    from app.models.crm import AuditLog

    monkeypatch.setattr(
        llm_service,
        "_invoke_claude",
        lambda **_kwargs: VALID_PROPOSAL_JSON,
    )
    description = "Vendo software a hospitales privados en España"
    client.post(
        "/api/pipelines/generate-ai",
        json={"description": description},
        headers=auth_headers(client, "manager"),
    )

    session_factory = app.dependency_overrides[get_session]
    gen = session_factory()
    session = next(gen)
    try:
        audit = (
            session.query(AuditLog)
            .filter(AuditLog.action == "pipeline.ai_generated")
            .one()
        )
    finally:
        gen.close()
    assert audit.metadata_json is not None
    assert "hospitales" not in audit.metadata_json
    assert str(len(description)) in audit.metadata_json
