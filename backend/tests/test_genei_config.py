"""Config de Genei (couriers preferidos por país, bulto por defecto) y el
comparador de agencias (`choose_agencies`)."""
from __future__ import annotations

from app.erp.integrations.genei.config import (
    AgencyPrice,
    DefaultPackage,
    GeneiConfig,
    choose_agencies,
    normalize_prices,
)

# --- config -----------------------------------------------------------------


def test_config_roundtrip_and_preferred_for():
    cfg = GeneiConfig(
        preferred_couriers={"ES": ["Correos", "GLS"], "fr": ["Chronopost"]},
        default_package=DefaultPackage(weight=2.0, height=10, width=15, length=20),
    )
    restored = GeneiConfig.from_json(cfg.to_json())
    # Los países se normalizan a mayúsculas.
    assert restored.preferred_for("es") == ["Correos", "GLS"]
    assert restored.preferred_for("FR") == ["Chronopost"]
    assert restored.preferred_for("DE") == []
    assert restored.preferred_for(None) == []
    assert restored.default_package.weight == 2.0


def test_config_defaults_when_missing_or_bad():
    assert GeneiConfig.from_json(None).preferred_couriers == {}
    assert GeneiConfig.from_json("not-json").default_package.weight == 1.0
    assert GeneiConfig.from_json("[]").preferred_couriers == {}


def test_default_package_tolerant_and_as_package():
    pkg = DefaultPackage.from_dict({"weight": "0.5", "height": 2, "width": 10, "length": 10})
    assert pkg.weight == 0.5
    assert pkg.as_package() == {"weight": 0.5, "height": 2.0, "width": 10.0, "length": 10.0}
    # Valores no positivos o basura → default.
    bad = DefaultPackage.from_dict({"weight": 0, "height": "x"})
    assert bad.weight == 1.0 and bad.height == 20.0


# --- normalización ----------------------------------------------------------


def test_normalize_prices_tolerant_naming():
    rows = [
        {"agencyId": 10, "nombre_agencia": "Correos Premium Domicilio", "precio": "4,50"},
        {"id_agencia": 11, "name": "GLS Oficina", "importe": 3.9},
        {"foo": "bar"},  # sin id → se descarta
    ]
    agencies = normalize_prices(rows)
    assert [a.agency_id for a in agencies] == ["10", "11"]
    assert agencies[0].price == 4.5
    assert agencies[0].is_home_delivery is True
    # «Oficina» en el nombre → no es entrega a domicilio.
    assert agencies[1].is_home_delivery is False


def test_normalize_prices_real_genei_shape():
    # Forma real de /agencies/prices (verificada en vivo): id_agencia, importe,
    # nombre_completo_agencia y domicilio_domicilio (1 = a domicilio).
    rows = [
        {"id_agencia": "2", "importe": 4.23, "nombre_completo_agencia": "Correos Dom-Dom",
         "domicilio_domicilio": 1},
        {"id_agencia": "9", "importe": 3.5, "nombre_completo_agencia": "GLS Punto",
         "domicilio_domicilio": 0},
    ]
    agencies = normalize_prices(rows)
    assert agencies[0].agency_id == "2"
    assert agencies[0].name == "Correos Dom-Dom"
    assert agencies[0].price == 4.23
    assert agencies[0].is_home_delivery is True     # domicilio_domicilio=1
    assert agencies[1].is_home_delivery is False     # domicilio_domicilio=0


# --- comparador -------------------------------------------------------------


def _rows() -> list[dict]:
    return [
        {"agencyId": 1, "name": "Correos Domicilio", "price": 6.0},
        {"agencyId": 2, "name": "GLS Domicilio", "price": 4.5},
        {"agencyId": 3, "name": "SEUR Domicilio", "price": 3.0},
        {"agencyId": 4, "name": "GLS Oficina", "price": 2.0},   # más barata pero oficina
    ]


def test_choose_prefers_cheapest_preferred_home_delivery():
    # Preferidos ES: Correos, GLS. La más barata a domicilio entre preferidas es
    # GLS (4.5), no SEUR (3.0, no preferida) ni GLS Oficina (2.0, no domicilio).
    choice = choose_agencies(_rows(), preferred=["Correos", "GLS"])
    assert choice.default is not None
    assert choice.default.agency_id == "2"
    # El comparador ofrece todas las de domicilio (3) para poder cambiar.
    assert {a.agency_id for a in choice.home_options} == {"1", "2", "3"}
    # Y todas (incl. oficina) por si el operario la pide a propósito.
    assert len(choice.all_options) == 4


def test_choose_falls_back_to_cheapest_home_when_no_preferred():
    # Ningún preferido factible → la más barata a domicilio (SEUR 3.0), NO la de
    # oficina (2.0).
    choice = choose_agencies(_rows(), preferred=["Chronopost", "DHL"])
    assert choice.default is not None
    assert choice.default.agency_id == "3"


def test_choose_empty_prices():
    choice = choose_agencies([], preferred=["Correos"])
    assert choice.default is None
    assert choice.home_options == [] and choice.all_options == []


def test_choose_respects_preference_order_as_tiebreak():
    rows = [
        {"agencyId": 1, "name": "Correos Domicilio", "price": 5.0},
        {"agencyId": 2, "name": "GLS Domicilio", "price": 5.0},
    ]
    # Mismo precio: gana el primero en la lista de preferencia (GLS antes).
    choice = choose_agencies(rows, preferred=["GLS", "Correos"])
    assert choice.default.agency_id == "2"


def test_agency_price_from_raw_requires_id():
    assert AgencyPrice.from_raw({"name": "x"}) is None
    assert AgencyPrice.from_raw({"agencyId": 7}).agency_id == "7"
