"""Migración 20261012_0144 — los correos compuestos: el formato del turno del
asistente (`ia_borradores.modo`) y la estructura de bloques en los turnos y en
los borradores guardados (`estructura_json`)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Las tablas como las deja la cadena anterior (lo que la migración necesita:
# que existan; añadir y quitar columnas no mira las demás).
_IA_BORRADORES_ANTES = """
CREATE TABLE ia_borradores (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    usuario_id VARCHAR(36) NOT NULL,
    modelo VARCHAR(80) NOT NULL,
    texto TEXT NOT NULL,
    html TEXT,
    estado VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""
_EMAIL_DRAFTS_ANTES = """
CREATE TABLE email_drafts (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    subject VARCHAR(500),
    body_html TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'compuestos.db'}"
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


def test_subir_deja_los_turnos_de_antes_como_normales_y_bajar_lo_quita(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(_IA_BORRADORES_ANTES))
        conn.execute(text(_EMAIL_DRAFTS_ANTES))
        conn.execute(text(
            "INSERT INTO ia_borradores (id, usuario_id, modelo, texto, html, estado, created_at, "
            "updated_at) VALUES ('b-1', 'u-1', 'claude-sonnet-5-5', 'Hola Marny', "
            "'<p>Hola Marny</p>', 'generado', '2026-10-12 09:00:00', '2026-10-12 09:00:00')"))
        conn.execute(text(
            "INSERT INTO email_drafts (id, user_id, subject, body_html, created_at, updated_at) "
            "VALUES ('d-1', 'u-1', 'Re: Equipos DTF', '<p>Hola</p>', '2026-10-12 09:00:00', "
            "'2026-10-12 09:00:00')"))
    command.stamp(cfg, "20261012_0143")
    command.upgrade(cfg, "20261012_0144")
    assert {"modo", "estructura_json"} <= _columnas(engine, "ia_borradores")
    assert "estructura_json" in _columnas(engine, "email_drafts")
    with engine.connect() as conn:
        turno = conn.execute(text(
            "SELECT texto, html, modo, estructura_json FROM ia_borradores WHERE id='b-1'")).one()
        borrador = conn.execute(text(
            "SELECT subject, body_html, estructura_json FROM email_drafts WHERE id='d-1'")).one()
    # Lo de antes queda tal cual y como un correo normal, sin estructura.
    assert tuple(turno) == ("Hola Marny", "<p>Hola Marny</p>", "normal", None)
    assert tuple(borrador) == ("Re: Equipos DTF", "<p>Hola</p>", None)
    # Un turno nuevo sin `modo` explícito también es normal (valor por defecto del servidor).
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ia_borradores (id, usuario_id, modelo, texto, estado, created_at, "
            "updated_at) VALUES ('b-2', 'u-1', 'claude-sonnet-5-5', '', 'generado', "
            "'2026-10-12 10:00:00', '2026-10-12 10:00:00')"))
        assert conn.execute(text("SELECT modo FROM ia_borradores WHERE id='b-2'")).scalar() == (
            "normal")

    command.downgrade(cfg, "20261012_0143")
    assert not {"modo", "estructura_json"} & _columnas(engine, "ia_borradores")
    assert "estructura_json" not in _columnas(engine, "email_drafts")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT texto FROM ia_borradores WHERE id='b-1'")).scalar() == (
            "Hola Marny")
