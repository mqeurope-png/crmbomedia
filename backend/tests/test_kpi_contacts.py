"""PR-Bugs-4-5amp-7-9 — endpoints de listas de contactos por KPI.

Cubre los dos nuevos endpoints que alimentan las páginas dedicadas:

  - GET /api/dashboard/my-campaign-contacts/{kpi}
  - GET /api/brevo/campaigns/{campaign_id}/contacts/{kpi}

Ambos devuelven shape `PriorityLead` para que el frontend reuse el
mismo `ContactKpiTable` que `/dashboard/leads-prioritarios`.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.db.session import get_session
from app.main import app
from app.models.brevo import BrevoCampaignCache
from app.models.crm import (
    ActivityEvent,
    Base,
    Contact,
    ContactAssignment,
    ExternalSystem,
    User,
    UserRole,
)
from app.models.integration_settings import IntegrationAccount, IntegrationMode
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sf() as seed:
        seed_test_users(seed)
        seed.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="main",
                display_name="Brevo main",
                enabled=True,
                mode=IntegrationMode.LIVE,
                api_key_encrypted=crypto.encrypt("dummy"),
            )
        )
        seed.commit()
    yield sf
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Dashboard /my-campaign-contacts/{kpi}
# ---------------------------------------------------------------------------


def _seed_user_campaign_scenario(
    factory: sessionmaker,
) -> tuple[str, str, str, int]:
    """Seed an admin user + a Brevo campaign sent inside the 30d
    window + a contact primary-assigned to the admin with one delivered
    + one opened event for that campaign. Returns
    (admin_id, contact_delivered_id, contact_opened_id,
    brevo_campaign_id)."""
    now = datetime.now(UTC)
    with factory() as session:
        admin = session.scalar(
            select(User).where(User.role == UserRole.ADMIN)
        )
        admin_id = admin.id

        campaign = BrevoCampaignCache(
            brevo_account_id="main",
            brevo_campaign_id=42,
            name="C42",
            status="sent",
            type="classic",
            sent_at=now - timedelta(days=2),
            cached_at=now,
        )
        session.add(campaign)

        # Contact A — delivered only.
        a_id = str(uuid4())
        session.add(
            Contact(
                id=a_id,
                first_name="Alice",
                email="a@x.com",
                owner_user_id=admin_id,
                is_active=True,
            )
        )
        session.add(
            ContactAssignment(
                contact_id=a_id,
                user_id=admin_id,
                is_primary=True,
                assigned_at=now - timedelta(days=5),
                source="manual",
            )
        )
        session.add(
            ActivityEvent(
                contact_id=a_id,
                system="brevo",
                account_id="main",
                event_type="email.delivered",
                campaign_brevo_id=42,
                occurred_at=now - timedelta(days=2),
            )
        )

        # Contact B — opened (also implies delivered).
        b_id = str(uuid4())
        session.add(
            Contact(
                id=b_id,
                first_name="Bob",
                email="b@x.com",
                owner_user_id=admin_id,
                is_active=True,
            )
        )
        session.add(
            ContactAssignment(
                contact_id=b_id,
                user_id=admin_id,
                is_primary=True,
                assigned_at=now - timedelta(days=5),
                source="manual",
            )
        )
        session.add(
            ActivityEvent(
                contact_id=b_id,
                system="brevo",
                account_id="main",
                event_type="email.opened",
                campaign_brevo_id=42,
                occurred_at=now - timedelta(days=1),
            )
        )
        session.commit()
        return admin_id, a_id, b_id, 42


def test_my_campaign_contacts_received_includes_open_and_delivered(
    client: TestClient, factory: sessionmaker
) -> None:
    _, a_id, b_id, _ = _seed_user_campaign_scenario(factory)
    resp = client.get(
        "/api/dashboard/my-campaign-contacts/received?period=30d",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()}
    assert ids == {a_id, b_id}


def test_my_campaign_contacts_opened_excludes_pure_delivered(
    client: TestClient, factory: sessionmaker
) -> None:
    _, a_id, b_id, _ = _seed_user_campaign_scenario(factory)
    resp = client.get(
        "/api/dashboard/my-campaign-contacts/opened?period=30d",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()}
    # `a` only has a delivered event → not in opened bucket.
    assert ids == {b_id}


def test_my_campaign_contacts_rejects_unknown_kpi(
    client: TestClient, factory: sessionmaker
) -> None:
    resp = client.get(
        "/api/dashboard/my-campaign-contacts/respuestas",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 400, resp.text


def test_my_campaign_contacts_shape_matches_priority_lead(
    client: TestClient, factory: sessionmaker
) -> None:
    _seed_user_campaign_scenario(factory)
    resp = client.get(
        "/api/dashboard/my-campaign-contacts/received?period=30d",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert rows
    expected_keys = {
        "id",
        "first_name",
        "last_name",
        "email",
        "phone",
        "signal_at",
        "reason",
        "lead_score",
        "tags",
        "owner_user_id",
        "owner_name",
    }
    assert expected_keys.issubset(rows[0].keys())


# ---------------------------------------------------------------------------
# Brevo /campaigns/{id}/contacts/{kpi}
# ---------------------------------------------------------------------------


def _seed_campaign_contacts(
    factory: sessionmaker,
) -> tuple[str, int, list[str]]:
    """Seed a campaign + 3 contacts each with different event types
    for the same campaign. Returns
    (campaign_cache_id, brevo_campaign_id, [contact_ids])."""
    now = datetime.now(UTC)
    with factory() as session:
        campaign = BrevoCampaignCache(
            brevo_account_id="main",
            brevo_campaign_id=99,
            name="C99",
            status="sent",
            type="classic",
            sent_at=now - timedelta(hours=2),
            cached_at=now,
        )
        session.add(campaign)
        session.flush()
        campaign_id = campaign.id

        contact_ids = []
        admin = session.scalar(
            select(User).where(User.role == UserRole.ADMIN)
        )
        admin_id = admin.id
        for label, event_type in (
            ("Delivered", "email.delivered"),
            ("Opened", "email.opened"),
            ("Clicked", "email.clicked"),
        ):
            cid = str(uuid4())
            session.add(
                Contact(
                    id=cid,
                    first_name=label,
                    email=f"{label.lower()}@x.com",
                    owner_user_id=admin_id,
                    is_active=True,
                )
            )
            session.add(
                ActivityEvent(
                    contact_id=cid,
                    system="brevo",
                    account_id="main",
                    event_type=event_type,
                    campaign_brevo_id=99,
                    occurred_at=now,
                )
            )
            contact_ids.append(cid)
        session.commit()
        return campaign_id, 99, contact_ids


def test_campaign_contacts_delivered_includes_open_and_click(
    client: TestClient, factory: sessionmaker
) -> None:
    campaign_id, _, [d_id, o_id, c_id] = _seed_campaign_contacts(factory)
    resp = client.get(
        f"/api/brevo/campaigns/{campaign_id}/contacts/delivered",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()}
    # delivered KPI rolls up open/click as Brevo's semantic.
    assert ids == {d_id, o_id, c_id}


def test_campaign_contacts_clicked_returns_only_click_event(
    client: TestClient, factory: sessionmaker
) -> None:
    campaign_id, _, [_, _, c_id] = _seed_campaign_contacts(factory)
    resp = client.get(
        f"/api/brevo/campaigns/{campaign_id}/contacts/clicked",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()}
    assert ids == {c_id}


def test_campaign_contacts_rejects_unknown_kpi(
    client: TestClient, factory: sessionmaker
) -> None:
    campaign_id, _, _ = _seed_campaign_contacts(factory)
    resp = client.get(
        f"/api/brevo/campaigns/{campaign_id}/contacts/respuestas",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 400, resp.text


def test_campaign_contacts_404_on_missing_campaign(
    client: TestClient, factory: sessionmaker
) -> None:
    resp = client.get(
        "/api/brevo/campaigns/does-not-exist/contacts/delivered",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 404, resp.text
