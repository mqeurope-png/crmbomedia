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


def test_0163_equivalencias_y_familias_que_faltan_sin_tocar_nada_de_bart(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261101_0162")
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO historico_familias (codigo, descripcion, clase, gama, clase_por) VALUES "
            "('019', 'SPARE PARTS ROLLCOATER', 'recambio', NULL, 'bart'), "
            "('UV', 'UV', 'consumible', 'uv-led', 'bart')")
        c.exec_driver_sql(
            "INSERT INTO historico_articulos (ejercicio, codart, descripcion, familia) VALUES "
            "('2017', '60995', 'FLUX BEAMBOX 40W', '19'), "
            "('2026', 'R1', 'RODILLO', '019')")
        c.exec_driver_sql("INSERT INTO historico_articulos_clase (codart, clase, por) "
                          "VALUES ('60995', 'maquina', 'bart'), ('X1', 'no_maquina', 'bart')")
        c.exec_driver_sql("INSERT INTO historico_series (serie, cuenta, por) "
                          "VALUES ('3', 0, 'bart')")

    def foto():
        with engine.connect() as c:
            return (
                sorted(c.exec_driver_sql(
                    "SELECT codigo, descripcion, clase, gama FROM historico_familias").all()),
                sorted(c.exec_driver_sql(
                    "SELECT codart, clase, por FROM historico_articulos_clase").all()),
                c.exec_driver_sql("SELECT serie, cuenta FROM historico_series").all(),
            )

    antes = foto()
    command.upgrade(cfg, "20261101_0163")
    fams, marcas, series = foto()
    assert marcas == antes[1] and series == antes[2]
    # La «19» entra sin clase; la «019» de Bart sigue como estaba.
    assert fams == sorted([*antes[0], ("19", None, None, None)])
    with engine.connect() as c:
        pares = sorted(c.exec_driver_sql(
            "SELECT canonico, codart FROM historico_equivalencias").all())
    assert pares == sorted([("60995", "34"), ("61000", "234"), ("60997", "1398"),
                            ("BT2EYB", "4173"), ("61403", "4174")])
    command.downgrade(cfg, "20261101_0162")
    assert foto() == antes


def test_0164_alias_de_articulo_sin_tocar_nada_de_bart(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261101_0163")
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO historico_familias (codigo, descripcion, clase, clase_por) "
                          "VALUES ('19', NULL, NULL, NULL), ('019', 'X', 'recambio', 'bart')")
        c.exec_driver_sql("INSERT INTO historico_articulos_clase (codart, clase, por) "
                          "VALUES ('53332', 'maquina', 'bart')")

    def foto():
        with engine.connect() as c:
            return (sorted(c.exec_driver_sql(
                        "SELECT codigo, descripcion, clase FROM historico_familias").all()),
                    c.exec_driver_sql("SELECT codart, clase FROM historico_articulos_clase").all())

    antes = foto()
    command.upgrade(cfg, "20261101_0164")
    assert foto() == antes
    with engine.connect() as c:
        n = c.exec_driver_sql("SELECT COUNT(*) FROM historico_alias_articulo").scalar()
    assert n == 9
    command.downgrade(cfg, "20261101_0163")
    assert foto() == antes


