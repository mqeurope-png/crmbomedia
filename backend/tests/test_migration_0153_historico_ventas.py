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


def test_0154_suma_gama_reglas_y_gamas_sin_tocar_la_clase(alembic_setup):
    from sqlalchemy import text

    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261030_0153")
    with engine.begin() as c:
        c.execute(text("INSERT INTO historico_familias (codigo, descripcion, clase) "
                       "VALUES ('601', 'Accesorios figurante', 'maquina')"))
    command.upgrade(cfg, "20261030_0154")
    with engine.connect() as c:
        assert c.execute(text("SELECT clase, gama FROM historico_familias")).one() == (
            "maquina", None)
        gamas = [r[0] for r in c.execute(text("SELECT nombre FROM historico_gamas ORDER BY orden"))]
        assert gamas == ["UV LED", "Láser", "DTF", "CD-DVD", "Packaging", "Textil", "CNC", "Otros"]
        regla = c.execute(text("SELECT clase, gama FROM historico_reglas_familia "
                               "WHERE campo='codigo' AND patron='601'")).one()
        assert regla == ("accesorio", "uv-led")
        assert c.execute(text("SELECT COUNT(*) FROM historico_reglas_familia "
                              "WHERE patron IN ('118', '251', '071', '004')")).scalar() == 0
    command.downgrade(cfg, "20261030_0153")
    cols = {col["name"] for col in inspect(engine).get_columns("historico_familias")}
    assert "gama" not in cols


def test_0155_suma_total_y_estado_a_los_documentos(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261030_0155")
    cols = {c["name"] for c in inspect(engine).get_columns("historico_documentos")}
    assert {"total", "estado"} <= cols
    command.downgrade(cfg, "20261030_0154")
    cols = {c["name"] for c in inspect(engine).get_columns("historico_documentos")}
    assert not {"total", "estado"} & cols


def test_0156_crea_maquinas_a_mano_y_bajas_del_parque(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261030_0156")
    tablas = set(inspect(engine).get_table_names())
    assert {"parque_maquinas_manuales", "parque_bajas"} <= tablas
    command.downgrade(cfg, "20261030_0155")
    assert not {"parque_maquinas_manuales", "parque_bajas"} & set(inspect(engine).get_table_names())


def test_0157_suma_el_origen_de_linea_y_la_clase_propia_del_articulo(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261030_0157")
    cols = {c["name"] for c in inspect(engine).get_columns("historico_lineas")}
    assert {"origen_tipo", "origen_serie", "origen_codigo"} <= cols
    assert "historico_articulos_clase" in inspect(engine).get_table_names()
    command.downgrade(cfg, "20261030_0156")
    cols = {c["name"] for c in inspect(engine).get_columns("historico_lineas")}
    assert not {"origen_tipo", "origen_serie", "origen_codigo"} & cols
    assert "historico_articulos_clase" not in inspect(engine).get_table_names()


def test_0158_reglas_de_articulo_sembradas_y_series(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261031_0158")
    tablas = set(inspect(engine).get_table_names())
    assert {"historico_reglas_articulo", "historico_series"} <= tablas
    with engine.connect() as c:
        n = c.exec_driver_sql("SELECT COUNT(*) FROM historico_reglas_articulo").scalar()
    assert n == 30
    command.downgrade(cfg, "20261030_0157")
    assert not {"historico_reglas_articulo", "historico_series"} & set(
        inspect(engine).get_table_names())
