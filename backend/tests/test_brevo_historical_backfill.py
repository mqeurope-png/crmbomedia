"""Brevo historical backfill v3 — single `all` export per campaign,
column-based event detection, real timestamps, idempotency, polling
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
    _extract_events_from_row,
    _parse_brevo_csv_date,
    _parse_export_rows,
    _wait_for_export,
    backfill_account_campaigns,
    backfill_campaign_events,
)
from app.models.brevo import BrevoCampaignCache
from app.models.crm import ActivityEvent, Contact, ExternalSystem
from app.models.integration_settings import IntegrationAccount

# Verbatim Brevo export header (semicolon-delimited; the trailing
# column is a dynamic per-link column we must ignore).
CSV_HEADER = (
    "Campaign ID;Campaign Name;Email_ID;Send_Date;Delivered_Date;"
    "Open_Date;Total Opens;Total Apple MPP Opens;Unsubscribe_Date;"
    "Hard_Bounce_Date;Hard_Bounce_Reason;Soft_Bounce_Date;"
    "Soft_Bounce_Reason;Open_IP;Click_IP;Unsubscribe_IP;"
    "Clicked_Links_Count;Complaint_date;https://mbo.example/promo"
)

COLUMNS = CSV_HEADER.split(";")


def _csv_row(**overrides: str) -> str:
    """One CSV line with every cell empty except the overrides.
    Column keys use the verbatim Brevo header names."""
    cells = {column: "" for column in COLUMNS}
    cells["Campaign ID"] = "42"
    cells["Campaign Name"] = "Verano 2026"
    cells.update(overrides)
    return ";".join(cells[column] for column in COLUMNS)


def _csv_bytes(*rows: str) -> bytes:
    body = "\r\n".join([CSV_HEADER, *rows]) + "\r\n"
    return b"\xef\xbb\xbf" + body.encode("utf-8")


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
    """In-memory replay of Brevo's async export flow — ONE export per
    campaign with recipientsType=all."""

    csv_by_campaign: dict[int, bytes] = {}
    raise_for_campaigns: set[int] = set()
    aborted_campaigns: set[int] = set()
    process_status_sequence: list[str] = []
    requested: list[tuple[int, str]] = []
    next_process_id: int = 1000

    def __init__(self, session, account_id, **kwargs):
        self._pending: dict[int, int] = {}
        self._poll_calls: dict[int, int] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def start_recipients_export(self, campaign_id, recipients_type):
        _FakeBrevoClient.requested.append((int(campaign_id), recipients_type))
        if int(campaign_id) in _FakeBrevoClient.raise_for_campaigns:
            from app.integrations.errors import IntegrationClientError

            raise IntegrationClientError(
                "boom",
                system="brevo",
                account_id="main",
                status_code=404,
            )
        _FakeBrevoClient.next_process_id += 1
        process_id = _FakeBrevoClient.next_process_id
        self._pending[process_id] = int(campaign_id)
        return process_id

    async def get_process_status(self, process_id):
        campaign_id = self._pending.get(int(process_id))
        if campaign_id is None:
            return {"status": "aborted"}
        if campaign_id in _FakeBrevoClient.aborted_campaigns:
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
        try:
            pid = int(export_url.rsplit("/", 1)[-1].split(".")[0])
        except (ValueError, IndexError):
            return b""
        campaign_id = self._pending.get(pid)
        if campaign_id is None:
            return b""
        return _FakeBrevoClient.csv_by_campaign.get(campaign_id, b"")


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeBrevoClient.csv_by_campaign = {}
    _FakeBrevoClient.raise_for_campaigns = set()
    _FakeBrevoClient.aborted_campaigns = set()
    _FakeBrevoClient.process_status_sequence = []
    _FakeBrevoClient.requested = []
    _FakeBrevoClient.next_process_id = 1000


def _patch_client():
    return patch(
        "app.integrations.brevo.historical_backfill.BrevoClient",
        _FakeBrevoClient,
    )


# Skip the inter-call sleep + polling waits — tests would otherwise
# block on the 1 s pacing between campaigns.
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
# Date parser
# ---------------------------------------------------------------------------


def test_parse_brevo_csv_date_day_first_utc():
    parsed = _parse_brevo_csv_date("03-10-2025 10:45:06")
    assert parsed == datetime(2025, 10, 3, 10, 45, 6, tzinfo=UTC)


@pytest.mark.parametrize("raw", ["", "   ", None, "not-a-date", "2025-10-03"])
def test_parse_brevo_csv_date_empty_or_malformed_is_none(raw):
    assert _parse_brevo_csv_date(raw) is None


# ---------------------------------------------------------------------------
# Row → events extraction
# ---------------------------------------------------------------------------


def _row_dict(**overrides: str) -> dict[str, str]:
    row = {column: "" for column in COLUMNS}
    row.update(overrides)
    return row


def test_extract_events_delivered_only_row_yields_only_delivered():
    """The contamination regression: a recipient who merely received
    the campaign must produce email.delivered and NOTHING else."""
    row = _row_dict(
        Email_ID="glopezm27@gmail.com",
        Send_Date="03-10-2025 10:45:06",
        Delivered_Date="03-10-2025 10:45:13",
        **{"Total Opens": "0", "Clicked_Links_Count": "0"},
    )
    events = _extract_events_from_row(row, fallback_dt=None)
    assert [event_type for event_type, _, _ in events] == ["email.delivered"]
    assert events[0][1] == datetime(2025, 10, 3, 10, 45, 13, tzinfo=UTC)


def test_extract_events_engaged_row_yields_delivered_opened_clicked():
    row = _row_dict(
        Email_ID="ana@example.com",
        Send_Date="03-10-2025 10:45:06",
        Delivered_Date="03-10-2025 10:45:13",
        Open_Date="03-10-2025 10:46:39",
        **{"Total Opens": "3", "Clicked_Links_Count": "2"},
    )
    events = {event_type: (ts, extra) for event_type, ts, extra in
              _extract_events_from_row(row, fallback_dt=None)}
    assert set(events) == {"email.delivered", "email.opened", "email.clicked"}
    # Real timestamps from the CSV.
    assert events["email.opened"][0] == datetime(2025, 10, 3, 10, 46, 39, tzinfo=UTC)
    assert events["email.opened"][1] == {"total_opens": 3}
    # Click has no own date column — the open timestamp approximates it.
    assert events["email.clicked"][0] == datetime(2025, 10, 3, 10, 46, 39, tzinfo=UTC)
    assert events["email.clicked"][1] == {"clicked_links_count": 2}


def test_extract_events_bounce_and_unsubscribe_and_complaint():
    row = _row_dict(
        Email_ID="boris@example.com",
        Send_Date="03-10-2025 10:45:06",
        Hard_Bounce_Date="03-10-2025 10:45:20",
        Unsubscribe_Date="04-10-2025 09:00:00",
        Soft_Bounce_Date="03-10-2025 10:45:21",
        Complaint_date="05-10-2025 12:00:00",
    )
    events = {event_type for event_type, _, _ in
              _extract_events_from_row(row, fallback_dt=None)}
    assert events == {
        "email.bounced_hard",
        "email.bounced_soft",
        "email.unsubscribed",
        "email.spam_complaint",
    }


def test_extract_clicked_falls_back_to_delivered_then_send_date():
    # No Open_Date → Delivered_Date approximates the click.
    row = _row_dict(
        Email_ID="x@y.z",
        Send_Date="03-10-2025 10:45:06",
        Delivered_Date="03-10-2025 10:45:13",
        Clicked_Links_Count="1",
    )
    events = dict(
        (event_type, ts) for event_type, ts, _ in
        _extract_events_from_row(row, fallback_dt=None)
    )
    assert events["email.clicked"] == datetime(2025, 10, 3, 10, 45, 13, tzinfo=UTC)

    # No dates at all → campaign fallback.
    fallback = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    row = _row_dict(Email_ID="x@y.z", Clicked_Links_Count="1")
    events = dict(
        (event_type, ts) for event_type, ts, _ in
        _extract_events_from_row(row, fallback_dt=fallback)
    )
    assert events["email.clicked"] == fallback


def test_extract_open_date_wins_over_zero_total_opens():
    """Brevo sometimes reports Total Opens=0 with Open_Date set; the
    date column is the source of truth."""
    row = _row_dict(
        Email_ID="x@y.z",
        Open_Date="03-10-2025 10:46:39",
        **{"Total Opens": "0"},
    )
    events = [event_type for event_type, _, _ in
              _extract_events_from_row(row, fallback_dt=None)]
    assert "email.opened" in events


def test_parse_export_rows_strips_bom_and_keeps_dynamic_columns_unread():
    raw = _csv_bytes(
        _csv_row(Email_ID="Ana@example.com", Send_Date="03-10-2025 10:45:06"),
        _csv_row(Email_ID="", Send_Date="03-10-2025 10:45:06"),
    )
    rows = _parse_export_rows(raw)
    assert len(rows) == 2
    assert rows[0]["Email_ID"] == "Ana@example.com"
    assert rows[0]["Campaign Name"] == "Verano 2026"
    assert _parse_export_rows(b"") == []


# ---------------------------------------------------------------------------
# Full flow
# ---------------------------------------------------------------------------


def test_full_flow_single_export_column_based_events(session_factory):
    """One campaign, one `all` export. The CSV carries 4 recipients:
    - ana: delivered + opened + clicked  → 3 events
    - oscar: delivered only              → 1 event (regression case)
    - boris: hard bounce, never delivered → 1 event
    - stranger: not a CRM contact        → contacts_unknown
    """
    with session_factory() as session:
        contacts = _seed_contacts(
            session,
            "ana@example.com",
            "glopezm27@gmail.com",
            "boris@example.com",
        )
        campaign = _seed_campaign(session)
        session.commit()

        _FakeBrevoClient.csv_by_campaign = {
            42: _csv_bytes(
                _csv_row(
                    Email_ID="Ana@example.com",
                    Send_Date="03-10-2025 10:45:06",
                    Delivered_Date="03-10-2025 10:45:13",
                    Open_Date="03-10-2025 10:46:39",
                    **{"Total Opens": "1", "Clicked_Links_Count": "1"},
                ),
                _csv_row(
                    Email_ID="glopezm27@gmail.com",
                    Send_Date="03-10-2025 10:45:06",
                    Delivered_Date="03-10-2025 10:45:12",
                    **{"Total Opens": "0", "Clicked_Links_Count": "0"},
                ),
                _csv_row(
                    Email_ID="boris@example.com",
                    Send_Date="03-10-2025 10:45:06",
                    Hard_Bounce_Date="03-10-2025 10:45:20",
                ),
                _csv_row(
                    Email_ID="stranger@unknown.invalid",
                    Send_Date="03-10-2025 10:45:06",
                    Delivered_Date="03-10-2025 10:45:14",
                ),
            )
        }

        with _patch_client():
            stats = backfill_campaign_events(
                session, account_id="main", campaign_id=campaign.id
            )
            session.commit()

    # Exactly ONE export was requested, with recipientsType=all.
    assert _FakeBrevoClient.requested == [(42, "all")]
    assert stats["events_inserted"] == 5
    assert stats["contacts_unknown"] == 1
    assert stats["errors"] == []

    with session_factory() as session:
        rows = list(session.scalars(select(ActivityEvent)))
        assert (
            session.scalar(
                select(Contact).where(
                    Contact.email == "stranger@unknown.invalid"
                )
            )
            is None
        )

    by_contact: dict[str, set[str]] = {}
    for event in rows:
        by_contact.setdefault(event.contact_id, set()).add(event.event_type)

    # The regression case: delivered-only recipient gets ONLY delivered.
    oscar = contacts["glopezm27@gmail.com"]
    assert by_contact[oscar] == {"email.delivered"}
    ana = contacts["ana@example.com"]
    assert by_contact[ana] == {
        "email.delivered",
        "email.opened",
        "email.clicked",
    }
    boris = contacts["boris@example.com"]
    assert by_contact[boris] == {"email.bounced_hard"}

    # Real per-event timestamps from the CSV, not the campaign sent_at.
    ana_opened = next(
        e for e in rows
        if e.contact_id == ana and e.event_type == "email.opened"
    )
    stored = ana_opened.occurred_at
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    assert stored == datetime(2025, 10, 3, 10, 46, 39, tzinfo=UTC)


def test_second_run_is_idempotent(session_factory):
    with session_factory() as session:
        _seed_contacts(session, "ana@example.com")
        campaign = _seed_campaign(session)
        session.commit()

        _FakeBrevoClient.csv_by_campaign = {
            42: _csv_bytes(
                _csv_row(
                    Email_ID="ana@example.com",
                    Send_Date="03-10-2025 10:45:06",
                    Delivered_Date="03-10-2025 10:45:13",
                ),
            )
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
    assert rows[0].event_type == "email.delivered"


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


def test_aborted_export_logs_error_and_next_campaign_continues(
    session_factory,
):
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
        session.commit()

        delivered_row = _csv_row(
            Email_ID="ana@example.com",
            Send_Date="03-10-2025 10:45:06",
            Delivered_Date="03-10-2025 10:45:13",
        )
        _FakeBrevoClient.csv_by_campaign = {
            10: _csv_bytes(delivered_row),
            11: _csv_bytes(delivered_row),
        }
        # The newest campaign's export aborts in Brevo's queue.
        _FakeBrevoClient.aborted_campaigns = {11}

        with _patch_client():
            stats = backfill_account_campaigns(session, account_id="main")

    # 'Old' (10) landed its event despite 'Mid' (11) aborting first.
    assert stats["events_inserted_total"] == 1
    assert any("aborted" in err for err in stats["errors"])
    assert any("campaign=11" in err for err in stats["errors"])


def test_account_runner_processes_sent_only_ordered_by_sent_at(
    session_factory,
):
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
        _seed_campaign(session, brevo_id=99, status="draft", name="Draft")
        session.commit()

        delivered_row = _csv_row(
            Email_ID="ana@example.com",
            Send_Date="03-10-2025 10:45:06",
            Delivered_Date="03-10-2025 10:45:13",
        )
        _FakeBrevoClient.csv_by_campaign = {
            12: _csv_bytes(delivered_row),
            11: _csv_bytes(delivered_row),
        }

        with _patch_client():
            stats = backfill_account_campaigns(
                session, account_id="main", max_campaigns=2
            )

        assert stats["campaigns_processed"] == 2
        assert stats["events_inserted_total"] == 2
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
    assert client.calls == 3
    assert waits == [5.0, 10.0]


def test_wait_for_export_times_out_when_status_never_lands():
    import asyncio

    waits: list[float] = []
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
