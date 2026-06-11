"""Dashboard widget endpoints — regression tests for the Fase 3
500s on pipeline-summary and leads-stats."""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    ContactPipelineStage,
    Pipeline,
    PipelineStage,
    User,
    UserRole,
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


# ---------------------------------------------------------------------------
# Pipeline summary
# ---------------------------------------------------------------------------


def test_pipeline_summary_no_pipelines_returns_empty(client: TestClient) -> None:
    response = client.get(
        "/api/dashboard/pipeline-summary", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    assert response.json() == []


def test_pipeline_summary_counts_stages(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Regression — the first version of this endpoint referenced
    `PipelineStage.is_archived` (only exists on
    `ContactPipelineStage`) and `ContactPipelineStage.pipeline_stage_id`
    (the column is `stage_id`). Both raised AttributeError → 500."""
    with session_factory() as session:
        user_id = session.scalar(select(User.id).where(User.role == UserRole.USER))
        pipeline = Pipeline(name="Ventas", owner_user_id=user_id)
        session.add(pipeline)
        session.flush()
        stage_a = PipelineStage(
            pipeline_id=pipeline.id, name="Lead", position=0
        )
        stage_b = PipelineStage(
            pipeline_id=pipeline.id, name="Cliente", position=1
        )
        session.add_all([stage_a, stage_b])
        session.flush()
        contact = Contact(
            first_name="Pepe",
            email="pepe@example.com",
            owner_user_id=user_id,
        )
        session.add(contact)
        session.flush()
        session.add(
            ContactPipelineStage(
                contact_id=contact.id,
                pipeline_id=pipeline.id,
                stage_id=stage_a.id,
            )
        )
        session.commit()

    response = client.get(
        "/api/dashboard/pipeline-summary", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    counts = {s["name"]: s["count"] for s in body[0]["stages"]}
    assert counts == {"Lead": 1, "Cliente": 0}


# ---------------------------------------------------------------------------
# Leads stats
# ---------------------------------------------------------------------------


def test_leads_stats_no_contacts_returns_zeros(client: TestClient) -> None:
    """Empty database used to raise 500 because of timezone
    comparison. Should return a clean zero-shaped response."""
    response = client.get(
        "/api/dashboard/leads-stats?range=30d&bucket=day",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["totals"]["leads_current"] == 0
    assert body["totals"]["leads_previous"] == 0
    assert body["totals"]["delta_pct"] is None
    assert body["series"] == []


def test_leads_stats_tolerates_naive_created_at(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """MySQL returns timezone-naive DATETIME values even when the
    SQLAlchemy column is declared `timezone=True`. The handler must
    not blow up when comparing those to tz-aware boundaries."""
    with session_factory() as session:
        # Created 2 days ago, written as a naive datetime to mimic the
        # MySQL driver behaviour.
        naive = (datetime.now(UTC) - timedelta(days=2)).replace(tzinfo=None)
        session.add(
            Contact(
                first_name="Naive",
                email="naive@example.com",
                created_at=naive,
                updated_at=naive,
            )
        )
        session.commit()
    response = client.get(
        "/api/dashboard/leads-stats?range=30d&bucket=day",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["totals"]["leads_current"] >= 1
