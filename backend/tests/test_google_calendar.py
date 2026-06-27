"""Google Calendar integration — OAuth + service + sync hooks.

The whole `google_*` library stack is mocked: we never speak to
Google. The tests assert that the right call payload reaches the
mocked client, that tokens are persisted encrypted, that the sync
hooks are tolerant of disconnected users, and that the state-replay
guard works.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.crypto import decrypt
from app.db.session import get_session
from app.integrations.google_calendar import service as google_service
from app.integrations.google_calendar.oauth import OAuthExchangeResult
from app.main import app
from app.models.crm import (
    Base,
    Task,
    User,
    UserRole,
)
from tests._test_helpers import auth_headers, seed_test_users


def _enable_oauth_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire pretend OAuth credentials so the configured-guard
    treats the integration as ready. The cache_clear bookends are
    required because `get_settings` is `@lru_cache`'d."""
    from app.core import config as config_module  # noqa: PLC0415

    config_module.get_settings.cache_clear()
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://localhost:8000/api/integrations/google/callback",
    )
    config_module.get_settings.cache_clear()


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    _enable_oauth_settings(monkeypatch)

    # Fake Redis backing the OAuth state cache. setex + get + delete
    # is all the service exercises.
    state_store: dict[str, str] = {}

    class _FakeRedis:
        def setex(self, key: str, _ttl: int, value: str) -> None:
            state_store[key] = value

        def get(self, key: str) -> bytes | None:
            v = state_store.get(key)
            return v.encode() if v is not None else None

        def delete(self, *keys: str) -> None:
            for k in keys:
                state_store.pop(k, None)

    monkeypatch.setattr(
        "app.workers.queues.redis_connection", lambda url=None: _FakeRedis()
    )

    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _user_id(session: Session, role: UserRole) -> str:
    return session.scalar(select(User.id).where(User.role == role))


def _seed_integration(
    session_factory: sessionmaker,
    *,
    user_id: str,
    google_email: str = "bart@bomedia.net",
    calendar_id: str | None = "cal-123",
    calendar_summary: str | None = "CRMBO Tareas",
) -> str:
    """PR-OAuth-Google-Unificado. Crea la integración ORG singleton (si
    no existe) + el calendario per-user. Idempotente para que varios
    tests/users la llamen sin colisionar en el PK singleton. Devuelve el
    id de la integración org."""
    from app.core.crypto import encrypt  # noqa: PLC0415
    from app.models.crm import (  # noqa: PLC0415
        ORG_GOOGLE_SINGLETON_ID,
        OrgGoogleIntegration,
        UserCalendarPref,
    )

    with session_factory() as session:
        integration = session.get(OrgGoogleIntegration, ORG_GOOGLE_SINGLETON_ID)
        if integration is None:
            integration = OrgGoogleIntegration(
                id=ORG_GOOGLE_SINGLETON_ID,
                google_email=google_email,
                access_token_encrypted=encrypt("access-token-plain"),
                refresh_token_encrypted=encrypt("refresh-token-plain"),
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                scopes=(
                    "https://www.googleapis.com/auth/calendar.readonly "
                    "https://www.googleapis.com/auth/calendar.events"
                ),
                connected_at=datetime.now(UTC),
                connected_by_user_id=user_id,
                status="active",
            )
            session.add(integration)
        if calendar_id is not None:
            pref = session.get(UserCalendarPref, user_id)
            if pref is None:
                pref = UserCalendarPref(user_id=user_id)
                session.add(pref)
            pref.selected_calendar_id = calendar_id
            pref.selected_calendar_summary = calendar_summary
        session.commit()
        return integration.id


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


