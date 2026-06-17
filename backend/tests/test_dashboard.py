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
from app.repositories import assignments as assignments_repo
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
        # Sprint Reglas-Assign PR-B: el widget mira contact_assignments
        # (EXISTS) en vez del caché owner_user_id. La fila multi-comercial
        # es la fuente de verdad.
        assignments_repo.add_assignment(
            session,
            contact_id=contact.id,
            user_id=user_id,
            is_primary=True,
        )
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


# ---------------------------------------------------------------------------
# PR-E3 — upcoming-tasks / priority-leads / user-campaign-stats /
# recent-interactions
# ---------------------------------------------------------------------------


def _user_id(session_factory: sessionmaker, role: UserRole) -> str:
    with session_factory() as session:
        return session.scalar(select(User.id).where(User.role == role))


def test_upcoming_tasks_only_future_open(
    client: TestClient, session_factory: sessionmaker
) -> None:
    from app.models.crm import Task, TaskStatus

    uid = _user_id(session_factory, UserRole.USER)
    now = datetime.now(UTC)
    with session_factory() as session:
        session.add_all(
            [
                Task(
                    title="Futura",
                    assigned_user_id=uid,
                    created_by_user_id=uid,
                    status=TaskStatus.PENDING,
                    due_at=now + timedelta(days=2),
                ),
                Task(
                    title="Vencida",
                    assigned_user_id=uid,
                    created_by_user_id=uid,
                    status=TaskStatus.PENDING,
                    due_at=now - timedelta(days=2),
                ),
                Task(
                    title="Completada futura",
                    assigned_user_id=uid,
                    created_by_user_id=uid,
                    status=TaskStatus.DONE,
                    due_at=now + timedelta(days=1),
                ),
            ]
        )
        session.commit()
    resp = client.get(
        "/api/dashboard/upcoming-tasks", headers=auth_headers(client, "user")
    )
    assert resp.status_code == 200
    titles = [t["title"] for t in resp.json()]
    assert titles == ["Futura"]


