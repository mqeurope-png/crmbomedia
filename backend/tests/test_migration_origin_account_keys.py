"""Migration 20260607_0020 transforms `contact_views.filters_json`
from the legacy `{origin_system, origin_account_id}` pair to the new
`origin_account_keys: ["system:account_id"]` array.

Like `test_migration_integration_accounts`, this test can't run the
full Alembic chain on SQLite because of pre-existing migrations that
use `ALTER COLUMN` outside `batch_alter_table`. We bootstrap the
`contact_views` table by hand and stamp Alembic at the previous
revision, then run only the upgrade we care about.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


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


_CONTACT_VIEWS_DDL = """
CREATE TABLE contact_views (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    owner_user_id VARCHAR(36) NOT NULL,
    is_shared BOOLEAN NOT NULL DEFAULT 0,
    is_default BOOLEAN NOT NULL DEFAULT 0,
    filters_json TEXT,
    columns_json TEXT,
    sort_json TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


def _bootstrap(cfg: Config, db_url: str) -> None:
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(text(_CONTACT_VIEWS_DDL))
    # Stamp Alembic at the revision just before ours so `upgrade` only
    # runs 20260607_0020 — and the prior 20260606_0019 it depends on,
    # which only does an `alter_column` we don't care about here.
    # Both migrations after 0019 are reversible so the stamp path is
    # safe even if a future test depends on a full chain.
    command.stamp(cfg, "20260606_0019")


def _insert_view(
    engine, *, view_id: str, filters: dict
) -> None:
    now = datetime.utcnow().isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO contact_views "
                "(id, name, owner_user_id, is_shared, is_default, "
                "filters_json, columns_json, sort_json, created_at, updated_at) "
                "VALUES (:id, 'V', 'u1', 0, 0, :f, '{}', '{}', :ts, :ts)"
            ),
            {"id": view_id, "f": json.dumps(filters), "ts": now},
        )


def test_legacy_filters_get_collapsed_to_origin_account_keys(alembic_setup):
    cfg, db_url = alembic_setup
    _bootstrap(cfg, db_url)
    engine = create_engine(db_url)

    _insert_view(
        engine,
        view_id="v-legacy",
        filters={"origin_system": "agilecrm", "origin_account_id": "mbomedia"},
    )
    _insert_view(engine, view_id="v-no-origin", filters={"q": "foo"})

    command.upgrade(cfg, "20260607_0020")

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id, filters_json FROM contact_views ORDER BY id")
        ).fetchall()
    by_id = {row[0]: json.loads(row[1]) for row in rows}

    assert by_id["v-legacy"]["origin_account_keys"] == ["agilecrm:mbomedia"]
    # Legacy keys preserved so the route's backwards-compat layer
    # keeps the filter consistent when an older client reads the view.
    assert by_id["v-legacy"]["origin_system"] == "agilecrm"
    assert by_id["v-legacy"]["origin_account_id"] == "mbomedia"

    # Views with no origin filter are untouched.
    assert "origin_account_keys" not in by_id["v-no-origin"]
    assert by_id["v-no-origin"]["q"] == "foo"


def test_downgrade_restores_legacy_keys(alembic_setup):
    cfg, db_url = alembic_setup
    _bootstrap(cfg, db_url)
    command.upgrade(cfg, "20260607_0020")

    engine = create_engine(db_url)
    _insert_view(
        engine,
        view_id="v-new",
        filters={"origin_account_keys": ["brevo:main"]},
    )

    command.downgrade(cfg, "20260606_0019")

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT filters_json FROM contact_views WHERE id = 'v-new'")
        ).fetchone()
    assert row is not None
    filters = json.loads(row[0])
    assert filters["origin_system"] == "brevo"
    assert filters["origin_account_id"] == "main"
    assert "origin_account_keys" not in filters
