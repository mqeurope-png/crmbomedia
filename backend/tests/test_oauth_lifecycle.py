"""PR-OAuth-Permisos-Admin — items 9, 12, 13.

Tests del ciclo de vida OAuth: no borrar integraciones, sync de aliases,
avisos de caducidad y visibilidad admin (item 10 cubierto en
test_workflows_pipelines_per_user para el filtrado; aquí el owner_email).
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401  — fuerza el registro de TODOS los modelos
from app.core.crypto import encrypt
from app.integrations.google_calendar import service as google_service
from app.models.crm import (
    ORG_GOOGLE_SINGLETON_ID,
    AuditLog,
    Base,
    OrgGoogleIntegration,
    User,
    UserEmailAliasPref,
    UserRole,
)
from tests._test_helpers import seed_test_users


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
    yield sf
    Base.metadata.drop_all(engine)


def _uid(session: Session, role: UserRole) -> str:
    return session.scalar(select(User.id).where(User.role == role))


def _seed_integration(
    session: Session, user_id: str, *, status: str = "active",
    expires_in_hours: int = 1,
    refresh_expires_in_hours: int | None = None,
) -> OrgGoogleIntegration:
    """PR-OAuth-Google-Unificado. Crea/actualiza la integración ORG
    singleton (id='singleton'). `user_id` se guarda como
    connected_by_user_id. Idempotente.

    `expires_in_hours` es la caducidad del ACCESS token. La del REFRESH
    token (la que dispara los avisos, Bug 14) por defecto sigue a la del
    access para no romper tests antiguos, pero se puede fijar aparte con
    `refresh_expires_in_hours`."""
    now = datetime.now(UTC)
    integ = session.get(OrgGoogleIntegration, ORG_GOOGLE_SINGLETON_ID)
    if integ is None:
        integ = OrgGoogleIntegration(id=ORG_GOOGLE_SINGLETON_ID)
        session.add(integ)
    integ.google_email = "mqeurope@gmail.com"
    integ.access_token_encrypted = encrypt("access")
    integ.refresh_token_encrypted = encrypt("refresh")
    integ.token_expires_at = now + timedelta(hours=expires_in_hours)
    rh = expires_in_hours if refresh_expires_in_hours is None else refresh_expires_in_hours
    integ.refresh_token_expires_at = now + timedelta(hours=rh)
    integ.scopes = "https://www.googleapis.com/auth/gmail.send"
    integ.connected_at = now
    integ.connected_by_user_id = user_id
    integ.status = status
    session.commit()
    session.refresh(integ)
    return integ


# ---------------------------------------------------------------------------
# Item 12 — mark needs_reconnect, no delete
# ---------------------------------------------------------------------------


def test_mark_needs_reconnect_marks_and_audits(factory):
    with factory() as session:
        uid = _uid(session, UserRole.USER)
        _seed_integration(session, uid)
        result = google_service.mark_needs_reconnect(
            session, user_id=uid, error="invalid_grant"
        )
        session.commit()
        assert result is not None
        assert result.status == "needs_reconnect"
        assert result.last_refresh_error == "invalid_grant"
        # Fila conservada (no borrada).
        assert session.scalar(select(OrgGoogleIntegration)) is not None
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "gmail.refresh_failed_permanent"
            )
        )
        assert audit is not None


def test_mark_needs_reconnect_idempotent(factory):
    with factory() as session:
        uid = _uid(session, UserRole.USER)
        _seed_integration(session, uid, status="needs_reconnect")
        google_service.mark_needs_reconnect(
            session, user_id=uid, error="invalid_grant"
        )
        session.commit()
        # No duplica el audit (ya estaba en needs_reconnect).
        n = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "gmail.refresh_failed_permanent"
            )
        )
        assert n is None


def test_client_for_rejects_non_active_status(factory):
    from app.integrations.gmail.service import (
        GmailNotConnectedError,
        _client_for,
    )

    with factory() as session:
        uid = _uid(session, UserRole.USER)
        _seed_integration(session, uid, status="needs_reconnect")
        with pytest.raises(GmailNotConnectedError):
            _client_for(session, uid)


def test_backfill_iter_connected_users_uses_org_status(factory):
    """PR-OAuth-Google-Unificado. La conexión es org-wide: si la cuenta
    org está activa, el backfill recorre TODOS los users del CRM; si la
    cuenta está needs_reconnect, no recorre ninguno."""
    from app.integrations.gmail import backfill as backfill_module

    # Org activa → todos los users del CRM están "conectados".
    with factory() as session:
        admin_uid = _uid(session, UserRole.ADMIN)
        _seed_integration(session, admin_uid, status="active")
        users = backfill_module._iter_connected_users(session)
        ids = {u.user_id for u in users}
        all_active = set(
            session.scalars(select(User.id).where(User.is_active.is_(True)))
        )
        assert ids == all_active
        assert len(ids) >= 2  # admin + manager + user + viewer

    # Org needs_reconnect → ningún user procesable.
    with factory() as session:
        admin_uid = _uid(session, UserRole.ADMIN)
        _seed_integration(session, admin_uid, status="needs_reconnect")
        users = backfill_module._iter_connected_users(session)
        assert users == []


# ---------------------------------------------------------------------------
# Item 13 — sync Send-As aliases
# ---------------------------------------------------------------------------


class _FakeGmailClient:
    def __init__(self, aliases):
        self._aliases = aliases

    def list_send_as_aliases(self):
        return self._aliases


def test_sync_send_as_aliases_sets_default_from_gmail(factory):
    from app.integrations.gmail.aliases import sync_send_as_aliases

    with factory() as session:
        uid = _uid(session, UserRole.USER)
        _seed_integration(session, uid)
        # Estado local: dos aliases, ninguno default.
        session.add_all([
            UserEmailAliasPref(
                user_id=uid, alias_email="bart@bomedia.net",
                is_allowed=True, is_default=False,
            ),
            UserEmailAliasPref(
                user_id=uid, alias_email="info@bomedia.net",
                is_allowed=True, is_default=False,
            ),
        ])
        session.commit()

        fake = _FakeGmailClient([
            {"send_as_email": "bart@bomedia.net", "display_name": "Bart",
             "is_primary": True, "is_default": True},
            {"send_as_email": "info@bomedia.net", "display_name": "Info",
             "is_primary": False, "is_default": False},
        ])
        with patch(
            "app.integrations.gmail.service._client_for", return_value=fake
        ):
            count = sync_send_as_aliases(session, user_id=uid)
        session.commit()
        assert count == 2
        rows = {
            r.alias_email: r
            for r in session.scalars(
                select(UserEmailAliasPref).where(
                    UserEmailAliasPref.user_id == uid
                )
            )
        }
        assert rows["bart@bomedia.net"].is_default is True
        assert rows["info@bomedia.net"].is_default is False


def test_backfill_fallback_prefers_user_email_match(factory):
    from app.integrations.gmail import backfill as backfill_module

    with factory() as session:
        uid = _uid(session, UserRole.USER)
        user = session.get(User, uid)
        # Aliases sin default; uno coincide con user.email.
        session.add_all([
            UserEmailAliasPref(
                user_id=uid, alias_email="aaa@bomedia.net",
                is_allowed=True, is_default=False,
            ),
            UserEmailAliasPref(
                user_id=uid, alias_email=user.email,
                is_allowed=True, is_default=False,
            ),
        ])
        session.commit()
        chosen = backfill_module._iter_aliases(session, uid)
        assert len(chosen) == 1
        assert chosen[0].alias_email == user.email


# ---------------------------------------------------------------------------
# Item 9 — token expiry warning + admin digest
# ---------------------------------------------------------------------------


def test_token_expiry_check_sends_warning_and_audits(factory):
    from app.integrations.gmail import oauth_lifecycle
    from app.services.email import get_email_service

    get_email_service.cache_clear()
    email_svc = get_email_service()
    email_svc.sent.clear()  # type: ignore[attr-defined]

    with factory() as session:
        uid = _uid(session, UserRole.USER)
        # Token caduca en 24h → dentro de la ventana de 48h.
        _seed_integration(session, uid, expires_in_hours=24)
        sent = oauth_lifecycle.token_expiry_check(session)
        assert sent == 1
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "gmail.token_expiry_warning_sent"
            )
        )
        assert audit is not None
    assert len(email_svc.sent) == 1  # type: ignore[attr-defined]


def test_token_expiry_check_dedups_within_12h(factory):
    from app.integrations.gmail import oauth_lifecycle

    with factory() as session:
        uid = _uid(session, UserRole.USER)
        _seed_integration(session, uid, expires_in_hours=24)
        first = oauth_lifecycle.token_expiry_check(session)
        second = oauth_lifecycle.token_expiry_check(session)
        assert first == 1
        assert second == 0  # dedup: ya avisado en <12h


def test_token_expiry_check_skips_when_app_verified(factory, monkeypatch):
    from app.core import config as config_module
    from app.integrations.gmail import oauth_lifecycle

    config_module.get_settings.cache_clear()
    monkeypatch.setenv("GMAIL_APP_VERIFIED", "true")
    config_module.get_settings.cache_clear()
    try:
        with factory() as session:
            uid = _uid(session, UserRole.USER)
            _seed_integration(session, uid, expires_in_hours=24)
            assert oauth_lifecycle.token_expiry_check(session) == 0
    finally:
        config_module.get_settings.cache_clear()


def test_admin_daily_digest_emails_admins(factory):
    from app.integrations.gmail import oauth_lifecycle
    from app.services.email import get_email_service

    get_email_service.cache_clear()
    email_svc = get_email_service()
    email_svc.sent.clear()  # type: ignore[attr-defined]

    with factory() as session:
        uid = _uid(session, UserRole.USER)
        _seed_integration(session, uid, status="needs_reconnect")
        sent = oauth_lifecycle.admin_daily_digest(session)
        assert sent >= 1
    assert len(email_svc.sent) >= 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Bug 14 — banner/crons usan la caducidad del REFRESH token, no del access
# ---------------------------------------------------------------------------


def test_token_expiry_check_uses_refresh_not_access_token(factory):
    from app.integrations.gmail import oauth_lifecycle

    with factory() as session:
        uid = _uid(session, UserRole.USER)
        # Access token caduca en 1h (dentro de 48h) pero el refresh token
        # está a 30 días → NO debe avisar (el access se refresca solo).
        _seed_integration(
            session, uid, expires_in_hours=1, refresh_expires_in_hours=24 * 30
        )
        assert oauth_lifecycle.token_expiry_check(session) == 0

        # Ahora el refresh token sí está a <48h → debe avisar.
        _seed_integration(
            session, uid, expires_in_hours=1, refresh_expires_in_hours=24
        )
        assert oauth_lifecycle.token_expiry_check(session) == 1


def test_admin_digest_uses_refresh_not_access_token(factory):
    from app.integrations.gmail import oauth_lifecycle

    with factory() as session:
        uid = _uid(session, UserRole.USER)
        # Access en 1h, refresh en 30 días, status active → conexión sana,
        # el digest no debe enviar nada.
        _seed_integration(
            session, uid, expires_in_hours=1, refresh_expires_in_hours=24 * 30
        )
        assert oauth_lifecycle.admin_daily_digest(session) == 0

        # Refresh a <48h → el digest sí avisa.
        _seed_integration(
            session, uid, expires_in_hours=1, refresh_expires_in_hours=24
        )
        assert oauth_lifecycle.admin_daily_digest(session) >= 1


def test_gmail_app_verified_true_disables_refresh_token_expiry(factory, monkeypatch):
    from app.core import config as config_module
    from app.integrations.gmail import oauth_lifecycle

    config_module.get_settings.cache_clear()
    monkeypatch.setenv("GMAIL_APP_VERIFIED", "true")
    config_module.get_settings.cache_clear()
    try:
        with factory() as session:
            uid = _uid(session, UserRole.USER)
            # Aunque el refresh esté a <48h, con la app verificada no hay
            # caducidad real → ni el cron de aviso ni el digest actúan.
            _seed_integration(session, uid, refresh_expires_in_hours=24)
            assert oauth_lifecycle.token_expiry_check(session) == 0
            assert oauth_lifecycle.admin_daily_digest(session) == 0
    finally:
        config_module.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Bug 15 — sync de aliases respeta las preferencias previas del user
# ---------------------------------------------------------------------------


def test_sync_aliases_preserves_is_allowed_for_existing_rows(factory):
    from app.integrations.gmail.aliases import sync_send_as_aliases

    with factory() as session:
        uid = _uid(session, UserRole.USER)
        _seed_integration(session, uid)
        session.add_all([
            UserEmailAliasPref(
                user_id=uid, alias_email="keep@bomedia.net",
                is_allowed=True, is_default=False,
            ),
            UserEmailAliasPref(
                user_id=uid, alias_email="hidden@bomedia.net",
                is_allowed=False, is_default=False,
            ),
        ])
        session.commit()

        # Gmail trae los mismos aliases (la cuenta org tiene 50+, aquí 2).
        fake = _FakeGmailClient([
            {"send_as_email": "keep@bomedia.net", "display_name": "Keep",
             "is_default": False},
            {"send_as_email": "hidden@bomedia.net", "display_name": "Hidden",
             "is_default": False},
        ])
        with patch(
            "app.integrations.gmail.service._client_for", return_value=fake
        ):
            sync_send_as_aliases(session, user_id=uid)
        session.commit()
        rows = {
            r.alias_email: r
            for r in session.scalars(
                select(UserEmailAliasPref).where(
                    UserEmailAliasPref.user_id == uid
                )
            )
        }
        # El sync NO debe pisar la preferencia del user.
        assert rows["keep@bomedia.net"].is_allowed is True
        assert rows["hidden@bomedia.net"].is_allowed is False


def test_sync_aliases_creates_new_aliases_as_hidden_except_self_email(factory):
    from app.integrations.gmail.aliases import sync_send_as_aliases

    with factory() as session:
        uid = _uid(session, UserRole.USER)
        user = session.get(User, uid)
        _seed_integration(session, uid)
        # Primer sync: la tabla está vacía para este user.
        fake = _FakeGmailClient([
            {"send_as_email": user.email, "display_name": "Yo",
             "is_default": False},
            {"send_as_email": "brand1@bomedia.net", "display_name": "Brand1",
             "is_default": False},
            {"send_as_email": "brand2@bomedia.net", "display_name": "Brand2",
             "is_default": False},
        ])
        with patch(
            "app.integrations.gmail.service._client_for", return_value=fake
        ):
            count = sync_send_as_aliases(session, user_id=uid)
        session.commit()
        assert count == 3
        rows = {
            r.alias_email: r
            for r in session.scalars(
                select(UserEmailAliasPref).where(
                    UserEmailAliasPref.user_id == uid
                )
            )
        }
        # Solo el alias propio del user nace visible; el resto, oculto.
        assert rows[user.email].is_allowed is True
        assert rows["brand1@bomedia.net"].is_allowed is False
        assert rows["brand2@bomedia.net"].is_allowed is False


def test_sync_aliases_updates_display_name(factory):
    from app.integrations.gmail.aliases import sync_send_as_aliases

    with factory() as session:
        uid = _uid(session, UserRole.USER)
        _seed_integration(session, uid)
        session.add(
            UserEmailAliasPref(
                user_id=uid, alias_email="x@bomedia.net",
                is_allowed=True, is_default=False,
                gmail_display_name="Nombre Viejo",
            )
        )
        session.commit()

        fake = _FakeGmailClient([
            {"send_as_email": "x@bomedia.net", "display_name": "Nombre Nuevo",
             "is_default": False},
        ])
        with patch(
            "app.integrations.gmail.service._client_for", return_value=fake
        ):
            sync_send_as_aliases(session, user_id=uid)
        session.commit()
        row = session.scalar(
            select(UserEmailAliasPref).where(
                UserEmailAliasPref.user_id == uid,
                UserEmailAliasPref.alias_email == "x@bomedia.net",
            )
        )
        assert row.gmail_display_name == "Nombre Nuevo"
        assert row.is_allowed is True  # preservado
