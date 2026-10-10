"""Migración 20261010_0134 — el catálogo de intereses y varios intereses por
lead.

Se ejecuta SOLO esta revisión sobre tablas mínimas (como
`test_migration_0132_leads`): la migración añade columnas con
`batch_alter_table` y migra datos de `lead_classifications`, `contacts` y el
mapa de plantillas de `erp_settings`, así que esas tablas tienen que existir.
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
from app.services.leads.intereses import DE_PARTIDA

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FECHA = "2026-10-10 00:00:00"


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'intereses.db'}"
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


def _preparar(engine) -> None:
    with engine.begin() as c:
        c.execute(text(
            "CREATE TABLE contacts (id VARCHAR(36) PRIMARY KEY, lead_interest VARCHAR(40))"
        ))
        c.execute(text(
            "CREATE TABLE lead_classifications (id VARCHAR(36) PRIMARY KEY, "
            "contact_id VARCHAR(36), source VARCHAR(16), source_ref VARCHAR(64), "
            "interest VARCHAR(40), corrected_interest VARCHAR(40), reason VARCHAR(500), "
            "language_mismatch BOOLEAN, is_spam BOOLEAN, status VARCHAR(24), "
            "created_at DATETIME, updated_at DATETIME)"
        ))
        c.execute(text(
            "CREATE TABLE erp_settings (id VARCHAR(36) PRIMARY KEY, factusol_series_json TEXT)"
        ))
        c.execute(text(
            "CREATE TABLE email_templates (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200), "
            "created_at DATETIME)"
        ))
        c.execute(text("INSERT INTO contacts VALUES ('c1', 'uv_gran_formato')"))
        c.execute(text("INSERT INTO contacts VALUES ('c2', 'servicio_tecnico')"))
        c.execute(text("INSERT INTO contacts VALUES ('c3', NULL)"))
        filas = [
            ("l1", "uv_pequeno_mediano", None, "el texto dice UV"),
            # Motivo al tope de los 500: la nota no se recorta, se recorta el motivo.
            ("l8", "uv_pequeno_mediano", None, "x" * 500),
            ("l2", "uv_gran_formato", None, "gran formato"),
            ("l3", "consumibles", None, "tintas"),
            ("l4", "repuestos", None, "cabezal"),
            ("l5", "servicio_tecnico", "laser_cnc", "avería"),
            ("l6", "vending", None, "vending"),
            ("l7", "otro", None, "factura"),
        ]
        for fila_id, interes, corregido, motivo in filas:
            c.execute(
                text(
                    "INSERT INTO lead_classifications (id, contact_id, source, source_ref, "
                    "interest, corrected_interest, reason, language_mismatch, is_spam, status, "
                    "created_at, updated_at) VALUES (:id, 'c1', 'web_form', :ref, :interes, "
                    ":corregido, :motivo, 0, 0, 'clasificado', :f, :f)"
                ),
                {"id": fila_id, "ref": f"envio-{fila_id}", "interes": interes,
                 "corregido": corregido, "motivo": motivo, "f": FECHA},
            )
        c.execute(
            text("INSERT INTO erp_settings VALUES ('singleton', :j)"),
            {"j": json.dumps({
                "lead_response": {
                    "activo": True,
                    "mapa": {"uv_pequeno_mediano:es": "tpl-uv-es", "laser_cnc:fr": "",
                             "vending:en": "tpl-v-en"},
                },
                "otra_clave": {"se": "conserva"},
            })},
        )
        for tpl_id, nombre in (("tpl-g-de", "Lead · UV gran formato (DE)"),
                               ("tpl-l-es", "Lead · Láser y CNC (ES)"),
                               ("tpl-uv-pt", "Lead · UV pequeño-mediano (PT)"),
                               ("tpl-vend-es", "Lead · Vending (ES)")):
            c.execute(
                text("INSERT INTO email_templates VALUES (:id, :n, :f)"),
                {"id": tpl_id, "n": nombre, "f": FECHA},
            )


def test_subir_siembra_el_catalogo_migra_los_codigos_y_el_mapa_y_bajar_lo_deshace(
    alembic_setup,
):
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    _preparar(engine)
    command.stamp(cfg, "20261010_0133")
    command.upgrade(cfg, "20261010_0134")

    insp = inspect(engine)
    assert "lead_interests" in insp.get_table_names()
    columnas = {c["name"] for c in insp.get_columns("lead_classifications")}
    assert {"interests_json", "corrected_interests_json"} <= columnas

    with engine.connect() as c:
        catalogo = c.execute(text(
            "SELECT code, label, is_commercial, position, is_active FROM lead_interests "
            "ORDER BY position"
        )).fetchall()
        clasificaciones = {
            r[0]: r[1:] for r in c.execute(text(
                "SELECT id, interest, corrected_interest, reason, interests_json, "
                "corrected_interests_json FROM lead_classifications"
            )).fetchall()
        }
        contactos = dict(c.execute(text("SELECT id, lead_interest FROM contacts")).fetchall())
        ajustes = json.loads(c.execute(text(
            "SELECT factusol_series_json FROM erp_settings WHERE id = 'singleton'"
        )).scalar_one())

    # El catálogo: los 13 de partida, en su orden, todos activos.
    assert [r[0] for r in catalogo] == [d["codigo"] for d in DE_PARTIDA]
    assert [r[1] for r in catalogo] == [d["etiqueta"] for d in DE_PARTIDA]
    assert [bool(r[2]) for r in catalogo] == [d["comercial"] for d in DE_PARTIDA]
    assert all(bool(r[4]) for r in catalogo)

    # Los códigos: renombrados, con su lista de uno; ninguna sin interés.
    assert clasificaciones["l1"][0] == "uv_mediano"
    assert "UV pequeño-mediano" in clasificaciones["l1"][2]
    assert clasificaciones["l1"][2].startswith("el texto dice UV")
    assert clasificaciones["l8"][0] == "uv_mediano"
    assert clasificaciones["l8"][2].endswith("talla por confirmar (pequeño o mediano).")
    assert len(clasificaciones["l8"][2]) <= 500 and "…" in clasificaciones["l8"][2]
    assert clasificaciones["l2"][0] == "uv_grande"
    assert clasificaciones["l3"][0] == "tienda" and clasificaciones["l4"][0] == "tienda"
    assert clasificaciones["l5"][0] == "soporte_postventa"
    assert clasificaciones["l5"][1] == "corte_laser"
    assert json.loads(clasificaciones["l5"][4]) == ["corte_laser"]
    assert clasificaciones["l6"][0] == "vending" and clasificaciones["l7"][0] == "otro"
    for fila_id, datos in clasificaciones.items():
        assert json.loads(datos[3]) == [datos[0]], fila_id
    # El motivo de los demás no se toca.
    assert clasificaciones["l2"][2] == "gran formato"
    assert contactos == {"c1": "uv_grande", "c2": "soporte_postventa", "c3": None}

    # El mapa: códigos nuevos (UV pequeño-mediano vale para los dos tamaños),
    # el «sin plantilla» a propósito se conserva, y lo que se resolvía por el
    # nombre antiguo queda escrito para los códigos nuevos.
    mapa = ajustes["lead_response"]["mapa"]
    assert mapa["uv_pequeno:es"] == "tpl-uv-es" and mapa["uv_mediano:es"] == "tpl-uv-es"
    assert mapa["corte_laser:fr"] == ""
    assert mapa["vending:en"] == "tpl-v-en"
    assert mapa["uv_grande:de"] == "tpl-g-de"
    # «Láser y CNC» cubría corte, grabado y CNC: queda escrita para los tres.
    assert mapa["corte_laser:es"] == mapa["grabado_laser:es"] == mapa["cnc:es"] == "tpl-l-es"
    assert mapa["grabado_laser:fr"] == "" and mapa["cnc:fr"] == ""
    assert mapa["uv_pequeno:pt"] == "tpl-uv-pt" and mapa["uv_mediano:pt"] == "tpl-uv-pt"
    assert "uv_pequeno_mediano:es" not in mapa and "laser_cnc:fr" not in mapa
    # Vending y Distribución no cambian de nombre: siguen por nombre, no se
    # escriben en el mapa.
    assert "vending:es" not in mapa
    assert ajustes["lead_response"]["activo"] is True
    assert ajustes["otra_clave"] == {"se": "conserva"}

    command.downgrade(cfg, "20261010_0133")
    insp = inspect(engine)
    assert "lead_interests" not in insp.get_table_names()
    columnas = {c["name"] for c in insp.get_columns("lead_classifications")}
    assert not {"interests_json", "corrected_interests_json"} & columnas
    with engine.connect() as c:
        vueltos = dict(c.execute(text(
            "SELECT id, interest FROM lead_classifications"
        )).fetchall())
        ajustes = json.loads(c.execute(text(
            "SELECT factusol_series_json FROM erp_settings WHERE id = 'singleton'"
        )).scalar_one())
    assert vueltos["l2"] == "uv_gran_formato" and vueltos["l5"] == "servicio_tecnico"
    assert vueltos["l3"] == "consumibles" and vueltos["l1"] == "uv_pequeno_mediano"
    assert ajustes["lead_response"]["mapa"]["uv_pequeno_mediano:es"] == "tpl-uv-es"


def test_subir_con_tablas_vacias_no_falla(alembic_setup):
    """Una base recién creada (el CI): sin clasificaciones, sin ajustes,
    sin plantillas. Siembra el catálogo y nada más."""
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE contacts (id VARCHAR(36) PRIMARY KEY)"))
        c.execute(text(
            "CREATE TABLE lead_classifications (id VARCHAR(36) PRIMARY KEY, "
            "interest VARCHAR(40), corrected_interest VARCHAR(40), reason VARCHAR(500), "
            "created_at DATETIME, updated_at DATETIME)"
        ))
    command.stamp(cfg, "20261010_0133")
    command.upgrade(cfg, "20261010_0134")
    with engine.connect() as c:
        total = c.execute(text("SELECT COUNT(*) FROM lead_interests")).scalar_one()
    assert total == len(DE_PARTIDA)
