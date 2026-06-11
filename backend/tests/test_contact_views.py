"""CRUD + permissions + duplicate / default + view_id merging on the
saved contact-views endpoints."""
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as seed_session:
        seed_test_users(seed_session)

    def override_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def _create_view(client: TestClient, role: str = "manager", **overrides) -> dict:
    payload = {
        "name": "Vista test",
        "description": None,
        "is_shared": False,
        "is_default": False,
        "filters": {"q": "demo"},
        "columns": {"visible": ["name", "email"], "order": ["name", "email"], "widths": {}},
        "sort": {"sort_by": "updated_at", "sort_dir": "desc"},
    }
    payload.update(overrides)
    response = client.post(
        "/api/contact-views",
        json=payload,
        headers=auth_headers(client, role),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_view_returns_owner_and_decoded_filters(client: TestClient):
    view = _create_view(client, role="manager")
    assert view["is_owner"] is True
    assert view["filters"]["q"] == "demo"
    assert view["columns"]["visible"] == ["name", "email"]
    assert view["sort"]["sort_by"] == "updated_at"


def test_list_includes_own_and_shared_views(client: TestClient):
    """Owner sees every own view + any other user's shared view; the
    private view of another user must NOT leak into the list."""
    own = _create_view(client, role="manager", name="Mía")
    shared = _create_view(
        client, role="admin", name="Compartida", is_shared=True
    )
    _create_view(client, role="admin", name="Solo admin")

    response = client.get(
        "/api/contact-views", headers=auth_headers(client, "manager")
    )
    body = response.json()
    names = {v["name"]: v for v in body}
    assert "Mía" in names
    assert "Compartida" in names
    assert "Solo admin" not in names
    assert names["Mía"]["is_owner"] is True
    assert names["Compartida"]["is_owner"] is False
    # Sanity: keep referencing for debugging
    assert own["id"] in {v["id"] for v in body}
    assert shared["id"] in {v["id"] for v in body}


def test_patch_view_blocked_for_non_owner(client: TestClient):
    """Even shared views can only be edited by the owner — operators
    duplicate to mutate."""
    shared = _create_view(client, role="admin", is_shared=True)
    response = client.patch(
        f"/api/contact-views/{shared['id']}",
        json={"name": "Robo"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 403


def test_setting_default_demotes_previous_default(client: TestClient):
    a = _create_view(client, role="manager", name="A", is_default=True)
    b = _create_view(client, role="manager", name="B")
    # Promote B → A must drop its is_default.
    response = client.post(
        f"/api/contact-views/{b['id']}/set-default",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200
    listed = client.get(
        "/api/contact-views", headers=auth_headers(client, "manager")
    ).json()
    by_id = {v["id"]: v for v in listed}
    assert by_id[a["id"]]["is_default"] is False
    assert by_id[b["id"]]["is_default"] is True


def test_duplicate_creates_owned_copy(client: TestClient):
    """Any user who can read a view can duplicate. The duplicate is
    owned by the duplicator with sharing/default reset so a copy of
    someone else's default doesn't become my default."""
    shared = _create_view(
        client, role="admin", name="Compartida", is_shared=True, is_default=True
    )
    response = client.post(
        f"/api/contact-views/{shared['id']}/duplicate",
        json={"name": "Mi copia"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201
    duplicate = response.json()
    assert duplicate["name"] == "Mi copia"
    assert duplicate["is_owner"] is True
    assert duplicate["is_shared"] is False
    assert duplicate["is_default"] is False


def test_delete_view_blocked_for_non_owner(client: TestClient):
    shared = _create_view(client, role="admin", is_shared=True)
    response = client.delete(
        f"/api/contact-views/{shared['id']}",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 403


def test_view_id_applies_filters_to_contacts_list(client: TestClient):
    """A saved filter (e.g. q="ana") narrows the result set when the
    operator points the contacts list at the view via `?view_id=...`."""
    headers = auth_headers(client, "manager")
    for first_name, email in (
        ("Ana", "ana@example.com"),
        ("Boris", "boris@example.com"),
    ):
        client.post(
            "/api/contacts",
            json={
                "first_name": first_name,
                "email": email,
                "marketing_consent": "unknown",
            },
            headers=headers,
        )
    view = _create_view(client, filters={"q": "ana"})

    response = client.get(
        f"/api/contacts?view_id={view['id']}",
        headers=auth_headers(client, "manager"),
    )
    body = response.json()
    emails = sorted(item["email"] for item in body["items"])
    assert emails == ["ana@example.com"]


def test_view_id_filters_overridden_by_explicit_query_param(client: TestClient):
    """A URL param wins over a view's saved value. Operator typed
    `q=` (explicit reset) → view's q="ana" is dropped."""
    headers = auth_headers(client, "manager")
    for first_name, email in (
        ("Ana", "ana@example.com"),
        ("Boris", "boris@example.com"),
    ):
        client.post(
            "/api/contacts",
            json={
                "first_name": first_name,
                "email": email,
                "marketing_consent": "unknown",
            },
            headers=headers,
        )
    view = _create_view(client, filters={"q": "ana"})

    response = client.get(
        f"/api/contacts?view_id={view['id']}&q=boris",
        headers=auth_headers(client, "manager"),
    )
    body = response.json()
    emails = sorted(item["email"] for item in body["items"])
    assert emails == ["boris@example.com"]


def test_view_id_for_private_view_of_other_user_is_404(client: TestClient):
    """A private view belonging to another user must never leak. The
    UI route should 404 rather than 403 so listings don't enumerate
    private ids."""
    private = _create_view(client, role="admin", name="Privada")
    response = client.get(
        f"/api/contact-views/{private['id']}",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Save view as segment
# ---------------------------------------------------------------------------


def _rules_view(client: TestClient, name: str, rules: dict) -> dict:
    """Helper: create a view whose `filters.rules_json` carries the
    new segments-engine boolean tree (Sprint UX)."""
    return _create_view(
        client, role="manager", name=name, filters={"rules_json": rules}
    )


def test_save_view_as_segment_creates_segment_with_same_rules(
    client: TestClient,
):
    rules = {
        "type": "rule",
        "field": "email",
        "comparator": "contains",
        "value": "@example.com",
    }
    view = _rules_view(client, "Vista clientes", rules)

    response = client.post(
        f"/api/contact-views/{view['id']}/save-as-segment",
        json={"name": "Clientes", "description": "Clientes activos"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Clientes"
    # The segment's rules_json round-trips the view's filter tree.
    assert body["rules"]["field"] == "email"
    assert body["rules"]["value"] == "@example.com"


def test_save_view_as_segment_blocked_for_non_owner_private_view(
    client: TestClient,
):
    rules = {"type": "rule", "field": "email", "comparator": "is_not_null"}
    admin_view = _create_view(
        client,
        role="admin",
        name="Solo admin",
        filters={"rules_json": rules},
    )
    response = client.post(
        f"/api/contact-views/{admin_view['id']}/save-as-segment",
        json={"name": "Robo"},
        headers=auth_headers(client, "manager"),
    )
    # The view exists but isn't shared and the manager isn't the
    # owner → 403 from the endpoint's ownership check.
    assert response.status_code == 403


def test_save_view_as_segment_legacy_filter_dict_becomes_empty_rules(
    client: TestClient,
):
    """Old views stored `filters` as a flat dict (`{"q": "demo"}`) —
    the action turns the tree into an empty rules_json, matching the
    'every contact' default rather than crashing the engine."""
    legacy = _create_view(client, role="manager", filters={"q": "demo"})
    response = client.post(
        f"/api/contact-views/{legacy['id']}/save-as-segment",
        json={"name": "Vista vieja"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["rules"] == {}


# ---------------------------------------------------------------------------
# Push view to brevo list
# ---------------------------------------------------------------------------


def _seed_brevo_account(client: TestClient) -> None:
    """Seed a Brevo integration account so the push endpoint can find
    one. We don't actually hit Brevo — `BrevoClient.create_list` is
    patched per-test."""
    from app.db.session import get_session
    from app.main import app
    from app.models.crm import ExternalSystem
    from app.models.integration_settings import IntegrationAccount

    session_factory = app.dependency_overrides[get_session]
    session_gen = session_factory()
    session = next(session_gen)
    try:
        session.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="default",
                display_name="Brevo Default",
                enabled=True,
            )
        )
        session.commit()
    finally:
        session_gen.close()


def test_push_view_to_existing_brevo_list_creates_target_and_enqueues(
    client: TestClient, monkeypatch
):
    rules = {
        "type": "rule",
        "field": "email",
        "comparator": "is_not_null",
    }
    view = _rules_view(client, "Lista email", rules)
    _seed_brevo_account(client)

    fake = {"sync_log_id": "log-1", "job_id": "job-1"}

    def _fake_enqueue(*_args, **_kwargs):
        return fake["sync_log_id"], fake["job_id"]

    monkeypatch.setattr(
        "app.api.routes.enqueue_sync_job", _fake_enqueue
    )

    response = client.post(
        f"/api/contact-views/{view['id']}/push-to-brevo-list",
        json={"brevo_account_id": "default", "brevo_list_id": 42},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sync_log_id"] == "log-1"
    assert body["brevo_list_id"] == 42
    assert body["target_id"]
    assert body["segment_id"]


def test_push_view_to_brevo_list_rejects_both_or_neither(client: TestClient):
    view = _rules_view(
        client,
        "Vista",
        {"type": "rule", "field": "email", "comparator": "is_not_null"},
    )
    _seed_brevo_account(client)
    both = client.post(
        f"/api/contact-views/{view['id']}/push-to-brevo-list",
        json={
            "brevo_account_id": "default",
            "brevo_list_id": 42,
            "new_list_name": "Otra",
        },
        headers=auth_headers(client, "manager"),
    )
    assert both.status_code == 400

    neither = client.post(
        f"/api/contact-views/{view['id']}/push-to-brevo-list",
        json={"brevo_account_id": "default"},
        headers=auth_headers(client, "manager"),
    )
    assert neither.status_code == 400


def test_push_view_creates_new_brevo_list_when_requested(
    client: TestClient, monkeypatch
):
    rules = {"type": "rule", "field": "email", "comparator": "is_not_null"}
    view = _rules_view(client, "Vista", rules)
    _seed_brevo_account(client)

    created_id = 9999

    class _FakeClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def create_list(self, name):
            return {"id": created_id, "name": name}

    monkeypatch.setattr(
        "app.integrations.brevo.client.BrevoClient", _FakeClient
    )
    monkeypatch.setattr(
        "app.api.routes.enqueue_sync_job", lambda *_a, **_kw: ("L", "J")
    )

    response = client.post(
        f"/api/contact-views/{view['id']}/push-to-brevo-list",
        json={"brevo_account_id": "default", "new_list_name": "Nueva lista"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["brevo_list_id"] == created_id


def test_push_view_target_handler_runs_end_to_end(client: TestClient):
    """End-to-end sanity check: the BrevoSyncTarget the push endpoint
    creates is consumable by `push_brevo_target` without crashing on
    the auto-generated rules / segment / target shape.

    Regression for "no funciona": the endpoint test only asserted the
    target was created + the job enqueued (with `enqueue_sync_job`
    mocked). A real production-like run also needs the worker handler
    to load the target → segment → rules → contacts list without any
    integrity error.
    """
    from unittest.mock import MagicMock, patch

    from app.db.session import get_session
    from app.integrations.brevo.sync_targets import push_brevo_target
    from app.main import app
    from app.models.brevo import BrevoSyncTarget
    from app.models.crm import Contact, SyncLog

    fake_redis = MagicMock()
    fake_redis.set.return_value = True  # SETNX returns True → got the lock

    # Seed a contact that matches the view's rules so the target picker
    # has someone to push.
    session_factory = app.dependency_overrides[get_session]
    session_gen = session_factory()
    session = next(session_gen)
    try:
        session.add(
            Contact(first_name="Ana", email="ana@example.com")
        )
        session.commit()
    finally:
        session_gen.close()

    view = _rules_view(
        client,
        "End-to-end",
        {"type": "rule", "field": "email", "comparator": "is_not_null"},
    )
    _seed_brevo_account(client)

    # Track every BrevoClient call the worker fires so we can assert
    # the contact actually reached the (faked) Brevo wire.
    class _WorkerFakeClient:
        calls: list[tuple[str, object]] = []

        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def create_contact(self, payload):
            _WorkerFakeClient.calls.append(("create", payload["email"]))
            return {"id": 1}

        async def update_contact(self, identifier, payload):
            _WorkerFakeClient.calls.append(("update", identifier))

        async def add_contacts_to_list(self, list_id, emails):
            _WorkerFakeClient.calls.append(
                ("add_to_list", (list_id, tuple(emails)))
            )
            return {}

        async def remove_contacts_from_list(self, list_id, emails):
            _WorkerFakeClient.calls.append(
                ("remove_from_list", (list_id, tuple(emails)))
            )
            return {}

    # `redis_connection()` is consulted by the target lock; a
    # MagicMock that returns True on SETNX is enough — the rest of
    # the codepath only touches `set`/`delete` and we don't care about
    # the side effects there.
    with (
        patch(
            "app.integrations.brevo.sync_targets.BrevoClient",
            _WorkerFakeClient,
        ),
        patch(
            "app.integrations.brevo.sync_targets.redis_connection",
            return_value=fake_redis,
        ),
        patch(
            "app.api.routes.enqueue_sync_job",
            lambda *_a, **_kw: ("log-x", "job-x"),
        ),
    ):
        push_response = client.post(
            f"/api/contact-views/{view['id']}/push-to-brevo-list",
            json={"brevo_account_id": "default", "brevo_list_id": 42},
            headers=auth_headers(client, "manager"),
        )
        assert push_response.status_code == 200, push_response.text
        target_id = push_response.json()["target_id"]

        # Now drive the worker handler against the very row we just
        # created. The endpoint mocked `enqueue_sync_job` so we feed
        # the handler a synthetic SyncLog with the same payload shape
        # the real job would have.
        session_gen = session_factory()
        session = next(session_gen)
        try:
            target = session.get(BrevoSyncTarget, target_id)
            assert target is not None
            sync_log = SyncLog(
                system="brevo",
                account_id="default",
                operation="push_target",
                status="running",
                metadata_json=f'{{"payload": {{"target_id": "{target_id}"}}}}',
            )
            session.add(sync_log)
            session.flush()
            outcome = push_brevo_target(session, sync_log)
        finally:
            session_gen.close()

    # The handler returned without raising; the test ana@example.com
    # contact reached the BrevoClient and the list add fired.
    assert outcome.records_failed == 0, outcome.error_summary
    add_calls = [c for c in _WorkerFakeClient.calls if c[0] == "add_to_list"]
    assert add_calls, _WorkerFakeClient.calls
    list_id_used, emails_used = add_calls[0][1]
    # `BrevoSyncTarget.brevo_list_id` is `str` in the model but the
    # add-to-list Brevo client method gets an `int` after the cast
    # the worker applies on its way out. We just assert the wire value
    # round-trips to either form so the test isn't brittle to that
    # detail.
    assert int(list_id_used) == 42
    assert "ana@example.com" in emails_used
