"""Migración 20261011_0140 — el borrador guardado arrastra la conversación."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# La tabla tal y como la deja la cadena anterior (lo que necesita la migración:
# que exista; el resto de columnas no importa para añadir o quitar una).
_EMAIL_DRAFTS_ANTES = """
CREATE TABLE email_drafts (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    thread_id VARCHAR(36),
    subject VARCHAR(500),
    body_html TEXT,
    to_emails_json TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'borradores.db'}"
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


def _columnas(engine, tabla: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(tabla)}


def test_subir_anade_la_columna_y_bajar_la_quita(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(_EMAIL_DRAFTS_ANTES))
        conn.execute(text(
            "INSERT INTO email_drafts (id, user_id, subject, created_at, updated_at) VALUES "
            "('d-1', 'u-1', 'Antes', '2026-10-11 09:00:00', '2026-10-11 09:00:00')"))
    command.stamp(cfg, "20261011_0139")
    assert "asistente_borrador_id" not in _columnas(engine, "email_drafts")

    command.upgrade(cfg, "20261011_0140")
    assert "asistente_borrador_id" in _columnas(engine, "email_drafts")
    with engine.connect() as conn:
        fila = conn.execute(text(
            "SELECT subject, asistente_borrador_id FROM email_drafts WHERE id='d-1'")).one()
    assert tuple(fila) == ("Antes", None)

    command.downgrade(cfg, "20261011_0139")
    assert "asistente_borrador_id" not in _columnas(engine, "email_drafts")
    with engine.connect() as conn:
        asunto = conn.execute(text("SELECT subject FROM email_drafts WHERE id='d-1'")).scalar()
    assert asunto == "Antes"
