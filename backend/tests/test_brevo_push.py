"""Sprint-Push-CRM-Brevo — tests del reverso del sync (CRM → Brevo).

Cubre:

- `push_contact_to_brevo`: happy path (crear), skip por owner=None,
  skip por mapping=None, contacto existente → solo add_to_list (no
  recrear), move entre listas, contacto sin email.
- `remove_contact_from_brevo`: desuscribir de listas mapeadas, NO
  borra el contacto en Brevo.
- `periodic_push_check`: encola contactos con owner+sin brevo_id.
- Endpoint `POST /api/brevo/admin/backfill-push`: encola todo.
- Endpoint `GET/PUT /api/brevo/admin/user-list-mappings`.
- Listener after_commit en `recompute_primary_cache`: cambio de owner
  vía `add_assignment(is_primary=True)` dispara enqueue post-commit.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.db.session import get_session
from app.integrations.brevo import push_jobs
from app.integrations.errors import IntegrationClientError
from app.main import app
from app.models.brevo import BrevoUserListMapping
from app.models.crm import (
    Base,
    Contact,
    ContactAssignment,
    ExternalSystem,
    User,
)
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
)
from app.repositories import assignments as _assignments
from app.services import brevo_push as _service
from tests._test_helpers import auth_headers, seed_test_users

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
                api_key_encrypted=crypto.encrypt("dummy-key"),
            )
        )
        seed.commit()
    _service.install_listeners()
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


def _user_ids(session) -> dict[str, str]:
    return {
        u.role.value: u.id
        for u in session.scalars(select(User))
    }


def _seed_mapping(
    session, *, user_id: str, list_id: int, list_name: str
) -> BrevoUserListMapping:
    return _service.upsert_mapping(
        session,
        user_id=user_id,
        brevo_list_id=list_id,
        brevo_list_name=list_name,
    )


def _seed_contact(
    session,
    *,
    email: str = "marny@example.com",
    owner_user_id: str | None = None,
    first_name: str = "Marny",
    brevo_contact_id: str | None = None,
) -> Contact:
    contact = Contact(
        id=str(uuid4()),
        first_name=first_name,
        email=email,
        owner_user_id=owner_user_id,
        brevo_contact_id=brevo_contact_id,
    )
    session.add(contact)
    session.flush()
    return contact


# ---------------------------------------------------------------------------
# Fake BrevoClient
# ---------------------------------------------------------------------------


class _FakeBrevo:
    """In-memory Brevo: contactos por email, cada uno con listIds. Las
    operaciones mutan el estado para que el siguiente get_contact vea
    el resultado. Permite testar el secuenciado real (remove then add)."""

    def __init__(self) -> None:
        self.contacts: dict[str, dict[str, Any]] = {}
        # Audit de llamadas para asserts:
        self.calls: list[tuple[str, tuple]] = []
        self._next_id = 1000

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    def seed_remote(self, email: str, *, list_ids: list[int]) -> int:
        bid = self._next_id
        self._next_id += 1
        self.contacts[email] = {"id": bid, "email": email, "listIds": list(list_ids)}
        return bid

    # The factory the test will patch BrevoClient with:
    def make_factory(self):
        outer = self

        class _Ctx:
            def __init__(self, session, account_id, **kwargs):
                _ = session, account_id, kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get_contact(self, email: str) -> dict[str, Any]:
                outer._record("get_contact", email)
                if email not in outer.contacts:
                    raise IntegrationClientError(
                        "not found", status_code=404
                    )
                return dict(outer.contacts[email])

            async def create_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
                outer._record("create_contact", payload)
                email = payload["email"]
                list_ids = list(payload.get("listIds") or [])
                bid = outer.seed_remote(email, list_ids=list_ids)
                return {"id": bid, "email": email}

            async def add_contacts_to_list(self, list_id: int, emails: list[str]):
                outer._record("add_contacts_to_list", list_id, emails)
                for e in emails:
                    if e not in outer.contacts:
                        outer.seed_remote(e, list_ids=[])
                    rec = outer.contacts[e]
                    if list_id not in rec["listIds"]:
                        rec["listIds"].append(list_id)
                return {}

            async def remove_contacts_from_list(
                self, list_id: int, emails: list[str]
            ):
                outer._record("remove_contacts_from_list", list_id, emails)
                for e in emails:
                    rec = outer.contacts.get(e)
                    if rec and list_id in rec["listIds"]:
                        rec["listIds"].remove(list_id)
                return {}

            async def list_contacts(
                self, *, limit: int = 50, offset: int = 0,
                modified_since: str | None = None,
            ):
                _ = modified_since
                outer._record("list_contacts", limit, offset)
                items = sorted(outer.contacts.values(), key=lambda r: r["email"])
                page = items[offset:offset + limit]
                return {"contacts": page, "count": len(items)}

        return _Ctx


@pytest.fixture()
def fake_brevo() -> _FakeBrevo:
    return _FakeBrevo()


@pytest.fixture()
def patched_push(factory: sessionmaker, fake_brevo: _FakeBrevo):
    """Patches push_contact_to_brevo's dependencies:
    - BrevoClient → fake (intercepts HTTP)
    - get_engine → test engine (so the RQ entrypoint reuses our SQLite)
    - _enqueue → records calls instead of hitting Redis
    """
    enqueued: list[tuple[str, tuple]] = []

    def fake_enqueue(callable_, *args):
        enqueued.append((callable_.__name__, args))

    engine = factory.kw["bind"]
    with (
        patch.object(push_jobs, "BrevoClient", fake_brevo.make_factory()),
        patch("app.db.session.get_engine", return_value=engine),
        patch.object(push_jobs, "_enqueue", side_effect=fake_enqueue),
    ):
        yield enqueued


# ---------------------------------------------------------------------------
# push_contact_to_brevo
# ---------------------------------------------------------------------------


def test_push_contact_with_owner_creates_in_brevo_in_correct_list(
    factory, fake_brevo, patched_push
):
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=42, list_name="Admin"
        )
        contact = _seed_contact(
            session, owner_user_id=users["admin"], email="marny@example.com"
        )
        session.commit()
        contact_id = contact.id

    push_jobs.push_contact_to_brevo(contact_id)

    # Brevo state: contact created in list 42.
    assert "marny@example.com" in fake_brevo.contacts
    assert fake_brevo.contacts["marny@example.com"]["listIds"] == [42]
    create_calls = [c for c in fake_brevo.calls if c[0] == "create_contact"]
    assert len(create_calls) == 1

    # Contact row marcado:
    with factory() as session:
        c = session.get(Contact, contact_id)
        assert c.brevo_contact_id and c.brevo_contact_id != "synced"
        assert c.brevo_last_synced_at is not None


def test_push_contact_without_owner_skipped(factory, fake_brevo, patched_push):
    with factory() as session:
        contact = _seed_contact(session, owner_user_id=None)
        session.commit()
        cid = contact.id

    push_jobs.push_contact_to_brevo(cid)

    assert fake_brevo.calls == []
    with factory() as session:
        c = session.get(Contact, cid)
        assert c.brevo_contact_id is None


def test_push_contact_without_mapping_skipped(factory, fake_brevo, patched_push):
    with factory() as session:
        users = _user_ids(session)
        # owner sin mapping
        contact = _seed_contact(session, owner_user_id=users["user"])
        session.commit()
        cid = contact.id

    push_jobs.push_contact_to_brevo(cid)

    assert fake_brevo.calls == []


def test_push_contact_without_email_skipped(factory, fake_brevo, patched_push):
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=10, list_name="X"
        )
        contact = Contact(
            id=str(uuid4()),
            first_name="NoEmail",
            email=None,
            owner_user_id=users["admin"],
        )
        session.add(contact)
        session.commit()
        cid = contact.id

    push_jobs.push_contact_to_brevo(cid)
    assert fake_brevo.calls == []


def test_existing_brevo_contact_just_added_to_list_not_recreated(
    factory, fake_brevo, patched_push
):
    fake_brevo.seed_remote("marny@example.com", list_ids=[7])  # ya existe
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=42, list_name="Admin"
        )
        contact = _seed_contact(
            session, owner_user_id=users["admin"], email="marny@example.com"
        )
        session.commit()
        cid = contact.id

    push_jobs.push_contact_to_brevo(cid)

    # No create_contact call.
    assert not any(c[0] == "create_contact" for c in fake_brevo.calls)
    # Added to 42.
    add_calls = [c for c in fake_brevo.calls if c[0] == "add_contacts_to_list"]
    assert (42, ["marny@example.com"]) in [c[1] for c in add_calls]
    # 7 NO se toca (no es lista mapeada).
    assert fake_brevo.contacts["marny@example.com"]["listIds"] == [7, 42]


def test_owner_change_moves_contact_between_lists(
    factory, fake_brevo, patched_push
):
    fake_brevo.seed_remote("marny@example.com", list_ids=[100])
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=100, list_name="Admin"
        )
        _seed_mapping(
            session, user_id=users["manager"], list_id=200, list_name="Manager"
        )
        # owner ahora es manager (lista 200)
        contact = _seed_contact(
            session,
            owner_user_id=users["manager"],
            email="marny@example.com",
        )
        session.commit()
        cid = contact.id

    push_jobs.push_contact_to_brevo(cid)

    # Quitado de 100 (lista mapeada del owner viejo) y añadido a 200.
    assert fake_brevo.contacts["marny@example.com"]["listIds"] == [200]
    assert ("remove_contacts_from_list", (100, ["marny@example.com"])) in [
        (c[0], c[1]) for c in fake_brevo.calls
    ]
    assert ("add_contacts_to_list", (200, ["marny@example.com"])) in [
        (c[0], c[1]) for c in fake_brevo.calls
    ]


def test_push_preserves_unmapped_lists(factory, fake_brevo, patched_push):
    """Lista 999 NO está en ningún mapping → no se toca."""
    fake_brevo.seed_remote("marny@example.com", list_ids=[100, 999])
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=100, list_name="Admin"
        )
        _seed_mapping(
            session, user_id=users["manager"], list_id=200, list_name="Manager"
        )
        contact = _seed_contact(
            session,
            owner_user_id=users["manager"],
            email="marny@example.com",
        )
        session.commit()
        cid = contact.id

    push_jobs.push_contact_to_brevo(cid)

    # 999 SIGUE: no se toca porque no es lista mapeada.
    assert 999 in fake_brevo.contacts["marny@example.com"]["listIds"]
    assert 200 in fake_brevo.contacts["marny@example.com"]["listIds"]
    assert 100 not in fake_brevo.contacts["marny@example.com"]["listIds"]


# ---------------------------------------------------------------------------
# remove_contact_from_brevo
# ---------------------------------------------------------------------------


def test_owner_removed_removes_from_brevo_lists_not_contact(
    factory, fake_brevo, patched_push
):
    fake_brevo.seed_remote("marny@example.com", list_ids=[100, 999])
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=100, list_name="Admin"
        )
        contact = _seed_contact(
            session, owner_user_id=users["admin"], email="marny@example.com"
        )
        session.commit()
        cid = contact.id

    push_jobs.remove_contact_from_brevo(cid, reason="owner_removed")

    # Quitado de 100, sigue en 999, contacto NO borrado.
    assert "marny@example.com" in fake_brevo.contacts
    assert fake_brevo.contacts["marny@example.com"]["listIds"] == [999]


# ---------------------------------------------------------------------------
# Periodic push runner
# ---------------------------------------------------------------------------


def test_periodic_push_runner_queues_unsynced_contacts(factory, patched_push):
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=1, list_name="A"
        )
        # 2 sin pushear + 1 ya pusheado + 1 sin owner
        _seed_contact(session, owner_user_id=users["admin"], email="a@x.com")
        _seed_contact(session, owner_user_id=users["admin"], email="b@x.com")
        _seed_contact(
            session,
            owner_user_id=users["admin"],
            email="c@x.com",
            brevo_contact_id="12345",
        )
        _seed_contact(session, owner_user_id=None, email="d@x.com")
        session.commit()

    enqueued = patched_push
    enqueued.clear()
    with factory() as session:
        from app.models.crm import SyncLog
        outcome = push_jobs.periodic_push_check(
            session, SyncLog(system="brevo", operation="periodic_push")
        )

    # Solo a@ y b@.
    names = [n for n, _ in enqueued]
    assert names.count("push_contact_to_brevo") == 2
    assert outcome.records_processed == 2


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


def test_get_user_list_mappings_lists_all_active_non_viewer_users(client, factory):
    response = client.get(
        "/api/brevo/admin/user-list-mappings",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    roles = {r["user_email"] for r in rows}
    # admin/manager/user pero NO viewer
    assert "admin@example.com" in roles
    assert "manager@example.com" in roles
    assert "user@example.com" in roles
    assert "viewer@example.com" not in roles
    # Todos sin mapeo todavía
    assert all(r["brevo_list_id"] is None for r in rows)


def test_put_user_list_mappings_persists_and_deletes(client, factory):
    with factory() as session:
        users = _user_ids(session)
        admin_id = users["admin"]
        manager_id = users["manager"]
        _seed_mapping(
            session, user_id=manager_id, list_id=99, list_name="Old"
        )
        session.commit()

    response = client.put(
        "/api/brevo/admin/user-list-mappings",
        headers=auth_headers(client, "admin"),
        json={
            "mappings": [
                {
                    "user_id": admin_id,
                    "brevo_list_id": 42,
                    "brevo_list_name": "Admin",
                },
                {
                    "user_id": manager_id,
                    "brevo_list_id": None,
                    "brevo_list_name": None,
                },
            ]
        },
    )
    assert response.status_code == 200, response.text

    with factory() as session:
        assert _service.get_mapping(session, admin_id).brevo_list_id == 42
        assert _service.get_mapping(session, manager_id) is None


def test_backfill_endpoint_queues_brand_new_contacts(
    client, factory, fake_brevo, patched_push
):
    """Sin emails en Brevo → todos los pendientes son brand-new →
    `push_contact_to_brevo` (no `add_to_owner_list`)."""
    enqueued = patched_push
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=1, list_name="A"
        )
        for i in range(3):
            _seed_contact(
                session, owner_user_id=users["admin"], email=f"b{i}@x.com"
            )
        _seed_contact(
            session,
            owner_user_id=users["admin"],
            email="done@x.com",
            brevo_contact_id="999",
        )
        session.commit()

    enqueued.clear()
    response = client.post(
        "/api/brevo/admin/backfill-push",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_with_owner"] == 3
    assert body["queued_for_creation"] == 3
    assert body["already_in_brevo_marked"] == 0
    assert body["queued_for_list_add_only"] == 0
    assert sum(1 for n, _ in enqueued if n == "push_contact_to_brevo") == 3
    assert not any(n == "add_contact_to_owner_list" for n, _ in enqueued)


# ---------------------------------------------------------------------------
# PR-Fix-Backfill-Brevo-Optimizado — pre-filtering tests
# ---------------------------------------------------------------------------


def test_backfill_pre_filters_emails_already_in_brevo(
    client, factory, fake_brevo, patched_push
):
    """2 contactos cuyo email ya está en Brevo + 1 brand-new → buckets
    correctos en el reporte."""
    fake_brevo.seed_remote("alice@x.com", list_ids=[])
    fake_brevo.seed_remote("bob@x.com", list_ids=[55])
    enqueued = patched_push
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=1, list_name="A"
        )
        _seed_contact(session, owner_user_id=users["admin"], email="alice@x.com")
        _seed_contact(session, owner_user_id=users["admin"], email="bob@x.com")
        _seed_contact(session, owner_user_id=users["admin"], email="new@x.com")
        session.commit()

    enqueued.clear()
    response = client.post(
        "/api/brevo/admin/backfill-push",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_with_owner"] == 3
    assert body["already_in_brevo_marked"] == 2
    assert body["queued_for_creation"] == 1
    assert body["queued_for_list_add_only"] == 2
    assert body["brevo_inventory_size"] == 2


def test_backfill_marks_pre_existing_as_synced_without_api_call(
    client, factory, fake_brevo, patched_push
):
    """Los pre-existing reciben `brevo_contact_id = "pre-existing"` en la
    misma transacción del endpoint. NO se llama a `get_contact` ni
    `create_contact` para ellos durante el endpoint (la cola se procesa
    aparte)."""
    fake_brevo.seed_remote("alice@x.com", list_ids=[])
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=1, list_name="A"
        )
        c = _seed_contact(
            session, owner_user_id=users["admin"], email="alice@x.com"
        )
        session.commit()
        cid = c.id

    fake_brevo.calls.clear()
    response = client.post(
        "/api/brevo/admin/backfill-push",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200, response.text

    # Durante el endpoint solo hay list_contacts (bulk fetch). No
    # get_contact ni create_contact.
    assert any(c[0] == "list_contacts" for c in fake_brevo.calls)
    assert not any(c[0] == "get_contact" for c in fake_brevo.calls)
    assert not any(c[0] == "create_contact" for c in fake_brevo.calls)

    with factory() as session:
        assert session.get(Contact, cid).brevo_contact_id == "pre-existing"


def test_backfill_queues_only_new_contacts_for_creation(
    client, factory, fake_brevo, patched_push
):
    """Los pre-existing van a `add_to_owner_list`, los brand-new van a
    `push_contact_to_brevo`. Sin solapamiento."""
    fake_brevo.seed_remote("known@x.com", list_ids=[])
    enqueued = patched_push
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=1, list_name="A"
        )
        _seed_contact(session, owner_user_id=users["admin"], email="known@x.com")
        _seed_contact(session, owner_user_id=users["admin"], email="fresh@x.com")
        session.commit()

    enqueued.clear()
    client.post(
        "/api/brevo/admin/backfill-push",
        headers=auth_headers(client, "admin"),
    )
    pushes = [n for n, _ in enqueued if n == "push_contact_to_brevo"]
    adds = [n for n, _ in enqueued if n == "add_contact_to_owner_list"]
    assert len(pushes) == 1  # fresh@
    assert len(adds) == 1  # known@


def test_backfill_handles_email_case_insensitive(
    client, factory, fake_brevo, patched_push
):
    """Brevo devuelve `Alice@X.COM`, CRM tiene `alice@x.com` — el
    pre-filtro debe matchearlos."""
    fake_brevo.seed_remote("Alice@X.COM", list_ids=[])
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=1, list_name="A"
        )
        _seed_contact(session, owner_user_id=users["admin"], email="alice@x.com")
        session.commit()

    response = client.post(
        "/api/brevo/admin/backfill-push",
        headers=auth_headers(client, "admin"),
    )
    body = response.json()
    assert body["already_in_brevo_marked"] == 1
    assert body["queued_for_creation"] == 0


def test_backfill_aborts_on_brevo_api_failure_during_bulk_fetch(
    client, factory, patched_push
):
    """Si `fetch_brevo_emails` raisea IntegrationError, devolvemos
    502 y NO marcamos ni encolamos nada."""
    from app.integrations.errors import IntegrationServerError

    def boom(session, account_id, *, refresh=False):
        raise IntegrationServerError(
            "500 Internal", system="brevo", account_id="main", status_code=500
        )

    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=1, list_name="A"
        )
        c = _seed_contact(
            session, owner_user_id=users["admin"], email="x@x.com"
        )
        cid = c.id
        session.commit()

    with patch.object(push_jobs, "fetch_brevo_emails", side_effect=boom):
        response = client.post(
            "/api/brevo/admin/backfill-push",
            headers=auth_headers(client, "admin"),
        )
    assert response.status_code == 502
    assert "Brevo" in response.json()["detail"]

    with factory() as session:
        # Contacto SIGUE sin marcar
        assert session.get(Contact, cid).brevo_contact_id is None


def test_backfill_dry_run_reports_without_marking_or_enqueuing(
    client, factory, fake_brevo, patched_push
):
    """`?dry_run=true` solo cuenta; no toca DB ni cola."""
    fake_brevo.seed_remote("known@x.com", list_ids=[])
    enqueued = patched_push
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=1, list_name="A"
        )
        c = _seed_contact(
            session, owner_user_id=users["admin"], email="known@x.com"
        )
        cid = c.id
        _seed_contact(session, owner_user_id=users["admin"], email="new@x.com")
        session.commit()

    enqueued.clear()
    response = client.post(
        "/api/brevo/admin/backfill-push?dry_run=true",
        headers=auth_headers(client, "admin"),
    )
    body = response.json()
    assert body["dry_run"] is True
    assert body["total_with_owner"] == 2
    assert body["queued_for_creation"] == 1
    assert body["queued_for_list_add_only"] == 1
    # En dry_run, already_in_brevo_marked refleja "lo que se marcaría"
    # = 0 porque no se marcó. queued_for_list_add_only = 1 = preview.
    assert body["already_in_brevo_marked"] == 0
    # Nada encolado:
    assert enqueued == []
    # DB intacta:
    with factory() as session:
        assert session.get(Contact, cid).brevo_contact_id is None


def test_backfill_uses_redis_cache_within_1h(
    client, factory, fake_brevo, patched_push
):
    """Segunda llamada al endpoint NO repite el bulk fetch si el set
    ya está cacheado. Mockeamos la cache de Redis a través del helper
    `_load_emails_from_cache`."""
    fake_brevo.seed_remote("a@x.com", list_ids=[])
    enqueued = patched_push
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=1, list_name="A"
        )
        _seed_contact(session, owner_user_id=users["admin"], email="a@x.com")
        session.commit()

    enqueued.clear()
    # 1ª llamada: cache miss → bulk fetch.
    with patch.object(
        push_jobs, "_load_emails_from_cache", return_value=None
    ) as miss, patch.object(push_jobs, "_store_emails_in_cache"):
        r1 = client.post(
            "/api/brevo/admin/backfill-push?dry_run=true",
            headers=auth_headers(client, "admin"),
        )
        assert r1.status_code == 200
        assert r1.json()["cached_inventory"] is False
        assert miss.called

    # 2ª llamada: cache hit (mock devuelve el set).
    with patch.object(
        push_jobs, "_load_emails_from_cache", return_value={"a@x.com"}
    ):
        r2 = client.post(
            "/api/brevo/admin/backfill-push?dry_run=true",
            headers=auth_headers(client, "admin"),
        )
        assert r2.status_code == 200
        assert r2.json()["cached_inventory"] is True


def test_backfill_refresh_param_bypasses_cache(
    client, factory, fake_brevo, patched_push
):
    """`?refresh=true` ignora la cache aunque exista."""
    fake_brevo.seed_remote("a@x.com", list_ids=[])
    with factory() as session:
        users = _user_ids(session)
        _seed_mapping(
            session, user_id=users["admin"], list_id=1, list_name="A"
        )
        _seed_contact(session, owner_user_id=users["admin"], email="a@x.com")
        session.commit()

    with patch.object(
        push_jobs, "_load_emails_from_cache", return_value={"stale@x.com"}
    ) as cache_read, patch.object(push_jobs, "_store_emails_in_cache"):
        r = client.post(
            "/api/brevo/admin/backfill-push?dry_run=true&refresh=true",
            headers=auth_headers(client, "admin"),
        )
        # cache no se leyó (refresh=True salta la lectura)
        assert not cache_read.called
        body = r.json()
        # Inventory size del FETCH FRESH (1 = a@x.com en fake), NO
        # del valor stale (que sería 1 también pero por otro email
        # que no matchea el CRM).
        assert body["brevo_inventory_size"] == 1
        # `a@x.com` está pendiente y matchea con el fetch fresco → 1
        # marked, 0 brand-new.
        assert body["already_in_brevo_marked"] == 0  # dry_run
        assert body["queued_for_list_add_only"] == 1
        assert body["queued_for_creation"] == 0


# ---------------------------------------------------------------------------
# Chokepoint: recompute_primary_cache triggers after_commit listener
# ---------------------------------------------------------------------------


def test_after_commit_listener_enqueues_on_owner_change(factory, patched_push):
    """Cambio de owner vía add_assignment(is_primary=True) → tras
    commit() el listener encola brevo:push_contact."""
    enqueued = patched_push
    with factory() as session:
        users = _user_ids(session)
        admin_id = users["admin"]
        _seed_mapping(
            session, user_id=admin_id, list_id=42, list_name="Admin"
        )
        contact = _seed_contact(session, email="new@x.com")
        contact_id = contact.id
        session.commit()
        # owner cambia: None → admin
        enqueued.clear()
        _assignments.add_assignment(
            session,
            contact_id=contact_id,
            user_id=admin_id,
            is_primary=True,
            source="manual",
        )
        session.commit()

    # Después del commit, el listener debe haber encolado push.
    push_calls = [n for n, _ in enqueued if n == "push_contact_to_brevo"]
    assert len(push_calls) == 1


def test_after_rollback_drops_pending_enqueue(factory, patched_push):
    """Si la transacción se rollbackea, NO se encola — el cambio nunca
    llegó a disco."""
    enqueued = patched_push
    with factory() as session:
        users = _user_ids(session)
        admin_id = users["admin"]
        _seed_mapping(
            session, user_id=admin_id, list_id=42, list_name="Admin"
        )
        contact = _seed_contact(session, email="new@x.com")
        contact_id = contact.id
        session.commit()
        enqueued.clear()
        _assignments.add_assignment(
            session,
            contact_id=contact_id,
            user_id=admin_id,
            is_primary=True,
            source="manual",
        )
        session.rollback()
        # post-rollback: ningún enqueue
        # (un commit() ahora sin mutaciones no debe encolar tampoco)
        session.commit()

    assert not any(n == "push_contact_to_brevo" for n, _ in enqueued)


def test_owner_removed_via_primary_demote_enqueues_remove(factory, patched_push):
    """Quitar primary (owner_user_id pasa a NULL) → remove_from_brevo."""
    enqueued = patched_push
    with factory() as session:
        users = _user_ids(session)
        admin_id = users["admin"]
        _seed_mapping(
            session, user_id=admin_id, list_id=42, list_name="Admin"
        )
        contact = _seed_contact(
            session, email="x@x.com", owner_user_id=admin_id
        )
        contact_id = contact.id
        # Persistir el assignment row para que demote tenga algo que
        # tumbar
        assignment = ContactAssignment(
            contact_id=contact_id,
            user_id=admin_id,
            is_primary=True,
            source="manual",
        )
        from datetime import UTC
        from datetime import datetime as _dt
        assignment.assigned_at = _dt.now(UTC)
        assignment.created_at = assignment.updated_at = _dt.now(UTC)
        session.add(assignment)
        session.commit()
        enqueued.clear()
        # Quitar primary
        _assignments.remove_assignment(session, assignment)
        session.commit()

    assert any(n == "remove_contact_from_brevo" for n, _ in enqueued)
