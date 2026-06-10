"""Brevo historical backfill — async export flow, idempotency,
unknown-email skipping, status filter, error capture, polling
schedule and timeout."""
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
    EVENT_TYPE_MAP,
    _parse_export_csv,
    _wait_for_export,
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


def _csv(*emails: str) -> bytes:
    """Build a Brevo-shaped CSV: UTF-8 BOM, semicolon delimiter and
    the real `Email_ID` column header."""
    body = "Campaign ID;Campaign Name;Email_ID;Send_Date\r\n" + "".join(
        f"42;Verano 2026;{email};03-10-2025 10:45:06\r\n" for email in emails
    )
    return b"\xef\xbb\xbf" + body.encode("utf-8")


class _FakeBrevoClient:
    """In-memory replay of Brevo's async export flow.

    Tests populate the class-attribute maps; the worker drives the
    backfill with no actual HTTP I/O. `process_status_sequence` lets a
    test simulate `queued → in_process → completed` polling without
    blocking on real timers."""

    by_campaign_recipients: dict[tuple[int, str], bytes] = {}
    raise_for_start: set[tuple[int, str]] = set()
    aborted_for: set[tuple[int, str]] = set()
    process_status_sequence: list[str] = []
    next_process_id: int = 1000

    def __init__(self, session, account_id, **kwargs):
        self._session = session
        self._pending: dict[int, tuple[int, str]] = {}
        self._poll_calls: dict[int, int] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def start_recipients_export(self, campaign_id, recipients_type):
        key = (int(campaign_id), recipients_type)
        if key in _FakeBrevoClient.raise_for_start:
            from app.integrations.errors import IntegrationClientError

            raise IntegrationClientError(
                "boom",
                system="brevo",
                account_id="main",
                status_code=404,
            )
        _FakeBrevoClient.next_process_id += 1
        process_id = _FakeBrevoClient.next_process_id
        self._pending[process_id] = key
        return process_id

    async def get_process_status(self, process_id):
        key = self._pending.get(int(process_id))
        if key is None:
            return {"status": "aborted"}
        if key in _FakeBrevoClient.aborted_for:
            return {"status": "aborted"}
        seq = list(_FakeBrevoClient.process_status_sequence)
        if seq:
            i = self._poll_calls.get(int(process_id), 0)
            self._poll_calls[int(process_id)] = i + 1
            status = seq[min(i, len(seq) - 1)]
            if status != "completed":
                return {"status": status}
        return {
            "status": "completed",
            "exportUrl": f"https://download/test/{process_id}.csv",
        }

    async def download_csv_export(self, export_url):
        """Resolve the URL back to the (campaign, recipients_type) key
        that issued the export and return the matching CSV. Buckets
        without a registered CSV come back as an empty file — that
        mirrors Brevo behaviour when nobody opened/clicked/bounced."""
        try:
            pid = int(export_url.rsplit("/", 1)[-1].split(".")[0])
        except (ValueError, IndexError):
            return b""
        key = self._pending.get(pid)
        if key is None:
            return b""
        return _FakeBrevoClient.by_campaign_recipients.get(key, b"")


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeBrevoClient.by_campaign_recipients = {}
    _FakeBrevoClient.raise_for_start = set()
    _FakeBrevoClient.aborted_for = set()
    _FakeBrevoClient.process_status_sequence = []
    _FakeBrevoClient.next_process_id = 1000


def _patch_client():
    return patch(
        "app.integrations.brevo.historical_backfill.BrevoClient",
        _FakeBrevoClient,
    )


# Skip the inter-call sleep + polling waits — tests would otherwise
# block on the 1 s pacing between recipientsType buckets.
@pytest.fixture(autouse=True)
def _patch_sleep():
    async def _noop(_seconds):
        return None

    with patch(
        "app.integrations.brevo.historical_backfill.asyncio.sleep",
        new=_noop,
    ):
        yield


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------


