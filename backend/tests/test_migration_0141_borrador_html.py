"""Migración 20261011_0141 — el borrador del asistente en HTML sencillo."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# La tabla como la deja la cadena anterior (lo que la migración necesita: que
# exista; añadir y quitar una columna no mira las demás).
_IA_BORRADORES_ANTES = """
CREATE TABLE ia_borradores (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    thread_id VARCHAR(36),
    usuario_id VARCHAR(36) NOT NULL,
    modelo VARCHAR(80) NOT NULL,
    texto TEXT NOT NULL,
    estado VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'ia_html.db'}"
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


def test_subir_anade_html_y_bajar_lo_quita_sin_perder_el_texto(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(_IA_BORRADORES_ANTES))
        conn.execute(text(
            "INSERT INTO ia_borradores (id, thread_id, usuario_id, modelo, texto, estado, "
            "created_at, updated_at) VALUES ('b-1', 't-1', 'u-1', 'claude-sonnet-5-5', "
            "'Hola Klaus', 'generado', '2026-10-11 09:00:00', '2026-10-11 09:00:00')"))
    command.stamp(cfg, "20261011_0140")
    assert "html" not in _columnas(engine, "ia_borradores")

    command.upgrade(cfg, "20261011_0141")
    assert "html" in _columnas(engine, "ia_borradores")
    with engine.connect() as conn:
        fila = conn.execute(text("SELECT texto, html FROM ia_borradores WHERE id='b-1'")).one()
    assert tuple(fila) == ("Hola Klaus", None)       # los de antes: sin HTML, se deriva del texto

    command.downgrade(cfg, "20261011_0140")
    assert "html" not in _columnas(engine, "ia_borradores")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT texto FROM ia_borradores WHERE id='b-1'")).scalar() == (
            "Hola Klaus")
