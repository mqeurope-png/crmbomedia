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


def test_0159_pistas_con_tipo_y_positivas(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261031_0159")
    with engine.connect() as c:
        tipos = dict(c.exec_driver_sql(
            "SELECT tipo, COUNT(*) FROM historico_reglas_articulo GROUP BY tipo").all())
    assert tipos == {"maquina": 14, "no_maquina": 30 + 14}
    command.downgrade(cfg, "20261031_0158")
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT COUNT(*) FROM historico_reglas_articulo").scalar() == 30


def test_0159_no_duplica_ni_borra_lo_anadido_a_mano(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261031_0158")
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO historico_reglas_articulo (patron, orden) "
                          "VALUES ('Motor', 100), ('impresora', 100)")
    command.upgrade(cfg, "20261031_0159")
    with engine.connect() as c:
        filas = c.exec_driver_sql(
            "SELECT patron, tipo FROM historico_reglas_articulo "
            "WHERE UPPER(patron) IN ('MOTOR', 'IMPRESORA')").all()
    assert sorted(filas) == [("Motor", "no_maquina"), ("impresora", "maquina")]
    command.downgrade(cfg, "20261031_0158")
    with engine.connect() as c:
        quedan = {p for (p,) in c.exec_driver_sql(
            "SELECT patron FROM historico_reglas_articulo").all()}
    assert {"Motor", "impresora"} <= quedan and "BEAMBOX" not in quedan


def test_0160_umbral_y_positivas_sin_tocar_lo_marcado(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261031_0159")
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO historico_articulos_clase (codart, clase, por) "
                          "VALUES ('A1', 'maquina', 'bart'), ('A2', 'no_maquina', 'bart')")
    command.upgrade(cfg, "20261101_0160")
    with engine.connect() as c:
        marcas = dict(c.exec_driver_sql(
            "SELECT codart, clase FROM historico_articulos_clase").all())
        umbral = c.exec_driver_sql("SELECT valor FROM historico_ajustes "
                                   "WHERE clave = 'umbral_precio_maquina'").scalar()
        adr = c.exec_driver_sql("SELECT tipo FROM historico_reglas_articulo "
                                "WHERE patron = 'ADR'").scalar()
    assert marcas == {"A1": "maquina", "A2": "no_maquina"}
    assert umbral == "2000" and adr == "maquina"
    command.downgrade(cfg, "20261031_0159")
    assert "historico_ajustes" not in inspect(engine).get_table_names()


def test_0161_palabra_entera_en_hexa_y_lamina(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261101_0161")
    with engine.connect() as c:
        enteras = {p for (p,) in c.exec_driver_sql(
            "SELECT patron FROM historico_reglas_articulo WHERE palabra_entera").all()}
        n_encelo = c.exec_driver_sql("SELECT COUNT(*) FROM historico_reglas_articulo "
                                     "WHERE patron = 'ENCELOFANADORA'").scalar()
        laminadora = c.exec_driver_sql("SELECT tipo FROM historico_reglas_articulo "
                                       "WHERE patron = 'LAMINADORA'").scalar()
    assert enteras == {"HEXA", "LAMINA"}
    assert n_encelo == 1 and laminadora == "maquina"        # sin duplicar la de la 0159
    command.downgrade(cfg, "20261101_0160")
    assert "palabra_entera" not in {c["name"] for c in inspect(engine).get_columns(
        "historico_reglas_articulo")}


def test_0162_clasifica_las_tres_sin_clase_y_no_toca_uv_ni_lo_de_bart(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261101_0161")
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO historico_familias (codigo, descripcion, clase, gama, clase_por) VALUES "
            "('071', 'Fluxlasers', NULL, NULL, NULL), "
            "('251', 'Flux no en la web', NULL, NULL, NULL), "
            "('118', 'GENERAL', NULL, NULL, NULL), "
            "('UV', 'UV', 'otro', NULL, 'bart'), "
            "('021', 'FLUX', 'maquina', 'laser', 'bart')")
        c.exec_driver_sql("INSERT INTO historico_articulos_clase (codart, clase, por) "
                          "VALUES ('A1', 'maquina', 'bart')")
    command.upgrade(cfg, "20261101_0162")

    def familias():
        with engine.connect() as c:
            return {k: (cl, g) for k, cl, g in c.exec_driver_sql(
                "SELECT codigo, clase, gama FROM historico_familias").all()}

    assert familias() == {"071": ("maquina", "laser"), "251": ("consumible", "laser"),
                          "118": ("otro", None), "UV": ("otro", None),
                          "021": ("maquina", "laser")}
    with engine.connect() as c:
        negativas = {p for (p,) in c.exec_driver_sql(
            "SELECT patron FROM historico_reglas_articulo WHERE orden = 162 "
            "AND tipo = 'no_maquina'").all()}
        marcas = c.exec_driver_sql("SELECT COUNT(*) FROM historico_articulos_clase").scalar()
    assert negativas == {"MODULE", "MÓDULO", "ROTATORIO"} and marcas == 1
    # Bart cambia la 251 después: el downgrade no la toca.
    with engine.begin() as c:
        c.exec_driver_sql("UPDATE historico_familias SET clase = 'recambio', clase_por = 'bart' "
                          "WHERE codigo = '251'")
    command.downgrade(cfg, "20261101_0161")
    assert familias() == {"071": (None, None), "251": ("recambio", "laser"),
                          "118": (None, None), "UV": ("otro", None),
                          "021": ("maquina", "laser")}
