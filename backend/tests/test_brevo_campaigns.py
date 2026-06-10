"""Brevo campaigns — cache, creation from segment, scheduling rules."""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_session
from app.integrations.brevo.campaigns import (
    campaign_cache_is_stale,
    upsert_campaign_row,
)
from app.main import app
from app.models.brevo import BrevoCampaignCache
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationAccount
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
    with factory() as session:
        seed_test_users(session)
        session.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="main",
                display_name="Brevo",
                enabled=True,
            )
        )
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class _FakeClient:
    campaigns: dict[int, dict[str, Any]] = {}
    lists_created: list[str] = []
    list_members: dict[int, list[str]] = {}
    next_campaign_id = 500
    next_list_id = 70
    calls: list[tuple[str, Any]] = []

    def __init__(self, session, account_id, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def create_list(self, name, folder_id=None):
        lid = _FakeClient.next_list_id
        _FakeClient.next_list_id += 1
        _FakeClient.lists_created.append(name)
        _FakeClient.list_members[lid] = []
        return {"id": lid}

    async def add_contacts_to_list(self, list_id, emails):
        _FakeClient.list_members.setdefault(list_id, []).extend(emails)
        return {}

    async def create_email_campaign(self, payload):
        cid = _FakeClient.next_campaign_id
        _FakeClient.next_campaign_id += 1
        _FakeClient.campaigns[cid] = {**payload, "id": cid, "status": "draft"}
        _FakeClient.calls.append(("create_campaign", cid))
        return {"id": cid}

    async def update_email_campaign(self, campaign_id, payload):
        _FakeClient.calls.append(("update_campaign", campaign_id, payload))
        _FakeClient.campaigns.setdefault(campaign_id, {}).update(payload)

    async def delete_email_campaign(self, campaign_id):
        _FakeClient.calls.append(("delete_campaign", campaign_id))
        _FakeClient.campaigns.pop(campaign_id, None)

    async def get_email_campaign(self, campaign_id):
        _FakeClient.calls.append(("get_campaign", campaign_id))
        return _FakeClient.campaigns.get(campaign_id, {"id": campaign_id})

    async def send_email_campaign_now(self, campaign_id):
        _FakeClient.calls.append(("send_now", campaign_id))

    async def send_test_email_campaign(self, campaign_id, email_to):
        _FakeClient.calls.append(("send_test", campaign_id, tuple(email_to)))

    async def schedule_email_campaign(self, campaign_id, scheduled_at):
        _FakeClient.calls.append(("schedule", campaign_id, scheduled_at))

    async def update_campaign_status(self, campaign_id, status):
        _FakeClient.calls.append(("status", campaign_id, status))


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeClient.campaigns = {}
    _FakeClient.lists_created = []
    _FakeClient.list_members = {}
    _FakeClient.next_campaign_id = 500
    _FakeClient.next_list_id = 70
    _FakeClient.calls = []


class _patch_api:
    """Patch every BrevoClient ref the campaign endpoints reach
    (the public route + the cache helpers in `campaigns.py`)."""

    def __enter__(self):
        self.p1 = patch("app.api.brevo.BrevoClient", _FakeClient)
        self.p2 = patch(
            "app.integrations.brevo.campaigns.BrevoClient", _FakeClient
        )
        self.p1.__enter__()
        self.p2.__enter__()
        return self

    def __exit__(self, *exc):
        self.p2.__exit__(*exc)
        self.p1.__exit__(*exc)


def _seed_segment(client: TestClient) -> str:
    headers = auth_headers(client, "manager")
    client.post(
        "/api/contacts",
        json={"first_name": "Ana", "email": "ana@example.com"},
        headers=headers,
    )
    client.post(
        "/api/contacts",
        json={"first_name": "Boris", "email": "boris@example.com"},
        headers=headers,
    )
    segment = client.post(
        "/api/segments",
        json={
            "name": "Todos",
            "rules": {
                "type": "rule",
                "field": "is_active",
                "comparator": "eq",
                "value": True,
            },
        },
        headers=headers,
    ).json()
    return segment["id"]


def _campaign_payload(**overrides):
    base = {
        "brevo_account_id": "main",
        "name": "Campaña verano",
        "subject": "¡Ofertas!",
        "sender_name": "MBO",
        "sender_email": "news@mbolasers.com",
        "html_content": "<h1>Hola</h1>",
        "list_ids": [3],
    }
    base.update(overrides)
    return base


def test_create_campaign_from_segment_materialises_list(client: TestClient):
    segment_id = _seed_segment(client)
    headers = auth_headers(client, "manager")
    with _patch_api():
        response = client.post(
            "/api/brevo/campaigns",
            json=_campaign_payload(list_ids=None, segment_id=segment_id),
            headers=headers,
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "draft"
    # A list was auto-created and both segment contacts joined it.
    assert len(_FakeClient.lists_created) == 1
    assert _FakeClient.lists_created[0].startswith("crm-campaign-")
    members = _FakeClient.list_members[70]
    assert sorted(members) == ["ana@example.com", "boris@example.com"]
    assert body["recipient_list_ids"] == [70]


def test_create_campaign_requires_content_and_recipients(client: TestClient):
    headers = auth_headers(client, "manager")
    no_content = client.post(
        "/api/brevo/campaigns",
        json=_campaign_payload(html_content=None),
        headers=headers,
    )
    assert no_content.status_code == 400
    no_recipients = client.post(
        "/api/brevo/campaigns",
        json=_campaign_payload(list_ids=None),
        headers=headers,
    )
    assert no_recipients.status_code == 400


def test_send_now_only_from_draft_or_scheduled(client: TestClient):
    headers = auth_headers(client, "manager")
    with _patch_api():
        created = client.post(
            "/api/brevo/campaigns", json=_campaign_payload(), headers=headers
        ).json()
        ok = client.post(
            f"/api/brevo/campaigns/{created['id']}/send-now", headers=headers
        )
        assert ok.status_code == 200
        assert ("send_now", 500) in _FakeClient.calls
        # Now in_process → a second send must 409.
        again = client.post(
            f"/api/brevo/campaigns/{created['id']}/send-now", headers=headers
        )
        assert again.status_code == 409


def test_schedule_requires_one_hour_lead(client: TestClient):
    headers = auth_headers(client, "manager")
    with _patch_api():
        created = client.post(
            "/api/brevo/campaigns", json=_campaign_payload(), headers=headers
        ).json()
        too_soon = client.post(
            f"/api/brevo/campaigns/{created['id']}/schedule",
            json={
                "scheduled_at": (
                    datetime.now(UTC) + timedelta(minutes=10)
                ).isoformat()
            },
            headers=headers,
        )
        assert too_soon.status_code == 400
        ok = client.post(
            f"/api/brevo/campaigns/{created['id']}/schedule",
            json={
                "scheduled_at": (
                    datetime.now(UTC) + timedelta(hours=2)
                ).isoformat()
            },
            headers=headers,
        )
        assert ok.status_code == 200
    detail = client.get(
        f"/api/brevo/campaigns/{created['id']}", headers=headers
    ).json()
    assert detail["status"] == "queued"
    assert detail["scheduled_at"] is not None


def test_cancel_schedule_returns_to_draft(client: TestClient):
    headers = auth_headers(client, "manager")
    with _patch_api():
        created = client.post(
            "/api/brevo/campaigns", json=_campaign_payload(), headers=headers
        ).json()
        client.post(
            f"/api/brevo/campaigns/{created['id']}/schedule",
            json={
                "scheduled_at": (
                    datetime.now(UTC) + timedelta(hours=2)
                ).isoformat()
            },
            headers=headers,
        )
        cancelled = client.post(
            f"/api/brevo/campaigns/{created['id']}/cancel-schedule",
            headers=headers,
        )
        assert cancelled.status_code == 200
        assert ("status", 500, "draft") in _FakeClient.calls
    detail = client.get(
        f"/api/brevo/campaigns/{created['id']}", headers=headers
    ).json()
    assert detail["status"] == "draft"
    assert detail["scheduled_at"] is None


def test_edit_blocked_once_sent(client: TestClient, session_factory):
    headers = auth_headers(client, "manager")
    with _patch_api():
        created = client.post(
            "/api/brevo/campaigns", json=_campaign_payload(), headers=headers
        ).json()
    # Flip the cached status to sent directly.
    with session_factory() as session:
        row = session.get(BrevoCampaignCache, created["id"])
        row.status = "sent"
        # Refresh the cache timestamp so the detail endpoint doesn't try
        # to re-pull from (fake) Brevo.
        row.cached_at = datetime.now(UTC)
        session.commit()
    with _patch_api():
        response = client.patch(
            f"/api/brevo/campaigns/{created['id']}",
            json={"subject": "nuevo"},
            headers=headers,
        )
    assert response.status_code == 409


def test_send_test_campaign(client: TestClient):
    headers = auth_headers(client, "manager")
    with _patch_api():
        created = client.post(
            "/api/brevo/campaigns", json=_campaign_payload(), headers=headers
        ).json()
        response = client.post(
            f"/api/brevo/campaigns/{created['id']}/send-test",
            json={"emails": ["qa@mbolasers.com"]},
            headers=headers,
        )
    assert response.status_code == 200
    assert ("send_test", 500, ("qa@mbolasers.com",)) in _FakeClient.calls


def test_cache_staleness_rule(session_factory):
    with session_factory() as session:
        row = upsert_campaign_row(
            session,
            account_id="main",
            payload={"id": 9, "name": "X", "status": "sent"},
        )
        assert campaign_cache_is_stale(row) is False
        row.cached_at = datetime.now(UTC) - timedelta(minutes=6)
        assert campaign_cache_is_stale(row) is True


def test_stats_extraction_from_global_stats(session_factory):
    with session_factory() as session:
        row = upsert_campaign_row(
            session,
            account_id="main",
            payload={
                "id": 11,
                "name": "Stats",
                "status": "sent",
                "statistics": {
                    "globalStats": {
                        "sent": 100,
                        "delivered": 95,
                        "uniqueViews": 40,
                        "uniqueClicks": 12,
                        "hardBounces": 2,
                        "unsubscriptions": 1,
                    }
                },
            },
        )
        session.commit()
        import json as _json

        stats = _json.loads(row.stats_json)
        assert stats["sent"] == 100
        assert stats["uniqueViews"] == 40


def test_campaign_recipients_resolved_from_activity_events(
    client: TestClient, session_factory
):
    """The recipients tabs read webhook-fed activity_events joined to
    CRM contacts, not the Brevo API."""
    headers = auth_headers(client, "manager")
    with _patch_api():
        created = client.post(
            "/api/brevo/campaigns", json=_campaign_payload(), headers=headers
        ).json()
    with session_factory() as session:
        from app.models.crm import ActivityEvent, Contact

        row = session.get(BrevoCampaignCache, created["id"])
        ana = Contact(first_name="Ana", email="ana@example.com")
        session.add(ana)
        session.flush()
        session.add(
            ActivityEvent(
                contact_id=ana.id,
                system="brevo",
                account_id="main",
                external_id="evt-1",
                event_type="email.opened",
                campaign_brevo_id=row.brevo_campaign_id,
                occurred_at=datetime.now(UTC),
                synced_at=datetime.now(UTC),
            )
        )
        row.cached_at = datetime.now(UTC)
        session.commit()
    response = client.get(
        f"/api/brevo/campaigns/{created['id']}/recipients/opened",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["email"] == "ana@example.com"
    assert items[0]["event_type"] == "email.opened"


def test_campaign_recipients_filtered_by_campaign_brevo_id(
    client: TestClient, session_factory
):
    """Regression: the endpoint used to mix every campaign's
    recipients into the same response. Each campaign now MUST only
    surface its own recipients, identified by the new
    `activity_events.campaign_brevo_id` column."""
    headers = auth_headers(client, "manager")
    with _patch_api():
        first = client.post(
            "/api/brevo/campaigns",
            json=_campaign_payload(name="Coles aviso"),
            headers=headers,
        ).json()
        second = client.post(
            "/api/brevo/campaigns",
            json=_campaign_payload(name="Coles subvención"),
            headers=headers,
        ).json()

    with session_factory() as session:
        from app.models.crm import ActivityEvent, Contact

        first_row = session.get(BrevoCampaignCache, first["id"])
        second_row = session.get(BrevoCampaignCache, second["id"])

        oscar = Contact(first_name="Oscar", email="oscar@example.com")
        ana = Contact(first_name="Ana", email="ana@example.com")
        session.add_all([oscar, ana])
        session.flush()

        # Oscar opened only the FIRST campaign.
        session.add(
            ActivityEvent(
                contact_id=oscar.id,
                system="brevo",
                account_id="main",
                external_id=f"backfill:{first_row.brevo_campaign_id}:oscar@example.com:openers",
                event_type="email.opened",
                campaign_brevo_id=first_row.brevo_campaign_id,
                occurred_at=datetime.now(UTC),
                synced_at=datetime.now(UTC),
            )
        )
        # Ana opened only the SECOND campaign.
        session.add(
            ActivityEvent(
                contact_id=ana.id,
                system="brevo",
                account_id="main",
                external_id=f"backfill:{second_row.brevo_campaign_id}:ana@example.com:openers",
                event_type="email.opened",
                campaign_brevo_id=second_row.brevo_campaign_id,
                occurred_at=datetime.now(UTC),
                synced_at=datetime.now(UTC),
            )
        )
        # A pre-0025 row with NULL campaign_brevo_id but the backfill
        # external_id pointing at the first campaign — must still
        # surface there via the fallback LIKE.
        session.add(
            ActivityEvent(
                contact_id=oscar.id,
                system="brevo",
                account_id="main",
                external_id=f"backfill:{first_row.brevo_campaign_id}:legacy@example.com:openers",
                event_type="email.opened",
                campaign_brevo_id=None,
                occurred_at=datetime.now(UTC),
                synced_at=datetime.now(UTC),
            )
        )
        first_row.cached_at = datetime.now(UTC)
        second_row.cached_at = datetime.now(UTC)
        session.commit()

    first_resp = client.get(
        f"/api/brevo/campaigns/{first['id']}/recipients/opened",
        headers=headers,
    ).json()
    assert {item["email"] for item in first_resp["items"]} == {
        "oscar@example.com",
    }
    # The legacy NULL row also lands on the first campaign via the
    # external_id fallback (one item, but Oscar is its contact too).
    assert len(first_resp["items"]) == 2

    second_resp = client.get(
        f"/api/brevo/campaigns/{second['id']}/recipients/opened",
        headers=headers,
    ).json()
    assert {item["email"] for item in second_resp["items"]} == {
        "ana@example.com",
    }


def test_campaign_detail_lazy_loads_html(client: TestClient):
    """Regression: detail page used to land with `html_content=None`
    because the list refresh paths don't fetch it. The detail
    endpoint now triggers `ensure_campaign_html`, caches the result,
    and serves it on subsequent reads without re-hitting Brevo."""
    headers = auth_headers(client, "manager")
    with _patch_api():
        created = client.post(
            "/api/brevo/campaigns", json=_campaign_payload(), headers=headers
        ).json()
    # Simulate that Brevo will return the htmlContent on the detail
    # GET — the fake's get_email_campaign returns whatever lives in
    # `_FakeClient.campaigns`.
    _FakeClient.campaigns[500] = {
        **_FakeClient.campaigns.get(500, {}),
        "id": 500,
        "name": "Campaña verano",
        "status": "draft",
        "htmlContent": "<h1>Real HTML</h1>",
    }
    with _patch_api():
        first = client.get(
            f"/api/brevo/campaigns/{created['id']}", headers=headers
        )
        assert first.status_code == 200
        assert first.json()["html_content"] == "<h1>Real HTML</h1>"
        # Second open within the freshness window must NOT call
        # get_email_campaign again — html came from cache.
        _FakeClient.calls = []
        second = client.get(
            f"/api/brevo/campaigns/{created['id']}", headers=headers
        )
        assert second.status_code == 200
        assert second.json()["html_content"] == "<h1>Real HTML</h1>"
        assert ("get_campaign", 500) not in _FakeClient.calls


def test_stats_prefer_campaign_stats_when_global_is_zero(session_factory):
    """Production Bug 6: 'post fespa español' showed 0 everywhere
    because Brevo returned globalStats present-but-all-zero with the
    real numbers in the per-list campaignStats rows. The extractor
    must aggregate campaignStats and prefer the block with signal."""
    with session_factory() as session:
        row = upsert_campaign_row(
            session,
            account_id="main",
            payload={
                "id": 77,
                "name": "post fespa español",
                "status": "sent",
                "statistics": {
                    "globalStats": {
                        "sent": 0,
                        "delivered": 0,
                        "uniqueViews": 0,
                        "uniqueClicks": 0,
                    },
                    "campaignStats": [
                        {
                            "listId": 4,
                            "sent": 1200,
                            "delivered": 1150,
                            "uniqueViews": 480,
                            "uniqueClicks": 95,
                            "hardBounces": 12,
                            "softBounces": 8,
                            "unsubscriptions": 5,
                            "complaints": 1,
                        },
                        {
                            "listId": 7,
                            "sent": 300,
                            "delivered": 290,
                            "uniqueViews": 100,
                            "uniqueClicks": 20,
                            "hardBounces": 2,
                            "softBounces": 1,
                            "unsubscriptions": 0,
                            "complaints": 0,
                        },
                    ],
                },
            },
        )
        session.commit()
        import json as _json

        stats = _json.loads(row.stats_json)
        # Summed across both lists.
        assert stats["sent"] == 1500
        assert stats["delivered"] == 1440
        assert stats["uniqueViews"] == 580
        assert stats["uniqueClicks"] == 115
        assert stats["hardBounces"] == 14
        assert stats["unsubscriptions"] == 5


def test_stats_keep_global_when_it_carries_data(session_factory):
    """Regression guard: globalStats with real numbers still wins
    over an absent/zero campaignStats."""
    with session_factory() as session:
        row = upsert_campaign_row(
            session,
            account_id="main",
            payload={
                "id": 78,
                "name": "Global",
                "status": "sent",
                "statistics": {
                    "globalStats": {"sent": 100, "delivered": 95, "uniqueViews": 40},
                    "campaignStats": [],
                },
            },
        )
        session.commit()
        import json as _json

        stats = _json.loads(row.stats_json)
        assert stats["sent"] == 100
        assert stats["delivered"] == 95
