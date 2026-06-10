"""Brevo historical backfill — idempotent insert, unknown-email
skipping, status filter, error capture."""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.integrations.brevo.historical_backfill import (
    backfill_account_campaigns,
    backfill_campaign_events,
)
from app.models.brevo import BrevoCampaignCache
from app.models.crm import ActivityEvent, Contact, ExternalSystem
from app.models.integration_settings import IntegrationAccount


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


def _seed_contacts(session, *emails: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for email in emails:
        contact = Contact(first_name=email.split("@")[0].title(), email=email)
        session.add(contact)
        session.flush()
        out[email] = contact.id
    return out


def _seed_campaign(
    session,
    *,
    brevo_id: int = 42,
    name: str = "Verano 2026",
    status: str = "sent",
    sent_at: datetime | None = None,
) -> BrevoCampaignCache:
    row = BrevoCampaignCache(
        brevo_account_id="main",
        brevo_campaign_id=brevo_id,
        name=name,
        subject=f"{name} — subject",
        status=status,
        type="classic",
        sent_at=sent_at or datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        cached_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


class _FakeBrevoClient:
    """In-memory replay of Brevo's per-event recipients endpoints."""

    by_campaign_event: dict[tuple[int, str], list[dict[str, Any]]] = {}
    raise_for_events: set[tuple[int, str]] = set()

    def __init__(self, session, account_id, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get_campaign_recipients_stats(
        self, campaign_id, event_type, *, limit=500, offset=0
    ):
        key = (int(campaign_id), event_type)
        if key in _FakeBrevoClient.raise_for_events:
            from app.integrations.errors import IntegrationClientError

            raise IntegrationClientError(
                "boom", system="brevo", account_id="main", status_code=502
            )
        rows = _FakeBrevoClient.by_campaign_event.get(key, [])
        sliced = rows[offset : offset + limit]
        return {"recipients": sliced, "count": len(rows)}


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeBrevoClient.by_campaign_event = {}
    _FakeBrevoClient.raise_for_events = set()


def _patch_client():
    return patch(
        "app.integrations.brevo.historical_backfill.BrevoClient",
        _FakeBrevoClient,
    )


# ---------------------------------------------------------------------------
# unit tests
# ---------------------------------------------------------------------------


def test_inserts_one_activity_event_per_recipient_event(session_factory):
    """Happy path: two recipients, two event types → four rows with
    the right event_type mapping and timestamps."""
    with session_factory() as session:
        contacts = _seed_contacts(
            session, "ana@example.com", "boris@example.com"
        )
        campaign = _seed_campaign(session)
        session.commit()

        _FakeBrevoClient.by_campaign_event = {
            (42, "opened"): [
                {"email": "ana@example.com", "openedAt": "2026-05-02T08:30:00Z"},
                {"email": "boris@example.com", "openedAt": "2026-05-02T08:31:00Z"},
            ],
            (42, "clicked"): [
                {"email": "ana@example.com", "clickedAt": "2026-05-02T08:35:00Z", "url": "https://mbo/x"},
            ],
        }
        with _patch_client():
            stats = backfill_campaign_events(
                session, account_id="main", campaign_id=campaign.id
            )
        session.commit()

    assert stats["events_inserted"] == 3
    assert stats["events_skipped_existing"] == 0
    assert stats["contacts_unknown"] == 0

    with session_factory() as session:
        rows = list(session.scalars(select(ActivityEvent)))
    assert len(rows) == 3
    event_types = {row.event_type for row in rows}
    assert event_types == {"email.opened", "email.clicked"}

    ana_id = contacts["ana@example.com"]
    ana_clicked = next(
        r for r in rows if r.contact_id == ana_id and r.event_type == "email.clicked"
    )
    assert ana_clicked.body == "https://mbo/x"
    assert ana_clicked.subject == "Verano 2026 — subject"
    # External id is deterministic and dedup-friendly.
    assert ana_clicked.external_id.startswith("backfill:42:ana@example.com:")
    # Timestamp came from `clickedAt`, not the campaign sent_at fallback.
    assert ana_clicked.occurred_at.hour == 8
    assert ana_clicked.occurred_at.minute == 35


def test_second_run_is_idempotent(session_factory):
    """Re-running must not duplicate events — the UNIQUE constraint
    catches the second insert and the row is counted as 'already
    there', not inserted nor failed."""
    with session_factory() as session:
        _seed_contacts(session, "ana@example.com")
        campaign = _seed_campaign(session)
        session.commit()

        _FakeBrevoClient.by_campaign_event = {
            (42, "delivered"): [{"email": "ana@example.com"}],
        }
        with _patch_client():
            first = backfill_campaign_events(
                session, account_id="main", campaign_id=campaign.id
            )
            session.commit()
            second = backfill_campaign_events(
                session, account_id="main", campaign_id=campaign.id
            )
            session.commit()
        rows = list(session.scalars(select(ActivityEvent)))

    assert first["events_inserted"] == 1
    assert second["events_inserted"] == 0
    assert second["events_skipped_existing"] == 1
    assert len(rows) == 1


def test_unknown_email_is_counted_not_created(session_factory):
    """Backfill must never create contacts (consistent with the
    webhook receiver's rule). Unknown emails are skipped + counted."""
    with session_factory() as session:
        _seed_contacts(session, "ana@example.com")
        campaign = _seed_campaign(session)
        session.commit()

        _FakeBrevoClient.by_campaign_event = {
            (42, "opened"): [
                {"email": "ana@example.com", "openedAt": "2026-05-02T08:00:00Z"},
                {"email": "stranger@unknown.invalid", "openedAt": "2026-05-02T08:01:00Z"},
            ],
        }
        with _patch_client():
            stats = backfill_campaign_events(
                session, account_id="main", campaign_id=campaign.id
            )
            session.commit()

        assert stats["events_inserted"] == 1
        assert stats["contacts_unknown"] == 1
        # No Contact row was created for the stranger.
        assert session.scalar(
            select(Contact).where(Contact.email == "stranger@unknown.invalid")
        ) is None


def test_falls_back_to_campaign_sent_at_when_event_has_no_timestamp(
    session_factory,
):
    """Some Brevo event payloads (delivered in particular) come
    without a per-row timestamp. The fallback is the campaign's
    `sent_at`, never `datetime.now()`."""
    with session_factory() as session:
        _seed_contacts(session, "ana@example.com")
        sent_at = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
        campaign = _seed_campaign(session, sent_at=sent_at)
        session.commit()

        _FakeBrevoClient.by_campaign_event = {
            (42, "delivered"): [{"email": "ana@example.com"}],
        }
        with _patch_client():
            backfill_campaign_events(
                session, account_id="main", campaign_id=campaign.id
            )
            session.commit()

        row = session.scalar(select(ActivityEvent))
        # `sent_at`, not `now()`. SQLite doesn't preserve tzinfo on
        # round-trip, so compare in UTC.
        stored = row.occurred_at
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=UTC)
        assert stored == sent_at


def test_skips_campaigns_not_in_sent_or_archive_status(session_factory):
    with session_factory() as session:
        _seed_contacts(session, "ana@example.com")
        campaign = _seed_campaign(session, status="draft")
        session.commit()

        with _patch_client():
            stats = backfill_campaign_events(
                session, account_id="main", campaign_id=campaign.id
            )
            session.commit()
        assert stats["skipped"] is True
        assert stats["reason"] == "not_sent"
        assert session.scalar(select(ActivityEvent)) is None


def test_error_on_one_event_does_not_abort_the_rest(session_factory):
    with session_factory() as session:
        _seed_contacts(session, "ana@example.com")
        campaign = _seed_campaign(session)
        session.commit()

        _FakeBrevoClient.by_campaign_event = {
            (42, "delivered"): [{"email": "ana@example.com"}],
            (42, "opened"): [{"email": "ana@example.com"}],
        }
        # The client raises a non-404 IntegrationClientError for
        # opened — the backfill must still process delivered and
        # collect the error.
        from app.integrations.errors import IntegrationClientError

        async def fake_fetch(client, cid, event, sem):
            if event == "opened":
                raise IntegrationClientError(
                    "boom", system="brevo", account_id="main", status_code=502
                )
            return _FakeBrevoClient.by_campaign_event.get((cid, event), [])

        with (
            _patch_client(),
            patch(
                "app.integrations.brevo.historical_backfill._fetch_event_recipients",
                side_effect=fake_fetch,
            ),
        ):
            stats = backfill_campaign_events(
                session, account_id="main", campaign_id=campaign.id
            )
            session.commit()

        # `delivered` landed, `opened` recorded in errors.
        assert stats["events_inserted"] == 1
        assert any("opened" in err for err in stats["errors"])


def test_account_runner_processes_sent_only_ordered_by_sent_at(
    session_factory,
):
    """`backfill_account_campaigns` walks the cache in `sent_at`
    descending order and respects max_campaigns."""
    with session_factory() as session:
        _seed_contacts(session, "ana@example.com")
        _seed_campaign(
            session,
            brevo_id=10,
            sent_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
            name="Old",
        )
        _seed_campaign(
            session,
            brevo_id=11,
            sent_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
            name="Mid",
        )
        _seed_campaign(
            session,
            brevo_id=12,
            sent_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            name="New",
        )
        # A draft campaign that must be skipped.
        _seed_campaign(session, brevo_id=99, status="draft", name="Draft")
        session.commit()

        # Only the newest two get processed; each contributes one event.
        _FakeBrevoClient.by_campaign_event = {
            (12, "delivered"): [{"email": "ana@example.com"}],
            (11, "delivered"): [{"email": "ana@example.com"}],
        }
        with _patch_client():
            stats = backfill_account_campaigns(
                session, account_id="main", max_campaigns=2
            )
        assert stats["campaigns_processed"] == 2
        assert stats["events_inserted_total"] == 2
        # Order is newest first → 'New' then 'Mid'.
        names = [item["campaign_name"] for item in stats["per_campaign"]]
        assert names == ["New", "Mid"]


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(session_factory):
    from fastapi.testclient import TestClient

    from app.db.session import get_session
    from app.main import app
    from tests._test_helpers import seed_test_users

    with session_factory() as session:
        seed_test_users(session)

    def override_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_post_endpoint_enqueues_admin_only(client):
    from tests._test_helpers import auth_headers

    with patch("app.api.brevo.enqueue_sync_job") as fake:
        fake.return_value = ("log-1", "job-1")
        ok = client.post(
            "/api/brevo/historical-backfill?account_id=main",
            headers=auth_headers(client, "admin"),
        )
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"sync_log_id": "log-1", "job_id": "job-1"}
    kwargs = fake.call_args.kwargs
    assert kwargs["operation"] == "historical_backfill"
    assert kwargs["account_id"] == "main"

    forbidden = client.post(
        "/api/brevo/historical-backfill?account_id=main",
        headers=auth_headers(client, "manager"),
    )
    assert forbidden.status_code == 403


def test_post_endpoint_passes_max_campaigns_payload(client):
    from tests._test_helpers import auth_headers

    with patch("app.api.brevo.enqueue_sync_job") as fake:
        fake.return_value = ("log-2", "job-2")
        client.post(
            "/api/brevo/historical-backfill?account_id=main&max_campaigns=50",
            headers=auth_headers(client, "admin"),
        )
    assert fake.call_args.kwargs["payload"] == {"max_campaigns": 50}


def test_status_endpoint_returns_never_with_no_log(client):
    from tests._test_helpers import auth_headers

    response = client.get(
        "/api/brevo/historical-backfill/status?account_id=main",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "never"}
