"""Migración 20261010_0132 — `lead_classifications` y los campos `lead_*` del
contacto.

Se ejecuta SOLO esta revisión (como en `test_migration_0124_cuadre`), sobre una
base con una tabla `contacts` mínima: la migración añade columnas con
`batch_alter_table`, así que la tabla tiene que existir.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'leads.db'}"
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


def test_subir_crea_la_tabla_y_las_columnas_y_bajar_las_quita(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    with engine.begin() as c:
        c.execute(text(
            "CREATE TABLE contacts (id VARCHAR(36) PRIMARY KEY, first_name VARCHAR(120))"
        ))
    command.stamp(cfg, "20261010_0131")
    command.upgrade(cfg, "20261010_0132")

    insp = inspect(engine)
    assert "lead_classifications" in insp.get_table_names()
    columnas = {c["name"] for c in insp.get_columns("lead_classifications")}
    assert {"contact_id", "source", "source_ref", "lead_at", "input_text", "input_json",
            "language", "language_source", "language_mismatch", "interest", "interest_source",
            "is_spam", "confidence", "reason", "provider", "status", "template_id",
            "sender_email", "draft_id", "task_id", "workflow_run_id", "corrected_interest",
            "corrected_is_spam", "corrected_by_user_id", "corrected_at"} <= columnas
    contacto = {c["name"] for c in insp.get_columns("contacts")}
    assert {"lead_interest", "lead_is_spam", "lead_confidence", "lead_classified_at"} <= contacto

    # Un lead (fuente + referencia) se clasifica UNA vez.
    insert = text(
        "INSERT INTO lead_classifications (id, contact_id, source, source_ref, "
        "language_mismatch, is_spam, status, created_at, updated_at) "
        "VALUES (:id, 'c1', 'web_form', 'envio-1', 0, 0, 'clasificado', :f, :f)"
    )
    with engine.begin() as c:
        c.execute(text("INSERT INTO contacts (id, first_name) VALUES ('c1', 'Lead')"))
        c.execute(insert, {"id": "1", "f": "2026-10-10 00:00:00"})
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as c:
        c.execute(insert, {"id": "2", "f": "2026-10-10 00:00:00"})

    command.downgrade(cfg, "20261010_0131")
    insp = inspect(engine)
    assert "lead_classifications" not in insp.get_table_names()
    contacto = {c["name"] for c in insp.get_columns("contacts")}
    assert not {"lead_interest", "lead_is_spam", "lead_confidence", "lead_classified_at"} & contacto
