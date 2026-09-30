"""Migración 20260930_0122 — una sola clave de tienda (`account_id` de Woo).

Los ajustes de producción (30/09/2026) guardaban artisJet como `artisjet` en
las reglas de contrapartida, la PayPal por tienda y los remitentes, pero su
cuenta Woo es `artisjet-europe` (la que ya usaba la serie por tienda): sus
reglas no casaban nunca. La migración renombra las claves, fusiona sin pisar,
es idempotente y deja boprint / fluxlasers intactos. Como en
`test_migration_origin_account_keys`, se montan a mano las dos tablas que toca
y se ejecuta SOLO esta revisión.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND_ROOT / "alembic" / "versions" / "20260930_0122_store_keys_account_id.py"

#: `factusol_series_json` tal cual estaba en producción (lo relevante).
PROD_SERIES = {
    "by_source": {"artisjet-europe": "2", "boprint": "5", "fluxlasers": "5", "manual": "5"},
    "contrapartida_rules": [
        {"tienda": "artisjet", "metodo": "paypal", "coincidencia": "contiene",
         "contrapartida": "12"},
        {"tienda": "boprint", "metodo": "paypal", "coincidencia": "contiene",
         "contrapartida": "14"},
        {"tienda": "fluxlasers", "metodo": "paypal", "coincidencia": "contiene",
         "contrapartida": "14"},
        {"tienda": "artisjet", "metodo": "Carte", "coincidencia": "exacta",
         "contrapartida": "15"},
        {"tienda": "artisjet", "metodo": "mollie_wc_gateway_creditcard",
         "coincidencia": "exacta", "contrapartida": "15"},
    ],
    "paypal_contrapartidas_by_store": {"artisjet": "12", "boprint": "14", "fluxlasers": "14"},
    "store_email_from": {"artisjet": "info@artisjet-printers.eu",
                         "boprint": "pedidos@streamtec.es",
                         "fluxlasers": "pedidos@streamtec.es"},
    "ref_prefix_by_store": {"artisjet": "ART", "fluxlasers": "FLE"},
    "contrapartidas": [{"codigo": "15", "nombre": "Tarjetas Mollie Belfius"},
                       {"codigo": "2", "nombre": "MQ Europe Belfius"},
                       {"codigo": "12", "nombre": "Paypal MQ Europe"},
                       {"codigo": "14", "nombre": "Paypal Streamtec"}],
}
WOO = (("artisjet-europe", "Artisjet Europe"), ("boprint", "Boprint"),
       ("fluxlasers", "Fluxlasers ES"))


def _module():
    spec = importlib.util.spec_from_file_location("m0122", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_renombra_artisjet_fusiona_sin_pisar_y_es_idempotente() -> None:
    mod = _module()
    renames = mod._renames({"artisjet-europe", "boprint", "fluxlasers"})
    assert renames["artisjet"] == "artisjet-europe"
    out = mod.migrate_series(PROD_SERIES, renames)
    assert [r["tienda"] for r in out["contrapartida_rules"]] == [
        "artisjet-europe", "boprint", "fluxlasers", "artisjet-europe", "artisjet-europe"]
    assert out["paypal_contrapartidas_by_store"] == {
        "boprint": "14", "fluxlasers": "14", "artisjet-europe": "12"}
    assert out["store_email_from"]["artisjet-europe"] == "info@artisjet-printers.eu"
    assert "artisjet" not in out["store_email_from"]
    assert out["ref_prefix_by_store"] == {"fluxlasers": "FLE", "artisjet-europe": "ART"}
    # boprint / fluxlasers y lo que no es de tienda, intactos.
    assert out["by_source"] == PROD_SERIES["by_source"]
    assert out["store_email_from"]["boprint"] == "pedidos@streamtec.es"
    assert out["contrapartidas"] == PROD_SERIES["contrapartidas"]
    # Idempotente.
    assert mod.migrate_series(out, renames) == out
    # Si la clave nueva ya existía, se respeta (fusionar sin pisar) y las
    # reglas repetidas tras renombrar quedan una sola vez.
    mixto = mod.migrate_series({
        "store_email_from": {"artisjet": "viejo@x.eu", "artisjet-europe": "nuevo@x.eu"},
        "contrapartida_rules": [
            {"tienda": "artisjet-europe", "metodo": "Carte", "coincidencia": "exacta",
             "contrapartida": "15"},
            {"tienda": "artisjet", "metodo": "Carte", "coincidencia": "exacta",
             "contrapartida": "15"},
        ],
    }, renames)
    assert mixto["store_email_from"] == {"artisjet-europe": "nuevo@x.eu"}
    assert len(mixto["contrapartida_rules"]) == 1
    # Donde `artisjet` SÍ es una cuenta Woo real, no se toca.
    assert "artisjet" not in mod._renames({"artisjet", "boprint"})


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'store_keys.db'}"
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


def test_migracion_en_bd_y_las_reglas_siguen_funcionando(alembic_setup) -> None:
    cfg, db_url = alembic_setup
    engine = create_engine(db_url)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE integration_accounts (id VARCHAR(36) PRIMARY KEY, "
                       "system VARCHAR(32), account_id VARCHAR(64))"))
        c.execute(text("CREATE TABLE erp_settings (id VARCHAR(36) PRIMARY KEY, "
                       "factusol_series_json TEXT)"))
        for i, (slug, _name) in enumerate(WOO):
            c.execute(text("INSERT INTO integration_accounts VALUES (:i, 'woocommerce', :a)"),
                      {"i": f"acc-{i}", "a": slug})
        c.execute(text("INSERT INTO erp_settings VALUES ('singleton', :v)"),
                  {"v": json.dumps(PROD_SERIES)})
    command.stamp(cfg, "20260930_0121")
    command.upgrade(cfg, "20260930_0122")
    with engine.connect() as c:
        migrated = json.loads(c.execute(text(
            "SELECT factusol_series_json FROM erp_settings")).scalar_one())
    assert {r["tienda"] for r in migrated["contrapartida_rules"]} == {
        "artisjet-europe", "boprint", "fluxlasers"}
    assert migrated["store_email_from"]["artisjet-europe"] == "info@artisjet-printers.eu"

    # Con los ajustes migrados, un pedido de la cuenta `artisjet-europe` con
    # «Carte» sugiere la 15 y con PayPal la 12; boprint sigue con la 14.
    from app.db.base import Base
    from app.erp.contrapartidas import suggest_contrapartida_explained
    from app.erp.models import ErpSettings
    from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID
    from app.models.integration_settings import (
        ExternalSystem,
        IntegrationAccount,
        IntegrationMode,
    )

    app_engine = create_engine("sqlite+pysqlite:///:memory:",
                               connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
    Base.metadata.create_all(app_engine)
    with sessionmaker(bind=app_engine)() as s:
        for slug, name in WOO:
            s.add(IntegrationAccount(system=ExternalSystem.WOOCOMMERCE, account_id=slug,
                                     display_name=name, enabled=True,
                                     mode=IntegrationMode.LIVE))
        s.add(ErpSettings(id=ERP_SETTINGS_SINGLETON_ID,
                          factusol_series_json=json.dumps(migrated)))
        s.commit()
        cuenta, motivo = suggest_contrapartida_explained(
            s, serie=2, store="artisjet-europe", payment_method_title="Carte",
            payment_method="mollie_wc_gateway_creditcard",
        )
        assert cuenta["codigo"] == "15"
        assert motivo == "tienda Artisjet Europe · método Carte"
        assert suggest_contrapartida_explained(
            s, serie=2, store="artisjet-europe", payment_method_title="PayPal",
        )[0]["codigo"] == "12"
        assert suggest_contrapartida_explained(
            s, serie=5, store="boprint", payment_method_title="PayPal",
        )[0]["codigo"] == "14"
