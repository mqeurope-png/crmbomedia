"""Test for migration 20260516_0008 — drops the legacy UNIQUE(system).

The bug we are fixing was masked on SQLite because `batch_alter_table`
recreates the whole table and drops the column-level UNIQUE along the
way. To reproduce the MySQL situation on SQLite we manually bootstrap
the post-`20260515_0007` *MySQL* schema (composite UNIQUE present AND
legacy `UNIQUE(system)` still present), stamp Alembic at `0007`, then
`upgrade` to `0008` and confirm a second row for the same system now
inserts cleanly.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# Mirrors what MySQL would have AFTER 20260515_0007 ran: both the new
# composite UNIQUE on (system, account_id) AND the legacy UNIQUE(system)
# constraint from the original `20260507_0002_integration_settings`. We
# use a *named, table-level* UNIQUE for the legacy constraint because
# that is what `sa.UniqueConstraint("system")` produced in MySQL and is
# what the migration's introspection looks for.
_POST_0007_MYSQL_LIKE_DDL = """
CREATE TABLE integration_accounts (
    id VARCHAR(36) PRIMARY KEY,
    system VARCHAR(32) NOT NULL,
    account_id VARCHAR(64) NOT NULL,
    display_name VARCHAR(255) NOT NULL,
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
    quota_max_contacts INTEGER,
    quota_strategy VARCHAR(32),
    sync_priority INTEGER NOT NULL DEFAULT 100,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_integration_accounts_system UNIQUE (system),
    CONSTRAINT uq_integration_accounts_system_account_id UNIQUE (system, account_id)
)
"""


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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


def _bootstrap_post_0007_mysql_like(cfg: Config, db_url: str) -> None:
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(text(_POST_0007_MYSQL_LIKE_DDL))
    command.stamp(cfg, "20260515_0007")


def _insert_row(connection, *, id_: str, system: str, account_id: str) -> None:
    connection.execute(
        text(
            "INSERT INTO integration_accounts "
            "(id, system, account_id, display_name, enabled, mode, status, "
            "credential_status, sync_priority, created_at, updated_at) "
            "VALUES (:id, :system, :account_id, :display_name, 0, 'sandbox', "
            "'not_configured', 'not_configured', 100, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            "id": id_,
            "system": system,
            "account_id": account_id,
            "display_name": f"{system} {account_id}",
        },
    )


def test_bug_reproduction_pre_0008(alembic_setup):
    """Confirm we accurately reproduced the MySQL failure on SQLite."""
    cfg, db_url = alembic_setup
    _bootstrap_post_0007_mysql_like(cfg, db_url)
    engine = create_engine(db_url)
    with engine.begin() as conn:
        _insert_row(conn, id_="a1", system="agilecrm", account_id="es")
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            _insert_row(conn, id_="a2", system="agilecrm", account_id="uk")


def test_after_0008_two_accounts_for_same_system(alembic_setup):
    """After applying the fix migration the column-level UNIQUE is gone
    and a second row for the same system inserts cleanly."""
    cfg, db_url = alembic_setup
    _bootstrap_post_0007_mysql_like(cfg, db_url)
    command.upgrade(cfg, "20260516_0008")

    engine = create_engine(db_url)
    with engine.begin() as conn:
        _insert_row(conn, id_="a1", system="agilecrm", account_id="es")
        _insert_row(conn, id_="a2", system="agilecrm", account_id="uk")
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT account_id FROM integration_accounts "
                "WHERE system='agilecrm' ORDER BY account_id"
            )
        ).all()
    assert [r.account_id for r in rows] == ["es", "uk"]


def test_after_0008_composite_still_blocks_exact_duplicate(alembic_setup):
    """The composite UNIQUE on (system, account_id) must remain intact."""
    cfg, db_url = alembic_setup
    _bootstrap_post_0007_mysql_like(cfg, db_url)
    command.upgrade(cfg, "20260516_0008")

    engine = create_engine(db_url)
    with engine.begin() as conn:
        _insert_row(conn, id_="a1", system="agilecrm", account_id="es")
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            _insert_row(conn, id_="a2", system="agilecrm", account_id="es")


def test_upgrade_is_idempotent(alembic_setup):
    """Running 0008 twice must not raise (idempotency requirement)."""
    cfg, db_url = alembic_setup
    _bootstrap_post_0007_mysql_like(cfg, db_url)
    command.upgrade(cfg, "20260516_0008")
    # Stamp back, then re-run. The migration should detect the absence
    # of the legacy UNIQUE and presence of the composite and do nothing.
    command.stamp(cfg, "20260515_0007")
    command.upgrade(cfg, "20260516_0008")

    engine = create_engine(db_url)
    with engine.begin() as conn:
        _insert_row(conn, id_="a1", system="agilecrm", account_id="es")
        _insert_row(conn, id_="a2", system="agilecrm", account_id="uk")
