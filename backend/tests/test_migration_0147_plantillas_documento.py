"""Migración 20261027_0147 — las plantillas de oferta pasan a ser de documento.

La plantilla que ya existía (una, con sus diez líneas y sus artículos de
FACTUSOL) tiene que seguir ahí, con el mismo id, y la oferta que la usó tiene
que seguir apuntándola. Se ejecuta SOLO 0146 → 0147 (y vuelta), como en
`test_migration_0124_cuadre`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
AHORA = "2026-10-12 10:00:00"


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'plantillas.db'}"
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


def _sembrar_0146(engine) -> None:
    """La plantilla real de antes de la migración: diez líneas, artículos de
    FACTUSOL, copiada del presupuesto 2-40; y una oferta que la usó."""
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO ofertas_plantillas (id, nombre, descripcion, activa, origen_serie, "
            "origen_codpre, created_at, updated_at) VALUES ('p-1', 'Kit UV escuelas', "
            "'La de siempre', 1, 2, 40, :t, :t)"), {"t": AHORA})
        for i in range(1, 11):
            c.execute(text(
                "INSERT INTO ofertas_plantillas_lineas (id, plantilla_id, posicion, codart, sku, "
                "descripcion, cantidad, precio, descuento_pct, iva_pct) VALUES "
                "(:id, 'p-1', :pos, :codart, :sku, :des, 1, :precio, 0, 21)"),
                {"id": f"l-{i}", "pos": i, "codart": f"0000{i}", "sku": f"UV-{i}",
                 "des": f"Línea {i}", "precio": 10 * i})
        c.execute(text(
            "INSERT INTO ofertas (id, numero, anio, secuencia, plantilla_id, serie, idioma, "
            "con_iva, fecha, validez_dias, valida_hasta, estado, base, iva_pct, iva, total, "
            "version, created_at, updated_at) VALUES ('o-1', 'OF-2026-0001', 2026, 1, 'p-1', "
            "2, 'es', 1, '2026-10-12', 30, '2026-11-11', 'borrador', 0, 21, 0, 0, 1, :t, :t)"),
            {"t": AHORA})


def test_la_plantilla_existente_sigue_con_sus_diez_lineas_y_vuelve_al_bajar(alembic_setup):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    # Las tablas a las que apuntan las claves ajenas (el modo «batch» de
    # SQLite las refleja al recrear `plantillas_documento`).
    with engine.begin() as c:
        for tabla in ("users", "contacts", "companies", "orders"):
            c.execute(text(f"CREATE TABLE {tabla} (id VARCHAR(36) PRIMARY KEY)"))
    command.stamp(cfg, "20261012_0145")
    command.upgrade(cfg, "20261012_0146")
    _sembrar_0146(engine)

    command.upgrade(cfg, "20261027_0147")
    insp = inspect(engine)
    tablas = set(insp.get_table_names())
    assert {"plantillas_documento", "plantillas_documento_lineas",
            "plantillas_documento_carpetas", "plantillas_documento_carpetas_acceso",
            "plantillas_documento_preferencias", "plantillas_documento_etiquetas"} <= tablas
    assert not {"ofertas_plantillas", "ofertas_plantillas_lineas"} & tablas
    with engine.connect() as c:
        p = c.execute(text(
            "SELECT id, nombre, idioma, carpeta_id, origen_tipo, origen_serie, origen_codigo, "
            "origen_numero, activa FROM plantillas_documento")).mappings().all()
        assert len(p) == 1
        assert dict(p[0]) == {
            "id": "p-1", "nombre": "Kit UV escuelas", "idioma": "es", "carpeta_id": None,
            "origen_tipo": "presupuesto", "origen_serie": 2, "origen_codigo": 40,
            "origen_numero": "2-000040", "activa": 1,
        }
        lineas = c.execute(text(
            "SELECT posicion, codart, sku, descripcion FROM plantillas_documento_lineas "
            "WHERE plantilla_id = 'p-1' ORDER BY posicion")).all()
        assert len(lineas) == 10
        assert [ln.codart for ln in lineas] == [f"0000{i}" for i in range(1, 11)]
        assert lineas[0].sku == "UV-1"
        # La oferta que la usó sigue apuntándola.
        assert c.execute(text("SELECT plantilla_id FROM ofertas WHERE id='o-1'")).scalar() == "p-1"
    columnas = {col["name"] for col in insp.get_columns("plantillas_documento")}
    assert "origen_codpre" not in columnas
    assert {idx["name"] for idx in insp.get_indexes("plantillas_documento_lineas")} >= {
        "ix_plantillas_documento_lineas_plantilla_id"}

    command.downgrade(cfg, "20261012_0146")
    insp = inspect(engine)
    tablas = set(insp.get_table_names())
    assert {"ofertas_plantillas", "ofertas_plantillas_lineas"} <= tablas
    assert "plantillas_documento_carpetas" not in tablas
    with engine.connect() as c:
        fila = c.execute(text(
            "SELECT id, origen_serie, origen_codpre FROM ofertas_plantillas")).one()
        assert tuple(fila) == ("p-1", 2, 40)
        assert c.execute(text("SELECT count(*) FROM ofertas_plantillas_lineas")).scalar() == 10
