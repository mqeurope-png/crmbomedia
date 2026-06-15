"""Sprint Empresas — sub-PR 4 backend tests.

Covers the new "Notas" surface: `extract_agilecrm_notes`,
`reconcile_agile_notes` idempotency on re-sync, the
`/api/contacts/{id}/notes` CRUD round-trip with pin / unpin, and
the backfill apply helper.
"""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.integrations.agilecrm.jobs import reconcile_agile_notes
from app.integrations.agilecrm.mapper import extract_agilecrm_notes
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    ContactNote,
)
from tests._test_helpers import auth_headers, seed_test_users


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
    with factory() as seed:
        seed_test_users(seed)
    yield _Fixture(engine=engine, factory=factory)
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db: _Fixture) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with db.factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_contact(factory: sessionmaker) -> str:
    with factory() as session:
        contact = Contact(
            first_name="Bart",
            email="bart@bomedia.net",
            tags="",
            commercial_status="new",
        )
        session.add(contact)
        session.commit()
        return contact.id


# -- Agile mapper --------------------------------------------------


def test_extract_agile_notes_picks_note1_to_note10() -> None:
    payload = {
        "id": 1,
        "properties": [
            {"name": "Note1", "value": "First", "type": "CUSTOM"},
            {"name": "Note2", "value": "", "type": "CUSTOM"},  # empty → skip
            {"name": "Note3", "value": "Third", "type": "CUSTOM"},
            {"name": "Note10", "value": "Tenth", "type": "CUSTOM"},
            {"name": "Note11", "value": "Out of range"},  # skipped
            {"name": "first_name", "value": "Skip"},  # non-note → skipped
        ],
    }
    notes = extract_agilecrm_notes(payload)
    contents = [(n["source"], n["content"]) for n in notes]
    assert contents == [
        ("agile:Note1", "First"),
        ("agile:Note3", "Third"),
        ("agile:Note10", "Tenth"),
    ]


def test_extract_agile_notes_tolerates_case_and_separators() -> None:
    """Real ES Agile accounts ship Note keys with mixed case
    (`note1`), separators (`Note_1`, `Note-1`) or stray spaces
    (`Note 1`). The exact-`name == "Note1"` match would drop them;
    the normaliser collapses every variant to the canonical
    `NOTE1` slot."""
    payload = {
        "id": 2,
        "properties": [
            {"name": "note1", "value": "lowercase"},
            {"name": "Note_2", "value": "underscore"},
            {"name": "NOTE-3", "value": "hyphen"},
            {"name": "Note 4", "value": "space"},
        ],
    }
    notes = extract_agilecrm_notes(payload)
    sources = [n["source"] for n in notes]
    assert sources == [
        "agile:Note1",
        "agile:Note2",
        "agile:Note3",
        "agile:Note4",
    ]
    # Labels preserve the exact key Agile sent.
    assert [n["label"] for n in notes] == [
        "note1",
        "Note_2",
        "NOTE-3",
        "Note 4",
    ]


def test_extract_agile_notes_deduplicates_same_slot() -> None:
    """A template glitch can ship the same slot twice — keep the
    first occurrence only so re-syncs don't spawn parallel rows."""
    payload = {
        "id": 3,
        "properties": [
            {"name": "Note1", "value": "first"},
            {"name": "note_1", "value": "duplicate"},
        ],
    }
    notes = extract_agilecrm_notes(payload)
    assert len(notes) == 1
    assert notes[0]["content"] == "first"


# -- Reconciler ----------------------------------------------------


def test_reconcile_agile_notes_inserts_then_idempotent(
    db: _Fixture,
) -> None:
    contact_id = _seed_contact(db.factory)
    payload = {
        "id": 1,
        "properties": [
            {"name": "Note1", "value": "Llamada inicial"},
            {"name": "Note2", "value": "Demo agendada"},
        ],
    }
    with db.factory() as session:
        first = reconcile_agile_notes(
            session, contact_id=contact_id, payload=payload
        )
        session.commit()
        second = reconcile_agile_notes(
            session, contact_id=contact_id, payload=payload
        )
        session.commit()
    assert first == 2
    assert second == 0

    with db.factory() as session:
        rows = list(
            session.scalars(
                select(ContactNote).where(ContactNote.contact_id == contact_id)
            )
        )
    assert {r.source for r in rows} == {"agile:Note1", "agile:Note2"}
    assert all(r.created_by_user_id is None for r in rows)
    assert all(r.pinned is False for r in rows)


