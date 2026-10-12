"""Migración 20261012_0145 — el índice de imágenes de las webs y la tabla de vídeos."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TABLAS = ("kb_imagenes", "kb_videos")


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'medios.db'}"
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


def test_subir_crea_las_dos_tablas_vacias_y_bajar_las_quita(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261012_0144")
    command.upgrade(cfg, "20261012_0145")

    assert set(TABLAS) <= set(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM kb_imagenes")).scalar() == 0
        assert c.execute(text("SELECT COUNT(*) FROM kb_videos")).scalar() == 0
        # Los valores por defecto de una fila escrita a mano (sin el ORM).
        c.execute(text(
            "INSERT INTO kb_videos (id, plataforma, video_id, url, titulo, visto_en, created_at, "
            "updated_at) VALUES ('v1', 'youtube', 'dQw4w9WgXcQ', 'https://youtu.be/dQw4w9WgXcQ', "
            "'x', '2026-10-12', '2026-10-12', '2026-10-12')"))
        fila = c.execute(text("SELECT marca, origen, visible, corto FROM kb_videos")).one()
        c.rollback()
    assert (fila[0], fila[1], bool(fila[2]), bool(fila[3])) == ("", "canal", True, False)
    unicos = {u["name"] for t in TABLAS for u in inspect(engine).get_unique_constraints(t)}
    assert {"uq_kb_imagenes_clave", "uq_kb_videos_clave"} <= unicos
    indices = {i["name"] for t in TABLAS for i in inspect(engine).get_indexes(t)}
    assert {"ix_kb_imagenes_sitio", "ix_kb_videos_tema", "ix_kb_videos_idioma"} <= indices

    command.downgrade(cfg, "20261012_0144")
    assert not set(TABLAS) & set(inspect(engine).get_table_names())