def test_parse_csv_strips_bom_and_dedupes_emails():
    raw = (
        b"\xef\xbb\xbfCampaign ID;Campaign Name;Email_ID;Send_Date\r\n"
        b"42;Verano 2026;Ana@example.com;03-10-2025 10:45:06\r\n"
        b"42;Verano 2026;ana@example.com;03-10-2025 10:45:06\r\n"
        b"42;Verano 2026;boris@example.com;03-10-2025 10:45:06\r\n"
        b"42;Verano 2026;;03-10-2025 10:45:06\r\n"
    )
    emails = _parse_export_csv(raw)
    assert emails == ["ana@example.com", "boris@example.com"]


def test_parse_csv_returns_empty_on_blank_bytes():
    assert _parse_export_csv(b"") == []


# ---------------------------------------------------------------------------
# Full flow
# ---------------------------------------------------------------------------


def test_full_flow_inserts_one_event_per_known_contact(session_factory):
    """1 campaign × 5 recipientsType, every CSV carries 3 emails of
    which 2 match CRM contacts → 5 × 2 = 10 events inserted, 5 × 1 = 5
    contacts_unknown counted, no contacts created."""
    with session_factory() as session:
        _seed_contacts(session, "ana@example.com", "boris@example.com")
        campaign = _seed_campaign(session)
        session.commit()

        csv_bytes = _csv(
            "ana@example.com", "boris@example.com", "stranger@unknown.invalid"
        )
        _FakeBrevoClient.by_campaign_recipients = {
            (42, recipients_type): csv_bytes
            for recipients_type in EVENT_TYPE_MAP
        }

        with _patch_client():
            stats = backfill_campaign_events(
                session, account_id="main", campaign_id=campaign.id
            )
            session.commit()

    assert stats["events_inserted"] == 10
    assert stats["events_skipped_existing"] == 0
    assert stats["contacts_unknown"] == 5

    with session_factory() as session:
        rows = list(session.scalars(select(ActivityEvent)))
        # No stranger contact was created.
        assert (
            session.scalar(
                select(Contact).where(
                    Contact.email == "stranger@unknown.invalid"
                )
            )
            is None
        )

    assert len(rows) == 10
    assert {row.event_type for row in rows} == set(EVENT_TYPE_MAP.values())
    # occurred_at is anchored on the campaign's sent_at (the export has
    # no per-event timestamp).
    for row in rows:
        stored = row.occurred_at
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=UTC)
        assert stored == datetime(2026, 5, 1, 10, 0, tzinfo=UTC)


