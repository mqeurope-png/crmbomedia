"""Migración 20261011_0136 — las tablas de la base de conocimiento y su siembra."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings
from app.services.conocimiento.flux import CATEGORIAS_DE_PARTIDA
from app.services.conocimiento.rutas import RUTAS_DE_PARTIDA

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TABLAS = ("kb_rutas", "kb_flux_categorias", "kb_flux_articulos", "kb_flux_sincronizaciones")


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'kb.db'}"
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


def test_subir_siembra_rutas_y_categorias_y_bajar_quita_las_tablas(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261011_0135")
    command.upgrade(cfg, "20261011_0136")

    tablas = set(inspect(engine).get_table_names())
    assert set(TABLAS) <= tablas
    with engine.connect() as c:
        rutas = c.execute(text(
            "SELECT tipo, sitio, idioma, interes, url, activo FROM kb_rutas ORDER BY posicion"
        )).fetchall()
        categorias = c.execute(text(
            "SELECT categoria_id, modelo, palabras_json, activo FROM kb_flux_categorias "
            "ORDER BY posicion"
        )).fetchall()
        articulos = c.execute(text("SELECT COUNT(*) FROM kb_flux_articulos")).scalar()
    assert len(rutas) == len(RUTAS_DE_PARTIDA) == 36
    assert [(r[0], r[1], r[2], r[3], r[4]) for r in rutas] == [
        (r.tipo, r.sitio, r.idioma, r.interes, r.url) for r in RUTAS_DE_PARTIDA]
    assert all(bool(r[5]) for r in rutas)
    assert len(categorias) == len(CATEGORIAS_DE_PARTIDA) == 13
    assert [c[0] for c in categorias] == [c.categoria_id for c in CATEGORIAS_DE_PARTIDA]
    assert json.loads(categorias[3][2]) == ["beambox ii", "beambox 2"]
    assert articulos == 0                       # el índice lo llena la primera sincronización
    indices = {i["name"] for i in inspect(engine).get_indexes("kb_flux_articulos")}
    assert {"ix_kb_flux_articulos_locale_categoria", "ix_kb_flux_articulos_idioma"} <= indices

    command.downgrade(cfg, "20261011_0135")
    assert not set(TABLAS) & set(inspect(engine).get_table_names())