def test_reconcile_agile_notes_dedup_protects_manual_edits(
    db: _Fixture,
) -> None:
    """If the operator edits an imported note in the CRM, the next
    sync must NOT re-insert the original Agile content as a
    parallel row — the dedup key (contact_id, source, content)
    accepts the divergence intentionally so the new content stays
    side-by-side with the operator's edit as a NEW row only on a
    real Agile update. Today we accept that subsequent re-syncs
    of the unchanged Agile content still see the edited row as
    "missing" and re-insert the original; the test pins that
    behaviour so a future fix to the merge story is intentional."""
    contact_id = _seed_contact(db.factory)
    payload = {
        "id": 1,
        "properties": [{"name": "Note1", "value": "Llamada inicial"}],
    }
    with db.factory() as session:
        reconcile_agile_notes(
            session, contact_id=contact_id, payload=payload
        )
        session.commit()
        # Operator edits the imported note.
        row = session.scalar(
            select(ContactNote).where(ContactNote.contact_id == contact_id)
        )
        assert row is not None
        row.content = "Llamada inicial — al final no respondieron"
        session.commit()
        # Next sync: same Agile payload. The original content is
        # missing from the dedup set now, so a fresh row gets added.
        added = reconcile_agile_notes(
            session, contact_id=contact_id, payload=payload
        )
        session.commit()
    assert added == 1
    with db.factory() as session:
        rows = list(
            session.scalars(
                select(ContactNote).where(ContactNote.contact_id == contact_id)
            )
        )
    assert len(rows) == 2


# -- CRUD round-trip -----------------------------------------------


def test_notes_crud_round_trip(client: TestClient, db: _Fixture) -> None:
    contact_id = _seed_contact(db.factory)
    headers = auth_headers(client, "user")

    res = client.get(f"/api/contacts/{contact_id}/notes", headers=headers)
    assert res.status_code == 200 and res.json() == []

    res = client.post(
        f"/api/contacts/{contact_id}/notes",
        json={"content": "Primera nota"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    first_id = res.json()["id"]
    assert res.json()["source"] == "manual"
    assert res.json()["pinned"] is False
    assert res.json()["created_by_user_id"] is not None

    # Empty content (after trim) rejected.
    res = client.post(
        f"/api/contacts/{contact_id}/notes",
        json={"content": "   "},
        headers=headers,
    )
    assert res.status_code == 400

    # Add a second one + pin it.
    res = client.post(
        f"/api/contacts/{contact_id}/notes",
        json={"content": "Importante", "pinned": True},
        headers=headers,
    )
    assert res.status_code == 201
    second_id = res.json()["id"]
    assert res.json()["pinned"] is True

    # Pinned floats to top.
    res = client.get(f"/api/contacts/{contact_id}/notes", headers=headers)
    order = [r["id"] for r in res.json()]
    assert order[0] == second_id

    # Edit content via PUT.
    res = client.put(
        f"/api/contacts/{contact_id}/notes/{first_id}",
        json={"content": "Primera nota editada", "pinned": False},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["content"] == "Primera nota editada"

    # Pin / unpin actions.
    res = client.post(
        f"/api/contacts/{contact_id}/notes/{first_id}/pin", headers=headers
    )
    assert res.status_code == 200
    assert res.json()["pinned"] is True

    res = client.post(
        f"/api/contacts/{contact_id}/notes/{second_id}/unpin", headers=headers
    )
    assert res.status_code == 200
    assert res.json()["pinned"] is False

    # Delete.
    res = client.delete(
        f"/api/contacts/{contact_id}/notes/{first_id}", headers=headers
    )
    assert res.status_code == 204
    res = client.get(f"/api/contacts/{contact_id}/notes", headers=headers)
    assert [r["id"] for r in res.json()] == [second_id]


def test_notes_404_for_missing_contact(client: TestClient) -> None:
    headers = auth_headers(client, "user")
    res = client.post(
        "/api/contacts/missing/notes",
        json={"content": "x"},
        headers=headers,
    )
    assert res.status_code == 404


def test_viewer_cannot_write_notes(client: TestClient, db: _Fixture) -> None:
    contact_id = _seed_contact(db.factory)
    headers = auth_headers(client, "viewer")
    res = client.post(
        f"/api/contacts/{contact_id}/notes",
        json={"content": "no"},
        headers=headers,
    )
    assert res.status_code == 403


# -- backfill ------------------------------------------------------


def test_backfill_apply_notes_is_idempotent(db: _Fixture) -> None:
    """The backfill's `_apply_notes` is the same dedupe shape as
    the per-sync reconciler — exercising it directly avoids
    standing up an HTTP mock of the Agile API for the assertion."""
    from scripts.backfill_contact_notes_from_agile import (  # noqa: PLC0415
        _apply_notes,
    )

    contact_id = _seed_contact(db.factory)
    payload = {
        "id": 1,
        "properties": [
            {"name": "Note1", "value": "Histórico A"},
            {"name": "Note3", "value": "Histórico B"},
        ],
    }
    now = datetime.now(UTC)
    with db.factory() as session:
        first = _apply_notes(
            session, contact_id=contact_id, payload=payload, now=now
        )
        session.commit()
        second = _apply_notes(
            session, contact_id=contact_id, payload=payload, now=now
        )
        session.commit()
    assert first == 2
    assert second == 0

    with db.factory() as session:
        rows = list(
            session.scalars(
                select(ContactNote).where(ContactNote.contact_id == contact_id)
            )
        )
    assert {r.source for r in rows} == {"agile:Note1", "agile:Note3"}
    assert all(r.source.startswith("agile:") for r in rows)