def test_second_run_is_idempotent(session_factory):
    """Re-running must not duplicate events — the UNIQUE constraint
    catches the second insert and the row is counted as 'already
    there', not inserted nor failed."""
    with session_factory() as session:
        _seed_contacts(session, "ana@example.com")
        campaign = _seed_campaign(session)
        session.commit()

        _FakeBrevoClient.by_campaign_recipients = {
            (42, "openers"): _csv("ana@example.com"),
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
    assert rows[0].event_type == "email.opened"


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


def test_aborted_export_is_logged_and_other_buckets_continue(session_factory):
    """If Brevo aborts the export for one recipientsType, the run logs
    the failure but still processes the other buckets and the next
    campaign."""
    with session_factory() as session:
        _seed_contacts(session, "ana@example.com")
        campaign = _seed_campaign(session)
        session.commit()

        csv_bytes = _csv("ana@example.com")
        _FakeBrevoClient.by_campaign_recipients = {
            (42, recipients_type): csv_bytes
            for recipients_type in EVENT_TYPE_MAP
        }
        # The `clickers` bucket aborts in Brevo's process queue.
        _FakeBrevoClient.aborted_for = {(42, "clickers")}

        with _patch_client():
            stats = backfill_campaign_events(
                session, account_id="main", campaign_id=campaign.id
            )
            session.commit()

    # 4 buckets succeeded × 1 event each = 4 inserts; clickers failed
    # but didn't crash the run.
    assert stats["events_inserted"] == 4
    assert any("clickers" in err for err in stats["errors"])
    assert any("status='aborted'" in err for err in stats["errors"])


def test_start_export_client_error_skips_bucket(session_factory):
    """If `start_recipients_export` raises (e.g. 404 on an old
    campaign) we log + skip that bucket but keep the rest of the run
    alive."""
    with session_factory() as session:
        _seed_contacts(session, "ana@example.com")
        campaign = _seed_campaign(session)
        session.commit()

        csv_bytes = _csv("ana@example.com")
        _FakeBrevoClient.by_campaign_recipients = {
            (42, recipients_type): csv_bytes
            for recipients_type in EVENT_TYPE_MAP
        }
        _FakeBrevoClient.raise_for_start = {(42, "hardBounces")}

        with _patch_client():
            stats = backfill_campaign_events(
                session, account_id="main", campaign_id=campaign.id
            )
            session.commit()

    assert stats["events_inserted"] == 4
    assert any("hardBounces" in err for err in stats["errors"])


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

        csv_bytes = _csv("ana@example.com")
        # Only the openers bucket carries data — the other 4 export
        # buckets return empty CSVs (default for the fake).
        _FakeBrevoClient.by_campaign_recipients = {
            (12, "openers"): csv_bytes,
            (11, "openers"): csv_bytes,
        }

        with _patch_client():
            stats = backfill_account_campaigns(
                session, account_id="main", max_campaigns=2
            )

        assert stats["campaigns_processed"] == 2
        # Both processed campaigns contributed 1 opener event each.
        assert stats["events_inserted_total"] == 2
        # Order is newest first → 'New' then 'Mid'.
        names = [item["campaign_name"] for item in stats["per_campaign"]]
        assert names == ["New", "Mid"]


# ---------------------------------------------------------------------------
# Polling primitives
# ---------------------------------------------------------------------------


class _PollingClient:
    """Stub `get_process_status` only — `_wait_for_export` is tested
    directly."""

    def __init__(self, statuses: list[dict[str, Any]]):
        self.statuses = list(statuses)
        self.calls = 0

    async def get_process_status(self, process_id):
        self.calls += 1
        return self.statuses[min(self.calls - 1, len(self.statuses) - 1)]


def test_wait_for_export_progresses_through_queued_in_process_completed():
    """Polling honours the adaptive schedule and stops as soon as
    `status=completed` lands."""
    import asyncio

    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    client = _PollingClient(
        [
            {"status": "queued"},
            {"status": "in_process"},
            {"status": "completed", "exportUrl": "https://x/y.csv"},
        ]
    )
    body = asyncio.run(
        _wait_for_export(
            client,  # type: ignore[arg-type]
            process_id=99,
            timeout_seconds=300.0,
            poll_schedule=(5.0, 10.0),
            sleeper=fake_sleep,
        )
    )
    assert body["status"] == "completed"
    # Three polls fired → two sleeps between them.
    assert client.calls == 3
    assert waits == [5.0, 10.0]


def test_wait_for_export_times_out_when_status_never_lands():
    """If the process never reaches a terminal state, the timeout is
    enforced and `TimeoutError` surfaces."""
    import asyncio

    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        # Push the event-loop clock forward so the deadline check
        # actually fires — `asyncio.get_event_loop().time()` lags
        # otherwise during fast-forwarded tests.
        waits.append(seconds)
        await asyncio.sleep(0)

    # Trick the deadline math: pretend the loop's time advances by the
    # nominal wait each time we "sleep". We use a counter on the
    # client + a monkeypatched loop.time to keep things deterministic.
    fake_clock = {"now": 0.0}

    async def advancing_sleep(seconds: float) -> None:
        waits.append(seconds)
        fake_clock["now"] += seconds

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        original_time = loop.time
        loop.time = lambda: original_time() + fake_clock["now"]  # type: ignore[assignment]
        client = _PollingClient([{"status": "in_process"}])
        with pytest.raises(TimeoutError):
            loop.run_until_complete(
                _wait_for_export(
                    client,  # type: ignore[arg-type]
                    process_id=99,
                    timeout_seconds=60.0,
                    poll_schedule=(30.0, 30.0, 30.0),
                    sleeper=advancing_sleep,
                )
            )
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


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
