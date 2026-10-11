"""Migración 20261011_0142 — el catálogo contra los datos reales: precio
público y entradas descartadas."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]

_KB_CATALOGO_ANTES = """
CREATE TABLE kb_catalogo (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    fuente VARCHAR(12) NOT NULL,
    tipo VARCHAR(12) NOT NULL,
    marca VARCHAR(80) NOT NULL,
    origen_id VARCHAR(80) NOT NULL,
    nombre VARCHAR(255) NOT NULL,
    precio_texto VARCHAR(80),
    idioma VARCHAR(5) NOT NULL,
    sitio VARCHAR(40) NOT NULL,
    visible BOOLEAN NOT NULL,
    visto_en DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'catalogo.db'}"
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


NUEVAS = {"precio_publico", "descartado", "motivo_descarte"}


def test_subir_anade_las_columnas_con_su_defecto_y_bajar_las_quita(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(_KB_CATALOGO_ANTES))
        conn.execute(text(
            "INSERT INTO kb_catalogo (id, fuente, tipo, marca, origen_id, nombre, precio_texto, "
            "idioma, sitio, visible, visto_en, created_at, updated_at) VALUES ('p-1', "
            "'compositor', 'maquina', 'mbo', 'mbo-6090', 'MBO 6090', '9.300 €', 'es', '', 1, "
            "'2026-10-11 09:00:00', '2026-10-11 09:00:00', '2026-10-11 09:00:00')"))
    command.stamp(cfg, "20261011_0141")
    assert not NUEVAS & _columnas(engine, "kb_catalogo")

    command.upgrade(cfg, "20261011_0142")
    assert NUEVAS <= _columnas(engine, "kb_catalogo")
    # Lo que ya había: precio público y no descartado (la siguiente pasada lo recalcula).
    with engine.connect() as conn:
        fila = conn.execute(text(
            "SELECT precio_publico, descartado, motivo_descarte FROM kb_catalogo WHERE id='p-1'"
        )).one()
    assert tuple(fila) == (1, 0, None)

    command.downgrade(cfg, "20261011_0141")
    assert not NUEVAS & _columnas(engine, "kb_catalogo")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT nombre FROM kb_catalogo WHERE id='p-1'")).scalar() == (
            "MBO 6090")
