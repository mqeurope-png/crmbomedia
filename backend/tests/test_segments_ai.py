"""AI generation + explanation endpoints for segments.

Monkeypatches `_invoke_claude` so the suite never makes real API calls.
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

VALID_GENERATED_RULES = """
{
  "operator": "AND",
  "children": [
    {"type": "rule", "field": "lead_score", "comparator": "gte", "value": 50},
    {"type": "rule", "field": "marketing_consent", "comparator": "eq", "value": "granted"}
  ]
}
"""


@pytest.fixture()
def client(monkeypatch) -> Generator[TestClient, None, None]:
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


def test_ai_generate_returns_validated_rules_with_preview(
    client: TestClient, monkeypatch
):
    """Happy path: Claude returns a valid tree, the route runs it
    through the engine, attaches a count + sample drawn from the
    actual DB so the operator sees expected impact before saving."""
    client.post(
        "/api/contacts",
        json={
            "first_name": "Ana",
            "email": "ana@example.com",
            "marketing_consent": "granted",
        },
        headers=auth_headers(client, "manager"),
    )
    monkeypatch.setattr(
        llm_service, "_invoke_claude", lambda **_kwargs: VALID_GENERATED_RULES
    )
    response = client.post(
        "/api/segments/ai-generate",
        json={"description": "Hot leads con consentimiento"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["error"] is None
    assert body["rules"] is not None
    assert body["count"] >= 0


def test_ai_generate_injects_crm_context_into_system_prompt(
    client: TestClient, monkeypatch
):
    """The route must build a per-tenant CRM context block and splice
    it into the system prompt before invoking Claude. Without this,
    the model can't know the operator's real tag ids and ends up
    generating rules that match zero contacts."""
    from app.models.crm import Tag
    from app.services.segments import ai_context

    session_factory = app.dependency_overrides[get_session]
    gen = session_factory()
    session = next(gen)
    try:
        session.add(Tag(name="formMBO", name_normalized="formmbo"))
        session.commit()
    finally:
        gen.close()
    ai_context.reset_cache()

    captured: dict[str, str] = {}

    def fake_invoke(**kwargs):
        captured["system_prompt"] = kwargs["system_prompt"]
        return VALID_GENERATED_RULES

    monkeypatch.setattr(llm_service, "_invoke_claude", fake_invoke)
    response = client.post(
        "/api/segments/ai-generate",
        json={"description": "leads con tag MBO"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200
    assert "TAGS DISPONIBLES" in captured["system_prompt"]
    assert "formMBO" in captured["system_prompt"]


def test_ai_generate_passes_error_when_model_returns_one(
    client: TestClient, monkeypatch
):
    """Claude's prompt allows it to return `{error: ...}` when the
    request is ambiguous. The route surfaces that as a 200 with
    `error` set so the UI can show a friendly message instead of a
    network failure."""
    monkeypatch.setattr(
        llm_service,
        "_invoke_claude",
        lambda **_kwargs: '{"error": "Ambiguo"}',
    )
    response = client.post(
        "/api/segments/ai-generate",
        json={"description": "no se sabe"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rules"] is None
    assert body["error"] == "Ambiguo"


def test_ai_generate_502_on_invalid_json(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        llm_service, "_invoke_claude", lambda **_kwargs: "not json"
    )
    response = client.post(
        "/api/segments/ai-generate",
        json={"description": "x"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 502


def test_ai_generate_503_when_disabled(monkeypatch):
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
                "/api/segments/ai-generate",
                json={"description": "x"},
                headers=auth_headers(test_client, "manager"),
            )
            assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        get_settings.cache_clear()  # type: ignore[attr-defined]


def test_ai_generate_429_after_rate_limit(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        llm_service, "_invoke_claude", lambda **_kwargs: VALID_GENERATED_RULES
    )
    headers = auth_headers(client, "manager")
    for _ in range(10):
        ok = client.post(
            "/api/segments/ai-generate",
            json={"description": "x"},
            headers=headers,
        )
        assert ok.status_code == 200
    blocked = client.post(
        "/api/segments/ai-generate",
        json={"description": "x"},
        headers=headers,
    )
    assert blocked.status_code == 429


def test_ai_explain_returns_natural_language(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        llm_service,
        "_invoke_claude",
        lambda **_kwargs: "Contactos con buen lead score y marketing concedido.",
    )
    rules = {
        "type": "rule",
        "field": "lead_score",
        "comparator": "gte",
        "value": 50,
    }
    response = client.post(
        "/api/segments/ai-explain",
        json={"rules": rules},
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 200
    assert "Contactos" in response.json()["explanation"]


def test_ai_explain_rejects_invalid_payload(client: TestClient):
    response = client.post(
        "/api/segments/ai-explain",
        json={},
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 400


def test_ai_generate_audit_metadata_only(client: TestClient, monkeypatch):
    """Audit must NEVER contain the raw description (PII / customer
    data). Only length + boolean has_rules."""
    from app.models.crm import AuditLog

    monkeypatch.setattr(
        llm_service, "_invoke_claude", lambda **_kwargs: VALID_GENERATED_RULES
    )
    description = "Clientes premium del mercado farmacéutico en Andalucía"
    client.post(
        "/api/segments/ai-generate",
        json={"description": description},
        headers=auth_headers(client, "manager"),
    )
    session_factory = app.dependency_overrides[get_session]
    gen = session_factory()
    session = next(gen)
    try:
        audit = (
            session.query(AuditLog)
            .filter(AuditLog.action == "segment.ai_generated")
            .one()
        )
    finally:
        gen.close()
    assert audit.metadata_json is not None
    assert "farmacéutico" not in audit.metadata_json
    assert str(len(description)) in audit.metadata_json
