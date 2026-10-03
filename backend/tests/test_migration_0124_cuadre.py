"""Migración 20261003_0124 — tablas del Cuadre (`cuadre_findings`, `cuadre_runs`).

Tablas nuevas sin datos que migrar: subir las crea (con la única por
`check_id + entidad_id`) y bajar las quita. Se ejecuta SOLO esta revisión,
como en `test_migration_0123_seguimiento_sync_meta`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'cuadre.db'}"
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


def test_subir_crea_las_tablas_y_bajar_las_quita(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261001_0123")
    command.upgrade(cfg, "20261003_0124")
    insp = inspect(engine)
    assert {"cuadre_findings", "cuadre_runs"} <= set(insp.get_table_names())
    columnas = {c["name"] for c in insp.get_columns("cuadre_findings")}
    assert {"check_id", "entidad_tipo", "entidad_id", "huella", "detalle_json", "severidad",
            "estado", "visto_por", "motivo", "primera_vez_at", "ultima_vez_at",
            "resuelto_at"} <= columnas
    fila = {"c": "x", "t": "pedido", "e": "1", "h": "h", "d": "{}", "s": "alta",
            "est": "abierto", "f": "2026-10-03 00:00:00"}
    insert = text(
        "INSERT INTO cuadre_findings (id, check_id, entidad_tipo, entidad_id, huella, "
        "detalle_json, severidad, estado, primera_vez_at, ultima_vez_at, abierto_at, "
        "created_at, updated_at) VALUES (:id, :c, :t, :e, :h, :d, :s, :est, :f, :f, :f, :f, :f)"
    )
    with engine.begin() as c:
        c.execute(insert, {"id": "1", **fila})
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as c:
        c.execute(insert, {"id": "2", **fila})                    # misma check + entidad
    command.downgrade(cfg, "20261001_0123")
    assert not {"cuadre_findings", "cuadre_runs"} & set(inspect(engine).get_table_names())
