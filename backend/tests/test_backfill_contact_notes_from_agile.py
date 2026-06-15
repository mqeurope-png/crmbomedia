"""Tests for the `--account-id` filter on the Agile notes backfill.

Sprint Empresas — mini-PR follow-up to sub-PR 4. Bart needs to be
able to backfill account-by-account (start with the tiny ones for
visual validation, spread the 7 independent rate-limit budgets,
re-launch a single account after a partial failure). The two
tests below pin that:

1. No filter → every Agile `ExternalReference` is processed.
2. `--account-id mbolasers` (single) → only the matching rows.
"""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.crm import (
    Base,
    Contact,
    ExternalReference,
    ExternalSystem,
)


@dataclass
class _Fixture:
    engine: Engine
    factory: sessionmaker


@pytest.fixture()
def db() -> Generator[_Fixture, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield _Fixture(engine=engine, factory=factory)
    Base.metadata.drop_all(engine)


def _seed_contacts(factory: sessionmaker) -> dict[str, list[str]]:
    """Two contacts in `mbolasers`, one in `mboprinters`, one in `default`.
    Returns `{account_id: [external_id, ...]}` so the assertions can
    check exactly which external ids the Agile client was called with."""
    by_account: dict[str, list[str]] = {
        "mbolasers": ["mbo-1", "mbo-2"],
        "mboprinters": ["mbop-9"],
        "default": ["def-42"],
    }
    with factory() as session:
        for account_id, external_ids in by_account.items():
            for external_id in external_ids:
                contact = Contact(
                    first_name=f"C-{external_id}",
                    email=f"{external_id}@example.com",
                    tags="",
                    commercial_status="new",
                )
                session.add(contact)
                session.flush()
                session.add(
                    ExternalReference(
                        system=ExternalSystem.AGILECRM,
                        account_id=account_id,
                        external_id=external_id,
                        contact_id=contact.id,
                    )
                )
        session.commit()
    return by_account


class _FakeClient:
    """In-process stand-in for `AgileCRMClient`. Records every
    `get_contact` call so the test can assert on the external ids
    the backfill walked — the dedupe / insert path is already
    covered by `test_contact_notes.py`, so this client just
    returns an empty `properties` list (nothing to insert).
    """

    calls: list[tuple[str, str]] = []  # (account_id, external_id)

    def __init__(self, session: Session, account_id: str) -> None:
        self.account_id = account_id

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def get_contact(self, external_id: str) -> dict[str, Any]:
        type(self).calls.append((self.account_id, external_id))
        return {"id": external_id, "properties": []}


@pytest.fixture()
def fake_client() -> Generator[type[_FakeClient], None, None]:
    _FakeClient.calls = []
    with (
        patch(
            "scripts.backfill_contact_notes_from_agile.AgileCRMClient",
            _FakeClient,
        ),
        patch(
            "scripts.backfill_contact_notes_from_agile.get_engine",
        ) as mock_get_engine,
    ):
        # `get_engine` is replaced per-test by the fixture below.
        mock_get_engine.return_value = None
        yield _FakeClient


def _run(db: _Fixture, **kwargs: Any) -> dict[str, int]:
    """Drive the backfill against the test engine. `get_engine` is
    patched in `fake_client`; we point it at the in-memory one
    here so the script's own session sees the seeded rows."""
    from scripts import backfill_contact_notes_from_agile  # noqa: PLC0415

    with patch.object(
        backfill_contact_notes_from_agile,
        "get_engine",
        return_value=db.engine,
    ):
        return backfill_contact_notes_from_agile.backfill(**kwargs)


def test_backfill_no_filter_processes_all_accounts(
    db: _Fixture, fake_client: type[_FakeClient]
) -> None:
    _seed_contacts(db.factory)
    summary = _run(db, dry_run=True)
    assert summary["scanned"] == 4
    assert summary["fetched"] == 4
    visited = sorted(fake_client.calls)
    assert visited == sorted(
        [
            ("mbolasers", "mbo-1"),
            ("mbolasers", "mbo-2"),
            ("mboprinters", "mbop-9"),
            ("default", "def-42"),
        ]
    )


def test_backfill_account_filter_skips_other_accounts(
    db: _Fixture, fake_client: type[_FakeClient]
) -> None:
    _seed_contacts(db.factory)
    summary = _run(db, dry_run=True, account_ids=["mbolasers"])
    assert summary["scanned"] == 2
    assert summary["fetched"] == 2
    # Only mbolasers contacts were fetched — `mboprinters` /
    # `default` are off-scope and the client was never opened
    # against them.
    assert sorted(fake_client.calls) == [
        ("mbolasers", "mbo-1"),
        ("mbolasers", "mbo-2"),
    ]


def test_backfill_account_filter_accepts_multiple(
    db: _Fixture, fake_client: type[_FakeClient]
) -> None:
    _seed_contacts(db.factory)
    summary = _run(
        db, dry_run=True, account_ids=["mbolasers", "mboprinters"]
    )
    assert summary["scanned"] == 3
    assert summary["fetched"] == 3
    accounts = {acc for acc, _ in fake_client.calls}
    assert accounts == {"mbolasers", "mboprinters"}
    assert ("default", "def-42") not in fake_client.calls