def test_status_when_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import config as config_module  # noqa: PLC0415

    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URI", raising=False)
    config_module.get_settings.cache_clear()

    response = client.get(
        "/api/integrations/google/status", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["connected"] is False


def test_status_when_connected_with_calendar(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_integration(session_factory, user_id=uid)

    response = client.get(
        "/api/integrations/google/status", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["google_email"] == "bart@bomedia.net"
    assert body["selected_calendar"]["id"] == "cal-123"
    assert body["requires_calendar_selection"] is False


def test_authorize_returns_consent_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PR-OAuth-Google-Unificado. /authorize es admin-only: solo el admin
    # conecta la cuenta Google org-wide.
    monkeypatch.setattr(
        "app.integrations.google_calendar.oauth.get_authorize_url",
        lambda state: f"https://accounts.google.com/o/oauth2/auth?state={state}",
    )
    monkeypatch.setattr(
        "app.api.google_integrations.get_authorize_url",
        lambda state: f"https://accounts.google.com/o/oauth2/auth?state={state}",
    )
    response = client.get(
        "/api/integrations/google/authorize",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("https://accounts.google.com/o/oauth2/auth?state=")


def test_authorize_forbidden_for_non_admin(client: TestClient) -> None:
    # PR-OAuth-Google-Unificado. Un comercial no puede iniciar la
    # conexión org-wide.
    response = client.get(
        "/api/integrations/google/authorize",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# OAuth callback
# ---------------------------------------------------------------------------


def test_callback_exchanges_code_and_persists_row(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PR-OAuth-Google-Unificado. Bind a state to the `admin` role first
    # by hitting /authorize (admin-only). The state ends up in fake Redis.
    monkeypatch.setattr(
        "app.api.google_integrations.get_authorize_url",
        lambda state: f"https://accounts.google.com/o/oauth2/auth?state={state}",
    )
    auth_resp = client.get(
        "/api/integrations/google/authorize",
        headers=auth_headers(client, "admin"),
    )
    state = auth_resp.json()["url"].rsplit("=", 1)[1]

    # Mock the token exchange — we never call Google.
    def fake_exchange(*, code: str, state: str) -> OAuthExchangeResult:
        _ = (code, state)
        return OAuthExchangeResult(
            google_email="mqeurope@gmail.com",
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=[
                "https://www.googleapis.com/auth/calendar.readonly",
                "https://www.googleapis.com/auth/calendar.events",
            ],
        )

    monkeypatch.setattr(google_service, "exchange_code_for_tokens", fake_exchange)

    response = client.get(
        "/api/integrations/google/callback",
        params={"code": "auth-code-from-google", "state": state},
        follow_redirects=False,
    )
    # The backend redirects the admin back to /admin/integrations.
    assert response.status_code == 302
    assert "/admin/integrations?google_connected=1" in response.headers["location"]

    from app.models.crm import (  # noqa: PLC0415
        ORG_GOOGLE_SINGLETON_ID,
        OrgGoogleIntegration,
    )

    with session_factory() as session:
        integration = session.get(OrgGoogleIntegration, ORG_GOOGLE_SINGLETON_ID)
        assert integration is not None
        assert integration.google_email == "mqeurope@gmail.com"
        assert integration.status == "active"
        assert decrypt(integration.access_token_encrypted) == "new-access"
        assert decrypt(integration.refresh_token_encrypted) == "new-refresh"
        # connected_by_user_id apunta al admin que inició el flujo.
        admin_id = _user_id(session, UserRole.ADMIN)
        assert integration.connected_by_user_id == admin_id


def test_callback_rejects_replayed_or_invalid_state(client: TestClient) -> None:
    response = client.get(
        "/api/integrations/google/callback",
        params={"code": "x", "state": "never-issued"},
        follow_redirects=False,
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Calendar listing + selection
# ---------------------------------------------------------------------------


def test_list_calendars_returns_user_calendars(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_integration(session_factory, user_id=uid, calendar_id=None, calendar_summary=None)

    fake_client = MagicMock()
    fake_client.list_calendars.return_value = [
        {"id": "primary@bomedia.net", "summary": "bart@bomedia.net", "primary": True},
        {"id": "cal-123", "summary": "CRMBO Tareas", "primary": False},
    ]
    monkeypatch.setattr(
        "app.api.google_integrations.GoogleCalendarClient",
        lambda *args, **kwargs: fake_client,
    )

    response = client.get(
        "/api/integrations/google/calendars",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    items = response.json()
    assert {item["id"] for item in items} == {"primary@bomedia.net", "cal-123"}


def test_select_calendar_persists_choice(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_integration(session_factory, user_id=uid, calendar_id=None, calendar_summary=None)

    fake_client = MagicMock()
    fake_client.list_calendars.return_value = [
        {"id": "cal-123", "summary": "CRMBO Tareas", "primary": False},
    ]
    monkeypatch.setattr(
        "app.integrations.google_calendar.service.GoogleCalendarClient",
        lambda *args, **kwargs: fake_client,
    )

    response = client.patch(
        "/api/integrations/google/calendar",
        json={"calendar_id": "cal-123"},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["selected_calendar"]["id"] == "cal-123"
    assert body["selected_calendar"]["summary"] == "CRMBO Tareas"


def test_select_calendar_rejects_id_not_in_user_account(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_integration(session_factory, user_id=uid, calendar_id=None, calendar_summary=None)

    fake_client = MagicMock()
    fake_client.list_calendars.return_value = [{"id": "real-cal", "summary": "x"}]
    monkeypatch.setattr(
        "app.integrations.google_calendar.service.GoogleCalendarClient",
        lambda *args, **kwargs: fake_client,
    )

    response = client.patch(
        "/api/integrations/google/calendar",
        json={"calendar_id": "fake-cal-id"},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


def test_disconnect_marks_row_disconnected_by_user(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PR-OAuth-Google-Unificado Item 12. Desconectar la cuenta org ya NO
    # borra la fila: la marca status='disconnected_by_user' + vacía tokens
    # + conserva la fila para histórico + audit. Solo el admin desconecta.
    with session_factory() as session:
        uid = _user_id(session, UserRole.ADMIN)
    _seed_integration(session_factory, user_id=uid)
    monkeypatch.setattr(
        "app.integrations.google_calendar.service._revoke_tokens",
        lambda _integration: None,
    )

    response = client.delete(
        "/api/integrations/google/disconnect",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200

    from app.models.crm import (  # noqa: PLC0415
        ORG_GOOGLE_SINGLETON_ID,
        OrgGoogleIntegration,
    )

    with session_factory() as session:
        row = session.get(OrgGoogleIntegration, ORG_GOOGLE_SINGLETON_ID)
        assert row is not None  # conservada
        assert row.status == "disconnected_by_user"
        assert row.access_token_encrypted == ""
        assert row.disconnect_audit_id is not None


def test_disconnect_forbidden_for_non_admin(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    # PR-OAuth-Google-Unificado. Un comercial no puede desconectar la
    # cuenta Google org-wide.
    with session_factory() as session:
        uid = _user_id(session, UserRole.ADMIN)
    _seed_integration(session_factory, user_id=uid)
    response = client.delete(
        "/api/integrations/google/disconnect",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Sync hooks on task create / update / delete
# ---------------------------------------------------------------------------


def test_create_task_with_sync_flag_creates_calendar_event(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_integration(session_factory, user_id=uid)

    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def create_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
            captured["calendar_id"] = calendar_id
            captured["body"] = body
            return {"id": "event-xyz", "htmlLink": "https://calendar.google.com/x"}

    monkeypatch.setattr(
        "app.integrations.google_calendar.service.GoogleCalendarClient",
        _FakeClient,
    )

    due_at = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    response = client.post(
        "/api/tasks",
        json={
            "title": "Llamar lead",
            "due_at": due_at,
            "sync_with_google_calendar": True,
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert captured["calendar_id"] == "cal-123"
    assert captured["body"]["summary"] == "Llamar lead"
    assert task["google_event_id"] == "event-xyz"
    assert task["google_calendar_id"] == "cal-123"


def test_create_task_with_sync_flag_but_assignee_not_connected_does_not_break(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task gets created even when the assignee has no Google
    connection — sync is best-effort and silent on missing config."""
    def _explode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("client must not be instantiated")

    monkeypatch.setattr(
        "app.integrations.google_calendar.service.GoogleCalendarClient",
        _explode,
    )
    response = client.post(
        "/api/tasks",
        json={"title": "x", "sync_with_google_calendar": True},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["google_event_id"] is None


def test_task_assigned_to_another_user_syncs_to_their_calendar(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User A creates a task assigned to user B; the event goes to
    B's calendar (manager has the integration here)."""
    with session_factory() as session:
        manager_id = _user_id(session, UserRole.MANAGER)
    _seed_integration(
        session_factory,
        user_id=manager_id,
        calendar_id="manager-cal",
        calendar_summary="Manager",
    )

    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def create_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
            captured["calendar_id"] = calendar_id
            captured["body"] = body
            return {"id": "event-2"}

    monkeypatch.setattr(
        "app.integrations.google_calendar.service.GoogleCalendarClient",
        _FakeClient,
    )

    # Admin creates the task and assigns to manager.
    response = client.post(
        "/api/tasks",
        json={
            "title": "Para manager",
            "assigned_user_id": manager_id,
            "sync_with_google_calendar": True,
        },
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 201, response.text
    assert captured["calendar_id"] == "manager-cal"


def test_delete_task_drops_calendar_event(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_integration(session_factory, user_id=uid)

    calls: list[tuple[str, str]] = []

    class _FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def create_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
            return {"id": "event-del"}

        def delete_event(self, calendar_id: str, event_id: str) -> None:
            calls.append((calendar_id, event_id))

    monkeypatch.setattr(
        "app.integrations.google_calendar.service.GoogleCalendarClient",
        _FakeClient,
    )

    task = client.post(
        "/api/tasks",
        json={"title": "x", "sync_with_google_calendar": True},
        headers=auth_headers(client, "user"),
    ).json()
    response = client.delete(
        f"/api/tasks/{task['id']}", headers=auth_headers(client, "user")
    )
    assert response.status_code == 200
    assert calls == [("cal-123", "event-del")]
    with session_factory() as session:
        assert session.get(Task, task["id"]) is None


def test_patch_task_with_existing_event_updates_calendar(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mini-PR C Fase 3 — edit task: PATCH a task with
    google_event_id set calls update_event with the patched body."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_integration(session_factory, user_id=uid)

    updates: list[dict[str, Any]] = []

    class _FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def create_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
            return {"id": "event-init"}

        def update_event(
            self, calendar_id: str, event_id: str, body: dict[str, Any]
        ) -> dict[str, Any]:
            updates.append({"calendar_id": calendar_id, "event_id": event_id, "body": body})
            return {"id": event_id}

    monkeypatch.setattr(
        "app.integrations.google_calendar.service.GoogleCalendarClient",
        _FakeClient,
    )
    task = client.post(
        "/api/tasks",
        json={"title": "Original", "sync_with_google_calendar": True},
        headers=auth_headers(client, "user"),
    ).json()
    assert task["google_event_id"] == "event-init"

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"title": "Renombrada"},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert len(updates) == 1
    assert updates[0]["event_id"] == "event-init"
    assert updates[0]["body"]["summary"] == "Renombrada"


def test_patch_task_to_enable_sync_creates_event(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Toggling sync ON in the edit modal on a previously-unsynced
    task triggers a create_event call."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_integration(session_factory, user_id=uid)

    creates: list[str] = []

    class _FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def create_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
            creates.append(body["summary"])
            return {"id": "event-late"}

        def update_event(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("update should not run on initial sync-on")

    monkeypatch.setattr(
        "app.integrations.google_calendar.service.GoogleCalendarClient",
        _FakeClient,
    )
    task = client.post(
        "/api/tasks",
        json={"title": "Unsynced"},
        headers=auth_headers(client, "user"),
    ).json()
    assert task["google_event_id"] is None

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"sync_with_google_calendar": True},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert creates == ["Unsynced"]
    assert response.json()["google_event_id"] == "event-late"


def test_patch_task_to_disable_sync_deletes_event(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Toggling sync OFF on a synced task deletes the event and
    clears the google_event_id / google_calendar_id columns."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_integration(session_factory, user_id=uid)

    deletes: list[tuple[str, str]] = []

    class _FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def create_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
            return {"id": "event-toggle"}

        def update_event(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("update should not run when toggling off")

        def delete_event(self, calendar_id: str, event_id: str) -> None:
            deletes.append((calendar_id, event_id))

    monkeypatch.setattr(
        "app.integrations.google_calendar.service.GoogleCalendarClient",
        _FakeClient,
    )
    task = client.post(
        "/api/tasks",
        json={"title": "Has event", "sync_with_google_calendar": True},
        headers=auth_headers(client, "user"),
    ).json()
    assert task["google_event_id"] == "event-toggle"

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"sync_with_google_calendar": False},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert deletes == [("cal-123", "event-toggle")]
    body = response.json()
    assert body["google_event_id"] is None
    assert body["google_calendar_id"] is None


def test_complete_task_renames_event_with_checkmark(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug 4 — when an operator marks a synced task done, the Google
    Calendar event title is prefixed with `✓ ` so completion is
    visible at a glance from the calendar."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_integration(session_factory, user_id=uid)

    titles: list[str] = []

    class _FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def create_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
            titles.append(("create", body["summary"]))
            return {"id": "event-done"}

        def update_event(
            self, calendar_id: str, event_id: str, body: dict[str, Any]
        ) -> dict[str, Any]:
            titles.append(("update", body["summary"]))
            return {"id": event_id}

    monkeypatch.setattr(
        "app.integrations.google_calendar.service.GoogleCalendarClient",
        _FakeClient,
    )
    task = client.post(
        "/api/tasks",
        json={"title": "Llamar cliente", "sync_with_google_calendar": True},
        headers=auth_headers(client, "user"),
    ).json()
    response = client.post(
        f"/api/tasks/{task['id']}/complete",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    update_titles = [t for op, t in titles if op == "update"]
    assert update_titles == ["✓ Llamar cliente"]


def test_reopen_task_reverts_event_title(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving status from done back to pending strips the ✓ prefix
    from the Google Calendar event so the title is again clean."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_integration(session_factory, user_id=uid)

    titles: list[str] = []

    class _FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def create_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
            return {"id": "event-reopen"}

        def update_event(
            self, calendar_id: str, event_id: str, body: dict[str, Any]
        ) -> dict[str, Any]:
            titles.append(body["summary"])
            return {"id": event_id}

    monkeypatch.setattr(
        "app.integrations.google_calendar.service.GoogleCalendarClient",
        _FakeClient,
    )
    task = client.post(
        "/api/tasks",
        json={"title": "Seguimiento", "sync_with_google_calendar": True},
        headers=auth_headers(client, "user"),
    ).json()
    client.post(
        f"/api/tasks/{task['id']}/complete",
        headers=auth_headers(client, "user"),
    )
    # First update lands as "✓ Seguimiento".
    assert titles[-1] == "✓ Seguimiento"
    # Reopen via PATCH back to pending.
    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"status": "pending"},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert titles[-1] == "Seguimiento"


def test_scope_expansion_merges_scopes_and_keeps_per_user_calendar(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-OAuth-Google-Unificado.

    Cuando el admin re-autoriza la cuenta org para añadir scopes de
    Gmail, los scopes se MEZCLAN (no se reemplazan) y la selección de
    calendario per-user (en `user_calendar_prefs`) queda intacta — vive
    en otra tabla que `connect_org` nunca toca."""
    with session_factory() as session:
        admin_id = _user_id(session, UserRole.ADMIN)
    _seed_integration(session_factory, user_id=admin_id)  # calendar per-user

    monkeypatch.setattr(
        "app.api.google_integrations.get_authorize_url",
        lambda state: f"https://accounts.google.com/o/oauth2/auth?state={state}",
    )
    auth_resp = client.get(
        "/api/integrations/google/authorize",
        headers=auth_headers(client, "admin"),
    )
    state = auth_resp.json()["url"].rsplit("=", 1)[1]

    def fake_exchange(*, code: str, state: str) -> OAuthExchangeResult:
        _ = (code, state)
        return OAuthExchangeResult(
            google_email="mqeurope@gmail.com",  # SAME org email
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=[
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.modify",
            ],
        )

    monkeypatch.setattr(google_service, "exchange_code_for_tokens", fake_exchange)

    response = client.get(
        "/api/integrations/google/callback",
        params={"code": "x", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/admin/integrations?google_connected=1" in response.headers["location"]

    from app.models.crm import (  # noqa: PLC0415
        ORG_GOOGLE_SINGLETON_ID,
        OrgGoogleIntegration,
        UserCalendarPref,
    )

    with session_factory() as session:
        integration = session.get(OrgGoogleIntegration, ORG_GOOGLE_SINGLETON_ID)
        assert integration is not None
        # Scopes are merged, not replaced.
        scopes = set(integration.scopes.split())
        assert "https://www.googleapis.com/auth/gmail.send" in scopes
        assert "https://www.googleapis.com/auth/calendar.events" in scopes
        # Per-user calendar selection survives the re-auth untouched.
        pref = session.get(UserCalendarPref, admin_id)
        assert pref is not None
        assert pref.selected_calendar_id == "cal-123"
        assert pref.selected_calendar_summary == "CRMBO Tareas"


# ---------------------------------------------------------------------------
# Sprint Email v1 closing — auto-register Gmail watch on OAuth
# ---------------------------------------------------------------------------


def test_callback_auto_registers_gmail_watch_when_modify_granted(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-OAuth-Google-Unificado. First-time grant of `gmail.modify`
    registers ONE watch for the org account, attributed to the admin
    that connected (`connected_by_user_id`)."""
    calls: list[str] = []
    monkeypatch.setattr(
        "app.integrations.gmail.service.register_watch",
        lambda session, *, user_id: calls.append(user_id),
    )
    monkeypatch.setattr(
        "app.api.google_integrations.get_authorize_url",
        lambda state: f"https://accounts.google.com/o/oauth2/auth?state={state}",
    )
    auth_resp = client.get(
        "/api/integrations/google/authorize",
        headers=auth_headers(client, "admin"),
    )
    state = auth_resp.json()["url"].rsplit("=", 1)[1]

    def fake_exchange(*, code: str, state: str) -> OAuthExchangeResult:
        _ = (code, state)
        return OAuthExchangeResult(
            google_email="mqeurope@gmail.com",
            access_token="t",
            refresh_token="r",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=[
                "https://www.googleapis.com/auth/calendar.events",
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.modify",
            ],
        )

    monkeypatch.setattr(google_service, "exchange_code_for_tokens", fake_exchange)

    response = client.get(
        "/api/integrations/google/callback",
        params={"code": "x", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with session_factory() as session:
        admin_id = _user_id(session, UserRole.ADMIN)
    assert calls == [admin_id]


def test_callback_does_not_re_register_watch_on_subsequent_auth(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-OAuth-Google-Unificado. If the org account already had
    gmail.modify granted, re-authorising should not trigger another
    watch registration — the existing watch keeps its history_id."""
    from app.core.crypto import encrypt  # noqa: PLC0415
    from app.models.crm import (  # noqa: PLC0415
        ORG_GOOGLE_SINGLETON_ID,
        OrgGoogleIntegration,
    )

    with session_factory() as session:
        admin_id = _user_id(session, UserRole.ADMIN)
        session.add(
            OrgGoogleIntegration(
                id=ORG_GOOGLE_SINGLETON_ID,
                google_email="mqeurope@gmail.com",
                access_token_encrypted=encrypt("a"),
                refresh_token_encrypted=encrypt("r"),
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                scopes=(
                    "https://www.googleapis.com/auth/calendar.events "
                    "https://www.googleapis.com/auth/gmail.modify "
                    "https://www.googleapis.com/auth/gmail.send"
                ),
                connected_at=datetime.now(UTC),
                connected_by_user_id=admin_id,
                status="active",
            )
        )
        session.commit()

    calls: list[str] = []
    monkeypatch.setattr(
        "app.integrations.gmail.service.register_watch",
        lambda session, *, user_id: calls.append(user_id),
    )
    monkeypatch.setattr(
        "app.api.google_integrations.get_authorize_url",
        lambda state: f"https://accounts.google.com/o/oauth2/auth?state={state}",
    )
    auth_resp = client.get(
        "/api/integrations/google/authorize",
        headers=auth_headers(client, "admin"),
    )
    state = auth_resp.json()["url"].rsplit("=", 1)[1]

    def fake_exchange(*, code: str, state: str) -> OAuthExchangeResult:
        _ = (code, state)
        return OAuthExchangeResult(
            google_email="mqeurope@gmail.com",
            access_token="new",
            refresh_token="new",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=[
                "https://www.googleapis.com/auth/calendar.events",
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.modify",
            ],
        )

    monkeypatch.setattr(google_service, "exchange_code_for_tokens", fake_exchange)

    response = client.get(
        "/api/integrations/google/callback",
        params={"code": "x", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert calls == []


def test_callback_swallows_watch_register_failure(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `register_watch` raises during OAuth completion, the flow
    still completes successfully. The user can retry from /account
    later via the manual refresh-watch endpoint."""
    def boom(session, *, user_id):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(
        "app.integrations.gmail.service.register_watch", boom
    )
    monkeypatch.setattr(
        "app.api.google_integrations.get_authorize_url",
        lambda state: f"https://accounts.google.com/o/oauth2/auth?state={state}",
    )
    auth_resp = client.get(
        "/api/integrations/google/authorize",
        headers=auth_headers(client, "admin"),
    )
    state = auth_resp.json()["url"].rsplit("=", 1)[1]

    def fake_exchange(*, code: str, state: str) -> OAuthExchangeResult:
        _ = (code, state)
        return OAuthExchangeResult(
            google_email="mqeurope@gmail.com",
            access_token="t",
            refresh_token="r",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=[
                "https://www.googleapis.com/auth/calendar.events",
                "https://www.googleapis.com/auth/gmail.modify",
            ],
        )

    monkeypatch.setattr(google_service, "exchange_code_for_tokens", fake_exchange)

    response = client.get(
        "/api/integrations/google/callback",
        params={"code": "x", "state": state},
        follow_redirects=False,
    )
    # The redirect still goes through (200/302), the user is connected.
    assert response.status_code == 302


def test_org_integration_is_a_singleton(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """PR-OAuth-Google-Unificado. La conexión Google es ÚNICA org-wide:
    una sola fila con PK fija 'singleton'. Reconectar reutiliza la misma
    fila — nunca hay dos. Cada user mantiene su propio calendario y sus
    Send-As aliases, pero comparten tokens."""
    from app.models.crm import (  # noqa: PLC0415
        ORG_GOOGLE_SINGLETON_ID,
        OrgGoogleIntegration,
    )

    with session_factory() as session:
        admin_id = _user_id(session, UserRole.ADMIN)
        user_id = _user_id(session, UserRole.USER)
    # Dos users distintos "conectan" (idempotente) → misma fila singleton.
    first_id = _seed_integration(session_factory, user_id=admin_id)
    second_id = _seed_integration(session_factory, user_id=user_id)
    assert first_id == second_id == ORG_GOOGLE_SINGLETON_ID

    with session_factory() as session:
        rows = list(session.scalars(select(OrgGoogleIntegration)))
    assert len(rows) == 1
    assert rows[0].id == ORG_GOOGLE_SINGLETON_ID
