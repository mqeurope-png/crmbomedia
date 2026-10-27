"""Migración 20261027_0148 — fuentes externas del redactor.

Solo crea tablas (no toca datos que ya existan): la lista blanca de soporte
sembrada con FLUX, la caché, el índice de las webs, sus reglas de partida y
sus pasadas. Se ejecuta SOLO 0147 → 0148 (y vuelta), sobre una base vacía,
como en `test_migration_0124_cuadre`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings
from app.services.webs import reglas_partida

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TABLAS = {"soporte_fuentes", "soporte_cache", "web_indice", "web_indice_reglas",
          "web_indice_pasadas"}


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'fuentes.db'}"
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


def test_crea_las_tablas_siembra_flux_y_las_reglas_y_baja_limpia(alembic_setup):
    cfg, db_url = alembic_setup
    command.stamp(cfg, "20261027_0147")
    command.upgrade(cfg, "20261027_0148")
    engine = create_engine(db_url)
    assert TABLAS <= set(inspect(engine).get_table_names())

    with engine.connect() as c:
        fuentes = c.execute(text(
            "SELECT clave, dominio, tipo, base_url, activo, locales_json FROM soporte_fuentes"
        )).all()
        assert [(f.clave, f.dominio, f.tipo, f.base_url) for f in fuentes] == [
            ("flux", "support.flux3dp.com", "zendesk", "https://support.flux3dp.com")]
        assert json.loads(fuentes[0].locales_json)["es"] == "es-419"
        reglas = c.execute(text(
            "SELECT tipo, patron FROM web_indice_reglas ORDER BY tipo, posicion")).all()
        # Las mismas que las de partida del código (la migración lleva su foto).
        assert [r.patron for r in reglas if r.tipo == "categoria_fuera"] == \
            [p for p, _ in reglas_partida.CATEGORIAS_FUERA]
        assert [r.patron for r in reglas if r.tipo == "pagina_excluir_slug"] == \
            [p for p, _ in reglas_partida.PAGINAS_FUERA]
        # El índice nace vacío: lo llena el primer recorrido.
        assert c.execute(text("SELECT COUNT(*) FROM web_indice")).scalar() == 0
        columnas = {col["name"] for col in inspect(engine).get_columns("web_indice")}
        assert {"sitio", "tipo", "wp_id", "idioma", "titulo", "texto", "imagen",
                "galeria_json", "precio_web", "precio_iva", "modificado_web", "visto_en",
                "ido_en"} <= columnas
    engine.dispose()

    command.downgrade(cfg, "20261027_0147")
    engine = create_engine(db_url)
    assert not (TABLAS & set(inspect(engine).get_table_names()))
    engine.dispose()