def test_priority_leads_tags_reason(
    client: TestClient, session_factory: sessionmaker
) -> None:
    uid = _user_id(session_factory, UserRole.USER)
    now = datetime.now(UTC)
    with session_factory() as session:
        c = Contact(
            first_name="Reciente",
            email="rec@example.com",
            is_active=True,
            created_at=now - timedelta(days=1),
        )
        session.add(c)
        session.flush()
        assignments_repo.add_assignment(
            session,
            contact_id=c.id,
            user_id=uid,
            is_primary=True,
            assigned_by_user_id=uid,
        )
        session.commit()
    resp = client.get(
        "/api/dashboard/priority-leads?period=7d",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["email"] == "rec@example.com"
    assert rows[0]["reason"] in {"recent", "assigned", "active"}


def test_user_campaign_stats_counts_primary_contacts(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Métrica PR-E3: por user primary, cuántos contactos abrieron/
    clickearon campañas enviadas en ventana. Verifica el shape +
    que cuenta los contactos primary del user."""
    import app.models.brevo  # noqa: F401
    from app.models.brevo import BrevoCampaignCache
    from app.models.crm import ActivityEvent

    uid = _user_id(session_factory, UserRole.USER)
    now = datetime.now(UTC)
    with session_factory() as session:
        session.add(
            BrevoCampaignCache(
                brevo_account_id="main",
                brevo_campaign_id=7,
                name="C7",
                status="sent",
                type="classic",
                sent_at=now - timedelta(days=2),
                cached_at=now,
            )
        )
        c = Contact(first_name="Lead", email="lead@example.com", is_active=True)
        session.add(c)
        session.flush()
        assignments_repo.add_assignment(
            session,
            contact_id=c.id,
            user_id=uid,
            is_primary=True,
            assigned_by_user_id=uid,
        )
        for et in ("email.delivered", "email.opened", "email.clicked"):
            session.add(
                ActivityEvent(
                    contact_id=c.id,
                    system="brevo",
                    account_id="main",
                    event_type=et,
                    external_id=f"{c.id}:{et}:7",
                    campaign_brevo_id=7,
                    occurred_at=now - timedelta(days=1),
                )
            )
        session.commit()
    resp = client.get(
        "/api/dashboard/user-campaign-stats?period=30d",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    rows = resp.json()
    mine = [r for r in rows if r["user_id"] == uid]
    assert len(mine) == 1
    assert mine[0]["received"] == 1
    assert mine[0]["opened"] == 1
    assert mine[0]["clicked"] == 1
    assert mine[0]["open_rate"] == 100.0
    assert mine[0]["click_rate"] == 100.0


def test_user_campaign_stats_period_excludes_old(
    client: TestClient, session_factory: sessionmaker
) -> None:
    import app.models.brevo  # noqa: F401
    from app.models.brevo import BrevoCampaignCache
    from app.models.crm import ActivityEvent

    uid = _user_id(session_factory, UserRole.USER)
    now = datetime.now(UTC)
    with session_factory() as session:
        session.add(
            BrevoCampaignCache(
                brevo_account_id="main",
                brevo_campaign_id=9,
                name="Vieja",
                status="sent",
                type="classic",
                sent_at=now - timedelta(days=40),
                cached_at=now,
            )
        )
        c = Contact(first_name="L", email="l@example.com", is_active=True)
        session.add(c)
        session.flush()
        assignments_repo.add_assignment(
            session,
            contact_id=c.id,
            user_id=uid,
            is_primary=True,
            assigned_by_user_id=uid,
        )
        session.add(
            ActivityEvent(
                contact_id=c.id,
                system="brevo",
                account_id="main",
                event_type="email.opened",
                external_id=f"{c.id}:opened:9",
                campaign_brevo_id=9,
                occurred_at=now - timedelta(days=39),
            )
        )
        session.commit()
    # period=30d → campaña enviada hace 40d queda fuera.
    resp = client.get(
        "/api/dashboard/user-campaign-stats?period=30d",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    assert [r for r in resp.json() if r["user_id"] == uid] == []


def test_recent_interactions_custom_requires_dates(
    client: TestClient,
) -> None:
    # custom sin start/end → cae a ventana default (no 500).
    resp = client.get(
        "/api/dashboard/recent-interactions?period=custom",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_my_campaign_stats_counts_current_user_only(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """PR-E4: nuevo endpoint /my-campaign-stats devuelve solo las
    cifras del current_user (no leaderboard). Mismo cálculo que
    /user-campaign-stats filtrado por user_id=current."""
    import app.models.brevo  # noqa: F401
    from app.models.brevo import BrevoCampaignCache
    from app.models.crm import ActivityEvent

    uid = _user_id(session_factory, UserRole.USER)
    other_uid = _user_id(session_factory, UserRole.MANAGER)
    now = datetime.now(UTC)
    with session_factory() as session:
        session.add(
            BrevoCampaignCache(
                brevo_account_id="main",
                brevo_campaign_id=11,
                name="C",
                status="sent",
                type="classic",
                sent_at=now - timedelta(days=2),
                cached_at=now,
            )
        )
        c_mine = Contact(first_name="Mio", email="mio@example.com", is_active=True)
        c_other = Contact(
            first_name="Otro", email="otro@example.com", is_active=True
        )
        session.add_all([c_mine, c_other])
        session.flush()
        assignments_repo.add_assignment(
            session,
            contact_id=c_mine.id,
            user_id=uid,
            is_primary=True,
            assigned_by_user_id=uid,
        )
        assignments_repo.add_assignment(
            session,
            contact_id=c_other.id,
            user_id=other_uid,
            is_primary=True,
            assigned_by_user_id=other_uid,
        )
        for cid in (c_mine.id, c_other.id):
            for et in ("email.delivered", "email.opened", "email.clicked"):
                session.add(
                    ActivityEvent(
                        contact_id=cid,
                        system="brevo",
                        account_id="main",
                        event_type=et,
                        external_id=f"{cid}:{et}:11",
                        campaign_brevo_id=11,
                        occurred_at=now - timedelta(days=1),
                    )
                )
        session.commit()
    resp = client.get(
        "/api/dashboard/my-campaign-stats?period=30d",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Solo cuenta el contacto del current user, no el del manager.
    assert body == {
        "received": 1,
        "opened": 1,
        "clicked": 1,
        "open_rate": 100.0,
        "click_rate": 100.0,
    }
