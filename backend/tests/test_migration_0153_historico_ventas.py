"""Migración 20261030_0153 — las cinco tablas del histórico de ventas."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TABLAS = ("historico_ejercicios", "historico_documentos", "historico_lineas",
          "historico_articulos", "historico_familias")


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'historico.db'}"
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


def test_subir_crea_las_cinco_tablas_y_bajar_las_quita(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261030_0153")

    insp = inspect(engine)
    assert set(TABLAS) <= set(insp.get_table_names())
    assert {u["name"] for u in insp.get_unique_constraints("historico_documentos")} == {
        "uq_historico_documento"}
    indices = {i["name"] for t in TABLAS for i in insp.get_indexes(t)}
    assert {"ix_historico_lineas_codart", "ix_historico_lineas_es_producto",
            "ix_historico_documentos_codcli", "ix_historico_articulos_familia"} <= indices

    command.downgrade(cfg, "20261030_0152")
    assert not set(TABLAS) & set(inspect(engine).get_table_names())
