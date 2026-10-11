"""Migración 20261011_0135 — la tabla `sitios_web` y su siembra."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings
from app.services.web_forms.sitios import SITIOS_DE_PARTIDA

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'sitios.db'}"
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


def test_subir_siembra_los_sitios_de_partida_y_bajar_quita_la_tabla(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261010_0134")
    command.upgrade(cfg, "20261011_0135")

    assert "sitios_web" in inspect(engine).get_table_names()
    with engine.connect() as c:
        filas = c.execute(text(
            "SELECT clave, dominio, host, dominios_extra_json, tipo, idioma_defecto, idiomas_json, "
            "plugin_traduccion, soporte_propio, remitente_defecto, activo FROM sitios_web "
            "ORDER BY posicion"
        )).fetchall()
    assert [f[0] for f in filas] == [s.clave for s in SITIOS_DE_PARTIDA]
    por_clave = {f[0]: f for f in filas}
    flux = por_clave["fluxlasers"]
    assert flux[1] == "fluxlasers.es" and json.loads(flux[3]) == ["fluxlasers.eu"]
    assert json.loads(flux[6]) == ["es", "en", "de", "fr", "nl"] and flux[7] == "wpml"
    assert bool(flux[8]) is True and flux[9] == "info@fluxlasers.es"
    assert por_clave["mqeurope"][2] == "www.mqeurope.com" and por_clave["mqeurope"][5] == "en"
    assert por_clave["artisjet-eu"][5] == "fr"
    assert por_clave["bomedia"][4] == "correo" and por_clave["bomedia"][9] is None
    assert all(bool(f[10]) for f in filas)

    command.downgrade(cfg, "20261010_0134")
    assert "sitios_web" not in inspect(engine).get_table_names()
