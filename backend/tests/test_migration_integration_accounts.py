"""Migration smoke test for 20260515_0007 (integration_settings →
integration_accounts).

This test cannot run the full alembic chain on SQLite because the
pre-existing `20260512_0004_user_totp` migration uses
`op.alter_column(..., server_default=None)` which SQLite rejects outside
of `batch_alter_table`. Reworking that migration is out of scope of this
PR. Instead we bootstrap the prior schema manually (only what the new
migration touches: the `integration_settings` table), stamp Alembic at
`20260514_0006`, and run the upgrade to `20260515_0007`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point Alembic and the cached `Settings` at a fresh sqlite file
    and yield the path + Config for the test."""
    sqlite_path = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{sqlite_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    old_cwd = os.getcwd()
    os.chdir(BACKEND_ROOT)
    try:
        yield cfg, db_url
    finally:
        os.chdir(old_cwd)
        get_settings.cache_clear()


# Minimal DDL that matches the pre-0007 state of integration_settings,
# expressed in SQLite-compatible syntax.
_LEGACY_INTEGRATION_SETTINGS_DDL = """
CREATE TABLE integration_settings (
    id VARCHAR(36) PRIMARY KEY,
    system VARCHAR(32) NOT NULL UNIQUE,
    display_name VARCHAR(120) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT 0,
    mode VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    api_base_url VARCHAR(255),
    account_label VARCHAR(255),
    credential_status VARCHAR(80) NOT NULL,
    notes TEXT,
    api_key_encrypted TEXT,
    api_key_set_at DATETIME,
    api_key_last_used_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


def _bootstrap_legacy_schema(cfg: Config, db_url: str) -> None:
    """Create the legacy `integration_settings` table from raw DDL and
    tell Alembic we are at revision 20260514_0006 so the next upgrade
    runs only the rename/expand migration we care about."""
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(text(_LEGACY_INTEGRATION_SETTINGS_DDL))
    command.stamp(cfg, "20260514_0006")


def test_legacy_row_is_preserved_as_default_account(alembic_setup):
    cfg, db_url = alembic_setup
    _bootstrap_legacy_schema(cfg, db_url)

    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO integration_settings "
                "(id, system, display_name, enabled, mode, status, "
                "credential_status, created_at, updated_at) "
                "VALUES ('abc-123', 'agilecrm', 'AgileCRM', 0, 'sandbox', "
                "'not_configured', 'not_configured', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    command.upgrade(cfg, "20260515_0007")

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, system, account_id, display_name, sync_priority "
                "FROM integration_accounts"
            )
        ).all()

    assert len(rows) == 1
    row = rows[0]
    assert row.id == "abc-123"
    assert row.system == "agilecrm"
    assert row.account_id == "default"
    assert row.display_name == "AgileCRM"
    assert row.sync_priority == 100


def test_unique_constraint_allows_multiple_accounts_per_system(alembic_setup):
    cfg, db_url = alembic_setup
    _bootstrap_legacy_schema(cfg, db_url)
    command.upgrade(cfg, "20260515_0007")

    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO integration_accounts "
                "(id, system, account_id, display_name, enabled, mode, status, "
                "credential_status, sync_priority, created_at, updated_at) "
                "VALUES ('a1', 'agilecrm', 'es', 'AgileCRM ES', 0, 'sandbox', "
                "'not_configured', 'not_configured', 100, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO integration_accounts "
                "(id, system, account_id, display_name, enabled, mode, status, "
                "credential_status, sync_priority, created_at, updated_at) "
                "VALUES ('a2', 'agilecrm', 'uk', 'AgileCRM UK', 0, 'sandbox', "
                "'not_configured', 'not_configured', 100, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT account_id FROM integration_accounts "
                "WHERE system='agilecrm' ORDER BY account_id"
            )
        ).all()
    assert [r.account_id for r in rows] == ["es", "uk"]
