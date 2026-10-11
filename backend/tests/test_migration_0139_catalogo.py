"""Migración 20261011_0139 — las tablas del catálogo y la propuesta interés → máquinas."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings
from app.services.conocimiento.catalogo import MAPA_DE_PARTIDA

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TABLAS = ("kb_catalogo", "kb_catalogo_marcas", "kb_catalogo_sincronizaciones",
          "kb_interes_productos")


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


def test_subir_crea_el_catalogo_vacio_y_siembra_la_propuesta_y_bajar_lo_quita(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261011_0138")
    command.upgrade(cfg, "20261011_0139")

    assert set(TABLAS) <= set(inspect(engine).get_table_names())
    with engine.connect() as c:
        productos = c.execute(text("SELECT COUNT(*) FROM kb_catalogo")).scalar()
        marcas = c.execute(text("SELECT COUNT(*) FROM kb_catalogo_marcas")).scalar()
        mapa = c.execute(text(
            "SELECT interes, marca, patron, nota, activo FROM kb_interes_productos "
            "ORDER BY posicion")).fetchall()
    assert productos == 0 and marcas == 0            # lo llena la primera sincronización
    assert len(mapa) == len(MAPA_DE_PARTIDA) == 15
    assert [(m[0], m[1], m[2], m[3]) for m in mapa] == list(MAPA_DE_PARTIDA)
    assert all(bool(m[4]) for m in mapa)
    assert [m[0] for m in mapa].count("uv_grande") == 5
    assert ("dtf", "SmartJet FLEX", "") == tuple(mapa[12][:3])      # toda la marca, a confirmar
    indices = {i["name"] for i in inspect(engine).get_indexes("kb_catalogo")}
    assert {"ix_kb_catalogo_tipo_marca", "ix_kb_catalogo_idioma", "ix_kb_catalogo_sitio"} <= indices
    unicos = {u["name"] for u in inspect(engine).get_unique_constraints("kb_catalogo")}
    assert "uq_kb_catalogo_clave" in unicos

    command.downgrade(cfg, "20261011_0138")
    assert not set(TABLAS) & set(inspect(engine).get_table_names())
