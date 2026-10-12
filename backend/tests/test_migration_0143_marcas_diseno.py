"""Migración 20261012_0143 — los datos de diseño de cada marca del compositor
(`kb_catalogo_marcas.datos_json`) para la tira de marca de un correo."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]

_MARCAS_ANTES = """
CREATE TABLE kb_catalogo_marcas (
    nombre VARCHAR(80) NOT NULL PRIMARY KEY,
    logo VARCHAR(500),
    color VARCHAR(20),
    urls_json TEXT,
    idioma_defecto VARCHAR(5) NOT NULL,
    visto_en DATETIME,
    ido_en DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'marcas.db'}"
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


def test_subir_anade_los_datos_de_diseno_sin_tocar_las_marcas_y_bajar_los_quita(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(_MARCAS_ANTES))
        conn.execute(text(
            "INSERT INTO kb_catalogo_marcas (nombre, logo, color, urls_json, idioma_defecto, "
            "created_at, updated_at) VALUES ('mbo_dtf', 'https://cdn.example/mbo.png', '#e30613', "
            "'{}', 'en', '2026-10-11 09:00:00', '2026-10-11 09:00:00')"))
    command.stamp(cfg, "20261011_0142")
    command.upgrade(cfg, "20261012_0143")
    assert "datos_json" in {c["name"] for c in inspect(engine).get_columns("kb_catalogo_marcas")}
    with engine.connect() as conn:
        fila = conn.execute(text(
            "SELECT nombre, color, idioma_defecto, datos_json FROM kb_catalogo_marcas")).one()
    assert tuple(fila) == ("mbo_dtf", "#e30613", "en", None)
    command.downgrade(cfg, "20261011_0142")
    assert "datos_json" not in {
        c["name"] for c in inspect(engine).get_columns("kb_catalogo_marcas")}
