"""End-to-end tests for `sync_agilecrm_contacts` and
`purge_agilecrm_quota`.

We mock the `AgileCRMClient` so the tests don't need a real httpx
transport — the surface under test is the dedup / quota logic, not the
HTTP client (covered separately in `test_agilecrm_client.py`).
"""
from __future__ import annotations

from collections.abc import Generator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.core.audit import Action
from app.integrations.agilecrm.jobs import (
    purge_agilecrm_quota,
    sync_agilecrm_contacts,
)
from app.models.crm import (
    AuditLog,
    Base,
    Contact,
    ExternalReference,
    ExternalSystem,
    SyncLog,
    SyncStatus,
    SyncTrigger,
)
from app.models.integration_settings import (
    IntegrationAccount,
    QuotaStrategy,
)


def _make_payload(*, contact_id: int, email: str, first_name: str = "Ana") -> dict[str, Any]:
    return {
        "id": contact_id,
        "tags": [],
        "properties": [
            {"name": "first_name", "value": first_name},
            {"name": "email", "value": email},
        ],
    }


class _FakeClient:
    """Drop-in replacement for `AgileCRMClient`. The test prepares a list
    of pages (each page is a list of payloads) and the fake replays
    them through `list_contacts`. `count_contacts` returns the sum.
    `delete_contact` records ids for later assertion."""

    def __init__(
        self,
        pages: list[list[dict[str, Any]]],
        *,
        count: int | None = None,
        count_unavailable: bool = False,
        notes_by_contact: dict[str, list[dict[str, Any]]] | None = None,
        tasks_by_contact: dict[str, list[dict[str, Any]]] | None = None,
        activities_by_contact: dict[str, list[dict[str, Any]]] | None = None,
        notes_error_for: set[str] | None = None,
    ) -> None:
        self._pages = list(pages)
        self._count: int | None = (
            None if count_unavailable
            else (count if count is not None else sum(len(p) for p in pages))
        )
        self.deleted: list[str] = []
        self._notes = notes_by_contact or {}
        self._tasks = tasks_by_contact or {}
        self._activities = activities_by_contact or {}
        self._notes_error_for = notes_error_for or set()
        # AsyncMock would be enough, but a tiny hand-rolled class makes
        # the test reads cleaner.

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def list_contacts(
        self,
        *,
        page_size: int | None = None,
        cursor: str | None = None,
        order_by: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not self._pages:
            return [], None
        page = self._pages.pop(0)
        return page, ("next" if self._pages else None)

    async def get_contact(self, external_id: str) -> dict[str, Any] | None:
        return None

    async def delete_contact(self, external_id: str) -> None:
        self.deleted.append(external_id)

    async def count_contacts(self) -> int | None:
        return self._count

    async def list_contact_notes(self, contact_id: str) -> list[dict[str, Any]]:
        if contact_id in self._notes_error_for:
            raise RuntimeError(f"simulated notes failure for {contact_id}")
        return list(self._notes.get(str(contact_id), []))

    async def list_contact_tasks(self, contact_id: str) -> list[dict[str, Any]]:
        return list(self._tasks.get(str(contact_id), []))

    async def list_contact_activities(self, contact_id: str) -> list[dict[str, Any]]:
        return list(self._activities.get(str(contact_id), []))


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sf() as session:
        session.add(
            IntegrationAccount(
                system=ExternalSystem.AGILECRM,
                account_id="es",
                display_name="AgileCRM España",
                enabled=True,
                credential_status="configured",
                api_key_encrypted=crypto.encrypt("ops@example.com:secret"),
            )
        )
        session.commit()
    yield sf
    Base.metadata.drop_all(engine)


def _new_sync_log(session: Session, *, operation: str, account_id: str = "es") -> SyncLog:
    sync_log = SyncLog(
        system=ExternalSystem.AGILECRM,
        account_id=account_id,
        operation=operation,
        status=SyncStatus.RUNNING.value,
        triggered_by=SyncTrigger.MANUAL.value,
    )
    session.add(sync_log)
    session.flush()
    return sync_log


def _patch_client(fake: _FakeClient):
    """Patch the `AgileCRMClient` name resolved inside jobs.py."""

    @asynccontextmanager
    async def fake_ctx(_session, _account_id):
        async with fake:
            yield fake

    # `AgileCRMClient(session, account_id)` is used as `async with` so
    # we replace the class with a callable that returns the fake.
    return patch(
        "app.integrations.agilecrm.jobs.AgileCRMClient",
        side_effect=lambda session, account_id: fake,
    )


# ---------------------------------------------------------------------------
# sync_contacts
# ---------------------------------------------------------------------------


def test_sync_creates_new_contact_and_external_reference(factory: sessionmaker):
    fake = _FakeClient([[_make_payload(contact_id=1, email="ana@example.com")]])
    with factory() as session, _patch_client(fake):
        sync_log = _new_sync_log(session, operation="sync_contacts")
        outcome = sync_agilecrm_contacts(session, sync_log)

    assert outcome.records_processed == 1
    assert outcome.records_failed == 0
    with factory() as session:
        contacts = session.query(Contact).all()
        assert len(contacts) == 1
        assert contacts[0].email == "ana@example.com"
        refs = session.query(ExternalReference).all()
        assert len(refs) == 1
        assert refs[0].external_id == "1"
        assert refs[0].account_id == "es"


def test_sync_updates_existing_reference(factory: sessionmaker):
    with factory() as session:
        contact = Contact(first_name="Old", email="ana@example.com")
        session.add(contact)
        session.flush()
        session.add(
            ExternalReference(
                system=ExternalSystem.AGILECRM,
                account_id="es",
                external_id="1",
                contact_id=contact.id,
            )
        )
        session.commit()

    fake = _FakeClient(
        [[_make_payload(contact_id=1, email="ana@example.com", first_name="Updated")]]
    )
    with factory() as session, _patch_client(fake):
        sync_log = _new_sync_log(session, operation="sync_contacts")
        sync_agilecrm_contacts(session, sync_log)

    with factory() as session:
        contact = session.query(Contact).one()
        assert contact.first_name == "Updated"
        # No duplicate references.
        assert session.query(ExternalReference).count() == 1


def test_sync_consolidates_duplicate_email_from_another_account(factory: sessionmaker):
    """Email collision across two AgileCRM accounts: the contact must
    NOT be duplicated; instead a second `external_references` row links
    the existing internal contact to the new account."""
    # Seed a contact already linked to AgileCRM UK.
    with factory() as session:
        session.add(
            IntegrationAccount(
                system=ExternalSystem.AGILECRM,
                account_id="uk",
                display_name="AgileCRM UK",
                enabled=True,
                credential_status="configured",
                api_key_encrypted=crypto.encrypt("ops@example.com:secret-uk"),
            )
        )
        contact = Contact(first_name="Ana", email="ana@example.com")
        session.add(contact)
        session.flush()
        session.add(
            ExternalReference(
                system=ExternalSystem.AGILECRM,
                account_id="uk",
                external_id="99",
                contact_id=contact.id,
            )
        )
        session.commit()

    # Now sync the ES account and get the same email back from AgileCRM
    # ES (external_id is different — AgileCRM IDs aren't shared across
    # accounts).
    fake = _FakeClient([[_make_payload(contact_id=1, email="ana@example.com")]])
    with factory() as session, _patch_client(fake):
        sync_log = _new_sync_log(session, operation="sync_contacts")
        sync_agilecrm_contacts(session, sync_log)

    with factory() as session:
        contacts = session.query(Contact).all()
        assert len(contacts) == 1, "should not duplicate the contact"
        refs = sorted(
            session.query(ExternalReference).all(), key=lambda r: r.account_id
        )
        assert [(r.account_id, r.external_id) for r in refs] == [
            ("es", "1"),
            ("uk", "99"),
        ]


def test_sync_collects_per_record_errors_without_aborting(factory: sessionmaker):
    """A payload without an email must be flagged in `error_summary`
    while the rest of the page succeeds."""
    fake = _FakeClient(
        [
            [
                {"id": 1, "properties": [{"name": "first_name", "value": "Ana"}]},  # no email
                _make_payload(contact_id=2, email="ok@example.com"),
            ]
        ]
    )
    with factory() as session, _patch_client(fake):
        sync_log = _new_sync_log(session, operation="sync_contacts")
        outcome = sync_agilecrm_contacts(session, sync_log)

    assert outcome.records_processed == 2
    assert outcome.records_failed == 1
    assert outcome.error_summary is not None
    assert "contact_id=1" in outcome.error_summary

    with factory() as session:
        # The well-formed one was inserted.
        assert session.query(Contact).count() == 1
        assert session.query(Contact).one().email == "ok@example.com"


def test_sync_emits_integration_api_call_audit_events_indirectly(factory: sessionmaker):
    """When the handler runs it emits `integration.sync_*` events
    through the worker wrapper — this handler doesn't emit them, but
    we can at least confirm that any audit row we DO emit is tagged
    correctly. Currently sync_contacts itself doesn't emit; the parent
    `run_sync_job` does. So this test just confirms the handler
    completes without writing stray audit rows."""
    fake = _FakeClient([[_make_payload(contact_id=1, email="ana@example.com")]])
    with factory() as session, _patch_client(fake):
        sync_log = _new_sync_log(session, operation="sync_contacts")
        sync_agilecrm_contacts(session, sync_log)

    with factory() as session:
        actions = {row.action for row in session.query(AuditLog).all()}
        # No spurious audit rows from the handler itself.
        assert Action.INTEGRATION_SYNC_TRIGGERED not in actions
        assert Action.INTEGRATION_SYNC_STARTED not in actions


# ---------------------------------------------------------------------------
# Per-contact sub-syncs
# ---------------------------------------------------------------------------


def test_sync_imports_notes_tasks_and_activities_per_contact(factory: sessionmaker):
    """A green-field sync writes notes, tasks and activity_events for
    each contact alongside the contact + external_reference."""
    from app.models.crm import ActivityEvent, Note, Task

    fake = _FakeClient(
        [[_make_payload(contact_id=1, email="ana@example.com")]],
        notes_by_contact={
            "1": [
                {
                    "id": 10,
                    "subject": "Llamada",
                    "description": "Quiere renovar",
                    "created_time": 1750000000,
                    "owner": {"email": "ops@example.com", "name": "Ops"},
                }
            ]
        },
        tasks_by_contact={
            "1": [
                {
                    "id": 20,
                    "subject": "Enviar propuesta",
                    "status": "YET_TO_START",
                    "due": 1750100000,
                }
            ]
        },
        activities_by_contact={
            "1": [
                {
                    "id": 30,
                    "activity_type": "EMAIL_SENT",
                    "time": 1750200000,
                    "label": "Welcome",
                    "description": "Opened",
                }
            ]
        },
    )
    with factory() as session, _patch_client(fake):
        sync_log = _new_sync_log(session, operation="sync_contacts")
        outcome = sync_agilecrm_contacts(session, sync_log)

    assert outcome.metadata["notes_synced"] == 1
    assert outcome.metadata["tasks_synced"] == 1
    assert outcome.metadata["activities_synced"] == 1
    with factory() as session:
        note = session.query(Note).one()
        assert note.body.startswith("Llamada")
        assert note.external_id == "10"
        assert note.external_author_email == "ops@example.com"
        task = session.query(Task).one()
        assert task.title == "Enviar propuesta"
        assert task.external_id == "20"
        event = session.query(ActivityEvent).one()
        assert event.event_type == "EMAIL_SENT"
        assert event.external_id == "30"
        assert event.subject == "Welcome"


def test_sync_dedups_sub_resources_on_re_run(factory: sessionmaker):
    """Running the sync twice must not double-write notes/tasks/events
    — the unique-by-external-id helpers upsert in place."""
    from app.models.crm import ActivityEvent, Note, Task

    fake_factory = lambda: _FakeClient(  # noqa: E731 - one-line builder
        [[_make_payload(contact_id=1, email="ana@example.com")]],
        notes_by_contact={
            "1": [
                {
                    "id": 10,
                    "subject": "Llamada",
                    "description": "Quiere renovar",
                    "created_time": 1750000000,
                }
            ]
        },
        tasks_by_contact={
            "1": [{"id": 20, "subject": "Enviar propuesta"}]
        },
        activities_by_contact={
            "1": [
                {
                    "id": 30,
                    "activity_type": "EMAIL_SENT",
                    "time": 1750200000,
                    "label": "Welcome",
                }
            ]
        },
    )

    for _ in range(2):
        fake = fake_factory()
        with factory() as session, _patch_client(fake):
            sync_log = _new_sync_log(session, operation="sync_contacts")
            sync_agilecrm_contacts(session, sync_log)

    with factory() as session:
        assert session.query(Note).count() == 1
        assert session.query(Task).count() == 1
        assert session.query(ActivityEvent).count() == 1


def test_sync_tolerates_subresource_failure_without_aborting_contact(
    factory: sessionmaker,
):
    """If notes fetch blows up for one contact, the contact must still
    land in the DB (the contact upsert already committed) and the rest
    of the sync carries on."""
    fake = _FakeClient(
        [
            [
                _make_payload(contact_id=1, email="ana@example.com"),
                _make_payload(contact_id=2, email="boris@example.com"),
            ]
        ],
        notes_error_for={"1"},
        notes_by_contact={"2": [{"id": 999, "subject": "OK", "description": "fine"}]},
    )
    with factory() as session, _patch_client(fake):
        sync_log = _new_sync_log(session, operation="sync_contacts")
        outcome = sync_agilecrm_contacts(session, sync_log)

    assert outcome.records_processed == 2
    with factory() as session:
        emails = sorted(c.email for c in session.query(Contact).all())
        assert emails == ["ana@example.com", "boris@example.com"]


# ---------------------------------------------------------------------------
# purge_quota
# ---------------------------------------------------------------------------


def _seed_account_with_quota(
    session: Session, *, quota: int, strategy: QuotaStrategy
) -> None:
    account = session.query(IntegrationAccount).filter_by(account_id="es").one()
    account.quota_max_contacts = quota
    account.quota_strategy = strategy
    session.commit()


def test_purge_noop_when_under_quota(factory: sessionmaker):
    fake = _FakeClient([], count=10)
    with factory() as session:
        _seed_account_with_quota(session, quota=50, strategy=QuotaStrategy.KEEP_NEWEST)
    with factory() as session, _patch_client(fake):
        sync_log = _new_sync_log(session, operation="purge_quota")
        outcome = purge_agilecrm_quota(session, sync_log)

    assert outcome.records_processed == 0
    assert fake.deleted == []


def test_purge_skips_when_quota_strategy_none(factory: sessionmaker):
    fake = _FakeClient([], count=999)
    with factory() as session:
        _seed_account_with_quota(session, quota=10, strategy=QuotaStrategy.NONE)
    with factory() as session, _patch_client(fake):
        sync_log = _new_sync_log(session, operation="purge_quota")
        outcome = purge_agilecrm_quota(session, sync_log)
    assert outcome.records_processed == 0
    assert fake.deleted == []


def test_purge_keep_newest_deletes_excess(factory: sessionmaker):
    payloads = [_make_payload(contact_id=i, email=f"u{i}@example.com") for i in range(1, 7)]
    fake = _FakeClient([payloads], count=6)
    with factory() as session:
        _seed_account_with_quota(session, quota=4, strategy=QuotaStrategy.KEEP_NEWEST)
    with factory() as session, _patch_client(fake):
        sync_log = _new_sync_log(session, operation="purge_quota")
        outcome = purge_agilecrm_quota(session, sync_log)

    assert outcome.records_processed == 2  # 6 remote - 4 quota
    assert fake.deleted == ["1", "2"]  # The first items returned (oldest, per order_by)
    with factory() as session:
        audit = {row.action for row in session.query(AuditLog).all()}
        assert Action.INTEGRATION_QUOTA_DELETED in audit


def test_purge_marks_existing_references_as_deleted_in_origin(factory: sessionmaker):
    """If a contact already has an external_reference for the purged
    account, the row stays around with `external_status='deleted_in_origin'`."""
    with factory() as session:
        _seed_account_with_quota(session, quota=1, strategy=QuotaStrategy.KEEP_NEWEST)
        contact = Contact(first_name="Ana", email="ana@example.com")
        session.add(contact)
        session.flush()
        session.add(
            ExternalReference(
                system=ExternalSystem.AGILECRM,
                account_id="es",
                external_id="1",
                contact_id=contact.id,
            )
        )
        session.commit()

    fake = _FakeClient(
        [
            [
                _make_payload(contact_id=1, email="ana@example.com"),
                _make_payload(contact_id=2, email="other@example.com"),
            ]
        ],
        count=2,
    )
    with factory() as session, _patch_client(fake):
        sync_log = _new_sync_log(session, operation="purge_quota")
        purge_agilecrm_quota(session, sync_log)

    with factory() as session:
        ref = session.query(ExternalReference).one()
        assert ref.external_status == "deleted_in_origin"
        # Contact itself is never deleted from the CRM.
        assert session.query(Contact).count() == 1


def test_purge_logs_warning_and_skips_when_count_unavailable(
    factory: sessionmaker, caplog
):
    """When AgileCRM's count endpoint refuses to answer (e.g. 400 from
    the tenant), the job must skip the purge cleanly: no deletions on
    the remote, no contact loss, a WARNING in the worker logs and a
    `skip_reason=count_unavailable` flag in the sync_log metadata."""
    import logging

    fake = _FakeClient([], count_unavailable=True)
    with factory() as session:
        _seed_account_with_quota(session, quota=4, strategy=QuotaStrategy.KEEP_NEWEST)

    with caplog.at_level(logging.WARNING, logger="app.integrations.agilecrm.jobs"):
        with factory() as session, _patch_client(fake):
            sync_log = _new_sync_log(session, operation="purge_quota")
            outcome = purge_agilecrm_quota(session, sync_log)

    assert outcome.records_processed == 0
    assert outcome.records_failed == 0
    assert outcome.metadata is not None
    assert outcome.metadata.get("skip_reason") == "count_unavailable"
    assert fake.deleted == []
    warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "count_contacts unavailable" in r.message
    ]
    assert warnings, "expected a WARNING for the unreachable AgileCRM count endpoint"
