"""Migración 20261001_0123 — tabla de estado del espejo (`seguimiento_sync_meta`).

Subir solo crea la tabla (lo guardado lo convierte el espejo en su pasada).
Bajar, si el espejo ya convirtió lo guardado a 20 columnas (marca
«formato_bd» = 20), lo devuelve a 19 —quita «Courier»— antes de borrar la
tabla: así, al volver a subir, se convierte una sola vez (y no dos). Como en
`test_migration_store_keys`, se montan a mano las tablas que toca y se
ejecuta SOLO esta revisión.
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

BACKEND_ROOT = Path(__file__).resolve().parents[1]

#: Una fila de 20 columnas: «Courier» en la 14 y la «id» la última (19).
FILA_20 = ["Enviado", "BOP-1", "", "Cliente", "", "", "", "", "", "", "", "", "",
           "Enviado", "UPS", "", "1Z", "", "nota", "ord-1"]


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'sync_meta.db'}"
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


def _tablas(c) -> None:
    c.execute(text("CREATE TABLE seguimiento_sync_snapshot (row_id VARCHAR(36) PRIMARY KEY, "
                   "kind VARCHAR(10), values_json TEXT)"))
    c.execute(text("CREATE TABLE seguimiento_manual (id VARCHAR(36) PRIMARY KEY, "
                   "row_id VARCHAR(36), values_json TEXT)"))
    c.execute(text("CREATE TABLE seguimiento_legacy (id VARCHAR(36) PRIMARY KEY, "
                   "raw_json TEXT)"))


def test_subir_crea_la_tabla_y_bajar_devuelve_lo_guardado_a_19_columnas(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    with engine.begin() as c:
        _tablas(c)
    command.stamp(cfg, "20260930_0122")
    command.upgrade(cfg, "20261001_0123")
    assert "seguimiento_sync_meta" in inspect(engine).get_table_names()

    manual = [*FILA_20[:19], "man-1"]
    legacy = FILA_20[:19]                                   # sin la «id» (datos)
    with engine.begin() as c:
        # Lo que deja el espejo tras su primera pasada: todo en 20 columnas.
        c.execute(text("INSERT INTO seguimiento_sync_meta VALUES "
                       "('formato_bd', '20', '2026-10-01', '2026-10-01')"))
        c.execute(text("INSERT INTO seguimiento_sync_snapshot VALUES (:r, :k, :v)"),
                  [{"r": "ord-1", "k": "order", "v": json.dumps(FILA_20)},
                   {"r": "leg-1", "k": "legacy", "v": "[]"}])
        c.execute(text("INSERT INTO seguimiento_manual VALUES ('m1', 'man-1', :v)"),
                  {"v": json.dumps(manual)})
        c.execute(text("INSERT INTO seguimiento_legacy VALUES ('leg-1', :v)"),
                  {"v": json.dumps(legacy)})

    command.downgrade(cfg, "20260930_0122")
    assert "seguimiento_sync_meta" not in inspect(engine).get_table_names()
    with engine.connect() as c:
        foto = json.loads(c.execute(text(
            "SELECT values_json FROM seguimiento_sync_snapshot WHERE row_id = 'ord-1'"
        )).scalar_one())
        man = json.loads(c.execute(text("SELECT values_json FROM seguimiento_manual")).scalar_one())
        leg = json.loads(c.execute(text("SELECT raw_json FROM seguimiento_legacy")).scalar_one())
        hist_foto = c.execute(text(
            "SELECT values_json FROM seguimiento_sync_snapshot WHERE row_id = 'leg-1'"
        )).scalar_one()
    # Sin «Courier»: la «id» vuelve a la 18 y lo de detrás de «Envío», atrás.
    assert foto == [*FILA_20[:14], *FILA_20[15:]] and foto[18] == "ord-1"
    assert man[18] == "man-1" and man[13] == "Enviado" and man[14] == ""
    assert len(leg) == 18 and leg[15] == "1Z" and leg[17] == "nota"
    assert hist_foto == "[]"


def test_bajar_sin_la_marca_no_toca_lo_guardado(alembic_setup):
    """Si el espejo aún no convirtió nada (sin marca), bajar solo borra la tabla."""
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    vieja = [*FILA_20[:14], *FILA_20[15:]]                   # 19 columnas
    with engine.begin() as c:
        _tablas(c)
        c.execute(text("INSERT INTO seguimiento_manual VALUES ('m1', 'ord-1', :v)"),
                  {"v": json.dumps(vieja)})
    command.stamp(cfg, "20260930_0122")
    command.upgrade(cfg, "20261001_0123")
    command.downgrade(cfg, "20260930_0122")
    with engine.connect() as c:
        assert json.loads(c.execute(text(
            "SELECT values_json FROM seguimiento_manual")).scalar_one()) == vieja