def test_0165_resumen_por_empresa_y_el_sector_de_padel(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261101_0164")
    with engine.begin() as c:
        # La base de este test arranca sellada en la 0152: sin `companies`.
        c.exec_driver_sql("CREATE TABLE companies (id VARCHAR(36) PRIMARY KEY, "
                          "name VARCHAR(255), sector VARCHAR(120))")
        c.exec_driver_sql("INSERT INTO companies (id, name, sector) VALUES "
                          "('c1', 'Figurante Uno', 'Fabricante palas Padel'), "
                          "('c2', 'Figurante Dos', 'Vending'), "
                          "('c3', 'Figurante Tres', NULL)")
        c.exec_driver_sql("INSERT INTO historico_familias (codigo, clase, clase_por) "
                          "VALUES ('019', 'recambio', 'bart')")
        c.exec_driver_sql("INSERT INTO historico_articulos_clase (codart, clase, por) "
                          "VALUES ('A1', 'maquina', 'bart')")

    def foto():
        with engine.connect() as c:
            return (sorted(c.exec_driver_sql("SELECT id, sector FROM companies").all()),
                    c.exec_driver_sql("SELECT codigo, clase FROM historico_familias").all(),
                    c.exec_driver_sql("SELECT codart, clase FROM historico_articulos_clase").all())

    antes = foto()
    command.upgrade(cfg, "20261101_0165")
    sectores, familias, marcas = foto()
    assert sectores == [("c1", "Industria y fabricación"), ("c2", "Vending"), ("c3", None)]
    assert familias == antes[1] and marcas == antes[2]
    tablas = set(inspect(engine).get_table_names())
    assert {"crm_empresa_erp", "crm_empresa_gama", "crm_empresa_modelo", "crm_empresa_compra",
            "crm_resumen_estado", "crm_etiqueta_auto"} <= tablas
    command.downgrade(cfg, "20261101_0164")
    assert "crm_empresa_erp" not in set(inspect(engine).get_table_names())


def test_0166_dominios_compartidos_sembrados_y_sin_despegar_nada(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261101_0165")
    command.upgrade(cfg, "20261101_0166")
    with engine.connect() as c:
        filas = dict(c.exec_driver_sql("SELECT dominio, tipo FROM dominios_compartidos").all())
        apuntes = c.exec_driver_sql("SELECT COUNT(*) FROM crm_despegue_dominio").scalar()
    # La lista que vivía en el código (gmail.com…) y la semilla de las consejerías.
    assert filas["gmail.com"] == "correo" and filas["xtec.cat"] == "compartido"
    assert sum(1 for t in filas.values() if t == "compartido") == 11
    from app.models.dominios import SEMILLA_COMPARTIDOS, SEMILLA_CORREO

    assert set(filas) == set(SEMILLA_CORREO) | set(SEMILLA_COMPARTIDOS)
    assert apuntes == 0                         # la migración no despega nada
    command.downgrade(cfg, "20261101_0165")
    tablas = set(inspect(engine).get_table_names())
    assert "dominios_compartidos" not in tablas and "crm_despegue_dominio" not in tablas


def test_0167_anade_los_aprobados_sin_pisar_y_baja_solo_los_suyos(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    command.upgrade(cfg, "20261101_0166")
    with engine.begin() as c:
        # Uno que alguien ya añadió a mano en la pantalla: no se pisa.
        c.exec_driver_sql("INSERT INTO dominios_compartidos (dominio, tipo, nota, por) VALUES "
                          "('gva.es', 'compartido', 'puesto a mano', 'admin@ejemplo.test')")
    command.upgrade(cfg, "20261101_0167")
    with engine.connect() as c:
        filas = {d: (t, por) for d, t, por in c.exec_driver_sql(
            "SELECT dominio, tipo, por FROM dominios_compartidos")}
    assert filas["gva.es"] == ("compartido", "admin@ejemplo.test")
    assert filas["outlook.fr"][0] == "correo" and filas["ub.edu"][0] == "compartido"
    assert filas["bomedia.net"][0] == "compartido"
    assert "flux3dp.com" not in filas and "casadellibro.com" not in filas
    # 0166 (46) + el de a mano + 33 de correo + 15 de administración (gva.es
    # ya estaba) + bomedia.net.
    assert len(filas) == 46 + 1 + 33 + 15 + 1
    command.downgrade(cfg, "20261101_0166")
    with engine.connect() as c:
        quedan = {r[0] for r in c.exec_driver_sql("SELECT dominio FROM dominios_compartidos")}
    assert "gva.es" in quedan and "outlook.fr" not in quedan and len(quedan) == 47


def test_0168_uno_por_empresa_solo_esquema(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    command.stamp(cfg, "20261030_0152")
    with engine.begin() as c:
        # La base de este test arranca sellada en la 0152: tablas mínimas.
        c.exec_driver_sql("CREATE TABLE contacts (id VARCHAR(36) PRIMARY KEY)")
        c.exec_driver_sql("CREATE TABLE segments (id VARCHAR(36) PRIMARY KEY)")
        c.exec_driver_sql("INSERT INTO contacts (id) VALUES ('c1')")
        c.exec_driver_sql("INSERT INTO segments (id) VALUES ('s1')")
    command.upgrade(cfg, "20261101_0167")
    command.upgrade(cfg, "20261101_0168")
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT contacto_principal FROM contacts").scalar() in (0, False)
        fila = c.exec_driver_sql("SELECT uno_por_empresa, orden_contacto FROM segments").one()
        assert fila[0] in (0, False) and fila[1] is None
    command.downgrade(cfg, "20261101_0167")
    cols = {col["name"] for col in inspect(engine).get_columns("segments")}
    assert "uno_por_empresa" not in cols


def test_0169_analisis_parque_solo_esquema(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    # Solo la 0169: se sella en la 0168 con la tabla a la que apunta.
    command.stamp(cfg, "20261101_0168")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE companies (id VARCHAR(36) PRIMARY KEY)")
    command.upgrade(cfg, "20261101_0169")
    cols = {col["name"] for col in inspect(engine).get_columns("crm_analisis_empresa")}
    assert {"company_id", "huella", "generado_at", "modelo", "contenido_json",
            "coste_usd"} <= cols
    command.downgrade(cfg, "20261101_0168")
    assert "crm_analisis_empresa" not in inspect(engine).get_table_names()
