"""Migración 20261011_0138 — el asistente como conversación y la caché de prompts."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

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


NUEVAS = {"conversacion_id", "turno", "padre_id", "instruccion", "aviso", "contexto_texto",
          "tokens_cache_lectura", "tokens_cache_escritura"}


def test_subir_anade_la_conversacion_y_la_cache_y_bajar_las_quita(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261011_0136")
    command.upgrade(cfg, "20261011_0137")
    # Un borrador de antes de la conversación: tiene que seguir ahí y pasar a
    # ser su propia conversación de un turno.
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ia_borradores (id, thread_id, usuario_id, modelo, tokens_entrada, "
            "tokens_salida, coste_usd, texto, alcance, mensajes_hilo, mensajes_historico, estado, "
            "created_at, updated_at) VALUES ('b-1', 't-1', 'u-1', 'claude-sonnet-5-5', 10, 5, "
            "0.001, 'Hola', 'contacto', 1, 0, 'generado', '2026-10-11 09:00:00', "
            "'2026-10-11 09:00:00')"))
    command.upgrade(cfg, "20261011_0138")
    insp = inspect(engine)
    borradores = {c["name"]: c for c in insp.get_columns("ia_borradores")}
    assert NUEVAS <= set(borradores)
    assert borradores["thread_id"]["nullable"] is True
    llamadas = {c["name"] for c in insp.get_columns("ia_llamadas")}
    assert {"tokens_cache_lectura", "tokens_cache_escritura"} <= llamadas
    indices = {i["name"] for i in insp.get_indexes("ia_borradores")}
    assert "ix_ia_borradores_conversacion_id" in indices
    with engine.connect() as conn:
        fila = conn.execute(text(
            "SELECT conversacion_id, turno, tokens_cache_lectura FROM ia_borradores WHERE id='b-1'"
        )).one()
    assert tuple(fila) == ("b-1", 0, 0)

    command.downgrade(cfg, "20261011_0137")
    insp = inspect(engine)
    borradores = {c["name"]: c for c in insp.get_columns("ia_borradores")}
    assert not NUEVAS & set(borradores)
    assert borradores["thread_id"]["nullable"] is False
    assert not {"tokens_cache_lectura", "tokens_cache_escritura"} & {
        c["name"] for c in insp.get_columns("ia_llamadas")}
