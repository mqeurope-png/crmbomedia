"""Migration 20260610_0022 — data truncation + column shrink.

Regression for the production failure: the first version used `||`
(boolean OR on MySQL) and crashed with `1292 Truncated incorrect
DOUBLE value`. The migration now branches per dialect; this test
exercises the SQLite path end-to-end (long value → truncated with
the `…` marker → columns shrunk) and asserts the MySQL branch builds
the CONCAT/CHAR_LENGTH statement.

Same bootstrap-and-stamp pattern as the other migration tests: the
full chain can't run on SQLite because of legacy ALTERs, so we
create the minimal `contacts` schema by hand and stamp at 0021.
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


# Minimal contacts schema mirroring the manually-widened production
# state (VARCHAR(500)) right before 0022 runs.
_CONTACTS_DDL = """
CREATE TABLE contacts (
    id VARCHAR(36) PRIMARY KEY,
    first_name VARCHAR(500) NOT NULL,
    last_name VARCHAR(500),
    email VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def test_long_values_truncated_then_columns_shrunk(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(_CONTACTS_DDL))
        long_name = "X" * 300
        long_last = "Y" * 300
        conn.execute(
            text(
                "INSERT INTO contacts (id, first_name, last_name, email) "
                "VALUES ('c1', :fn, :ln, 'a@b.c'), ('c2', 'Ana', 'García', 'd@e.f')"
            ),
            {"fn": long_name, "ln": long_last},
        )
    command.stamp(cfg, "20260610_0021")
    command.upgrade(cfg, "20260610_0022")

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, first_name, last_name FROM contacts ORDER BY id")
        ).fetchall()
    by_id = {row[0]: (row[1], row[2]) for row in rows}

    fn, ln = by_id["c1"]
    assert len(fn) == 120
    assert fn.endswith("…")
    assert len(ln) == 160
    assert ln.endswith("…")
    # Short values untouched.
    assert by_id["c2"] == ("Ana", "García")


def test_mysql_branch_builds_concat_statement():
    """The MySQL path can't run on SQLite, but we can pin the SQL it
    would emit so a refactor can't silently regress to `||` again."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "migration_0022",
        BACKEND_ROOT
        / "alembic"
        / "versions"
        / "20260610_0022_revert_contact_name_columns.py",
    )
    module = importlib.util.module_from_spec(spec)
    captured: list[str] = []

    class _FakeBind:
        class dialect:  # noqa: N801 - mimics SQLAlchemy attribute
            name = "mysql"

    class _FakeOp:
        @staticmethod
        def get_bind():
            return _FakeBind()

        @staticmethod
        def execute(statement):
            captured.append(str(statement))

    spec.loader.exec_module(module)
    module.op = _FakeOp()
    module._truncate("first_name", 120)

    assert len(captured) == 1
    sql = captured[0]
    assert "CONCAT(SUBSTR(first_name, 1, :keep), '…')" in sql
    assert "CHAR_LENGTH(first_name)" in sql
    assert "||" not in sql
