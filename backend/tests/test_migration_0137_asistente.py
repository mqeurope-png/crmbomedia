"""Migración 20261011_0137 — las tablas del asistente y del registro de la IA."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'ia.db'}"
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


def test_subir_crea_las_dos_tablas_y_bajar_las_quita(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261011_0136")
    command.upgrade(cfg, "20261011_0137")
    insp = inspect(engine)
    assert {"ia_llamadas", "ia_borradores"} <= set(insp.get_table_names())
    llamadas = {c["name"] for c in insp.get_columns("ia_llamadas")}
    assert {"proposito", "modelo", "tokens_entrada", "tokens_salida", "coste_usd", "usuario_id",
            "referencia_tipo", "referencia_id", "ok", "error"} <= llamadas
    borradores = {c["name"] for c in insp.get_columns("ia_borradores")}
    assert {"thread_id", "message_id", "contact_id", "usuario_id", "idioma", "modelo", "texto",
            "pendiente_json", "enlaces_json", "contexto_json", "alcance", "mensajes_hilo",
            "mensajes_historico", "estado", "email_message_id", "resuelto_en"} <= borradores
    indices = {i["name"] for i in insp.get_indexes("ia_borradores")}
    assert {"ix_ia_borradores_thread", "ix_ia_borradores_usuario"} <= indices

    command.downgrade(cfg, "20261011_0136")
    assert not {"ia_llamadas", "ia_borradores"} & set(inspect(engine).get_table_names())
