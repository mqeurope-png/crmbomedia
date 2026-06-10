"""Brevo read sync — upsert, consolidation, delta watermark, lock."""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.integrations.brevo.jobs import (
    sync_brevo_contacts,
    upsert_brevo_contact,
)
from app.models.crm import (
    Base,
    Contact,
    ContactTag,
    ExternalReference,
    ExternalSystem,
    SyncLog,
    SyncTrigger,
    Tag,
)
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
                display_name="Brevo principal",
                enabled=True,
            )
        )
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)


def _payload(contact_id: int, email: str, list_ids: list[int] | None = None):
    return {
        "id": contact_id,
        "email": email,
        "emailBlacklisted": False,
        "attributes": {"NOMBRE": "Ana"},
        "listIds": list_ids or [],
    }


def test_upsert_creates_contact_with_reference_and_list_tags(session_factory):
    with session_factory() as session:
        action, contact_id = upsert_brevo_contact(
            session,
            account_id="main",
            payload=_payload(1, "ana@example.com", [4]),
            list_names={4: "Newsletter"},
        )
        session.commit()
        assert action == "created"
        contact = session.get(Contact, contact_id)
        assert contact.email == "ana@example.com"
        ref = session.scalar(select(ExternalReference))
        assert ref.system == ExternalSystem.BREVO
        assert ref.external_id == "1"
        tag = session.scalar(select(Tag))
        assert tag.name == "brevo-list:Newsletter"
        link = session.scalar(select(ContactTag))
        assert link.source == "brevo:main"


def test_upsert_consolidates_by_email_with_existing_contact(session_factory):
    """A contact imported from AgileCRM gains a second external
    reference instead of duplicating when Brevo syncs the same email."""
    with session_factory() as session:
        existing = Contact(first_name="Ana", email="ana@example.com")
        session.add(existing)
        session.flush()
        session.add(
            ExternalReference(
                system=ExternalSystem.AGILECRM,
                account_id="agile-1",
                external_id="agile-77",
                contact_id=existing.id,
            )
        )
        session.commit()

        action, contact_id = upsert_brevo_contact(
            session,
            account_id="main",
            payload=_payload(9, "ana@example.com"),
        )
        session.commit()
        assert action == "updated"
        assert contact_id == existing.id
        refs = list(session.scalars(select(ExternalReference)))
        assert {(r.system, r.external_id) for r in refs} == {
            (ExternalSystem.AGILECRM, "agile-77"),
            (ExternalSystem.BREVO, "9"),
        }
        assert session.scalar(select(Contact.id).where(Contact.id != existing.id)) is None


def test_upsert_updates_existing_brevo_reference(session_factory):
    with session_factory() as session:
        upsert_brevo_contact(
            session, account_id="main", payload=_payload(1, "ana@example.com")
        )
        session.commit()
        action, _ = upsert_brevo_contact(
            session,
            account_id="main",
            payload={
                "id": 1,
                "email": "ana@example.com",
                "attributes": {"NOMBRE": "Ana María"},
                "listIds": [],
            },
        )
        session.commit()
        assert action == "updated"
        contact = session.scalar(select(Contact))
        assert contact.first_name == "Ana María"


def test_upsert_skips_contact_without_usable_email(session_factory, caplog):
    with session_factory() as session:
        with caplog.at_level("WARNING"):
            action, _ = upsert_brevo_contact(
                session,
                account_id="main",
                payload=_payload(5, "not-an-email@@broken"),
            )
        assert action == "skipped"
        assert session.scalar(select(Contact)) is None


def test_leaving_a_list_removes_the_auto_tag(session_factory):
    with session_factory() as session:
        upsert_brevo_contact(
            session,
            account_id="main",
            payload=_payload(1, "ana@example.com", [4]),
            list_names={4: "Newsletter"},
        )
        session.commit()
        assert session.scalar(select(ContactTag)) is not None

        upsert_brevo_contact(
            session,
            account_id="main",
            payload=_payload(1, "ana@example.com", []),
            list_names={4: "Newsletter"},
        )
        session.commit()
        assert session.scalar(select(ContactTag)) is None


class _FakeBrevoClient:
    """Replays prepared pages through the same surface the job uses."""

    def __init__(self, session, account_id, **kwargs):
        self.pages = list(_FakeBrevoClient.pages)

    pages: list[list[dict[str, Any]]] = []
    lists: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def list_lists(self, **kwargs):
        return {"lists": _FakeBrevoClient.lists, "count": len(_FakeBrevoClient.lists)}

    async def list_contacts(self, *, limit, offset, modified_since=None):
        _FakeBrevoClient.last_modified_since = modified_since
        index = offset // limit
        if index < len(self.pages):
            return {"contacts": self.pages[index], "count": 999}
        return {"contacts": [], "count": 999}


def _run_sync(session, *, full_sync=False) -> Any:
    log = SyncLog(
        system=ExternalSystem.BREVO,
        account_id="main",
        operation="sync_contacts",
        status="running",
        triggered_by=SyncTrigger.MANUAL.value,
        metadata_json='{"payload": {"full_sync": %s}}' % ("true" if full_sync else "false"),
    )
    session.add(log)
    session.flush()
    with (
        patch("app.integrations.brevo.jobs.BrevoClient", _FakeBrevoClient),
        patch("app.integrations.brevo.jobs._acquire_lock", return_value=True),
        patch("app.integrations.brevo.jobs._release_lock"),
    ):
        return sync_brevo_contacts(session, log)


def test_sync_creates_and_updates_across_pages(session_factory):
    _FakeBrevoClient.pages = [
        [_payload(1, "ana@example.com"), _payload(2, "boris@example.com")],
    ]
    _FakeBrevoClient.lists = [{"id": 4, "name": "Newsletter"}]
    with session_factory() as session:
        outcome = _run_sync(session, full_sync=True)
        assert outcome.records_processed == 2
        assert outcome.metadata["created"] == 2
        assert session.scalar(
            select(Contact).where(Contact.email == "boris@example.com")
        ) is not None


def test_sync_respects_lock(session_factory):
    _FakeBrevoClient.pages = []
    with session_factory() as session:
        log = SyncLog(
            system=ExternalSystem.BREVO,
            account_id="main",
            operation="sync_contacts",
            status="running",
            triggered_by=SyncTrigger.MANUAL.value,
        )
        session.add(log)
        session.flush()
        with (
            patch("app.integrations.brevo.jobs.BrevoClient", _FakeBrevoClient),
            patch("app.integrations.brevo.jobs._acquire_lock", return_value=False),
        ):
            outcome = sync_brevo_contacts(session, log)
        assert outcome.records_failed == 1
        assert "ya está en ejecución" in (outcome.error_summary or "")


def test_delta_sync_uses_watermark(session_factory):
    """A previous successful run sets the modifiedSince watermark for
    the next delta run (with a 5-minute overlap)."""
    from datetime import UTC, datetime

    _FakeBrevoClient.pages = []
    _FakeBrevoClient.lists = []
    _FakeBrevoClient.last_modified_since = None
    with session_factory() as session:
        session.add(
            SyncLog(
                system=ExternalSystem.BREVO,
                account_id="main",
                operation="sync_contacts",
                status="success",
                finished_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
            )
        )
        session.commit()
        _run_sync(session, full_sync=False)
        assert _FakeBrevoClient.last_modified_since is not None
        assert _FakeBrevoClient.last_modified_since.startswith("2026-06-01T11:55")
