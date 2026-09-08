"""ERP-F5 — catálogos de FACTUSOL mal leídos.

1. Formas de pago: F_FOP está VACÍA en producción; el catálogo real es F_FPA.
2. CPALCO/CPACOB NO es la forma de pago: es la CONTRAPARTIDA de cobro
   (catálogo configurable; la tabla de FACTUSOL no se ha localizado).
3. Las cuentas bancarias de la conciliación se enlazan con su contrapartida.
Además: serie 4 = Lambert, y catálogos F_AGE / F_TRN / F_ALM / F_FAM.
"""
from __future__ import annotations

import logging
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.contrapartidas import (
    DEFAULT_CONTRAPARTIDAS,
    paypal_contrapartida_for_store,
    resolve_contrapartida,
)
from app.integrations.factusol.catalogs import (
    CATALOGS,
    load_catalog,
    names_index,
    normalize_code,
    payment_method_names,
    resolve_name,
)
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_documents import FakeClient

# --- tablas reales (muestra) --------------------------------------------------------

F_FPA = [
    {"CODFPA": "000", "DESFPA": "SC Sin cargo", "VENFPA": 0},
    {"CODFPA": "001", "DESFPA": "Cash", "VENFPA": 1, "DIA1FPA": 0},
    {"CODFPA": "002", "DESFPA": "Transferencia", "VENFPA": 1, "DIA1FPA": 0},
    {"CODFPA": "005", "DESFPA": "30 DÍAS: TRANSFERENCIA - SIN DOMICILIACIÓN",
     "VENFPA": 1, "DIA1FPA": 30},
    {"CODFPA": "007", "DESFPA": "60 días 2 pagos", "VENFPA": 2, "DIA1FPA": 30, "DIA2FPA": 60},
    {"CODFPA": "011", "DESFPA": "Prepago", "VENFPA": 1, "DIA1FPA": 0},
]
F_AGE = [
    {"CODAGE": "1", "NOMAGE": "BART"}, {"CODAGE": "2", "NOMAGE": "ROSA"},
    {"CODAGE": "10", "NOMAGE": "Brice Leroux"},
]
F_TRN = [
    {"CODTRN": str(i + 1), "NOMTRN": n}
    for i, n in enumerate(["MRW", "DSV", "DHL", "MBE", "SELF", "TNT", "UPS"])
]
F_ALM = [{"CODALM": "GEN", "NOMALM": "SAT", "DOMALM": "Mossen Antoni Solanas 65",
          "POBALM": "Sant Boi de Llobregat"}]
F_FAM = [{"CODFAM": "001", "DESFAM": "Impresoras"}, {"CODFAM": "002", "DESFAM": "Tintas"}]


def _fac(serie, codigo, total, *, fop=None, estfac="0", cliente="Cliente"):
    row = {
        "TIPFAC": str(serie), "CODFAC": codigo, "TOTFAC": total, "ESTFAC": estfac,
        "CLIFAC": 1, "CNOFAC": cliente, "FECFAC": "2026-08-01T00:00:00",
        "REFFAC": f"BOP-{codigo}",
    }
    if fop is not None:
        row["FOPFAC"] = fop
    return row


def _cobro(serie, codigo, importe, cpalco):
    return {
        "TFALCO": str(serie), "CFALCO": codigo, "LINLCO": 1,
        "FECLCO": "2026-08-10T00:00:00", "IMPLCO": importe,
        "CPALCO": cpalco, "CPTLCO": f"COBRO FACTURA Nº: {serie} - {codigo}",
    }


def _fake(**over) -> FakeClient:
    tables = {
        "F_FAC": [], "F_LFA": [], "F_ALB": [], "F_LAL": [], "F_LCO": [],
        "F_FOP": [],  # ← vacía, como en producción
        "F_FPA": F_FPA, "F_AGE": F_AGE, "F_TRN": F_TRN, "F_ALM": F_ALM, "F_FAM": F_FAM,
    }
    tables.update(over)
    return FakeClient(tables)


def _patched(fake: FakeClient):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


# --- fixtures ----------------------------------------------------------------------


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --- Error 1: formas de pago desde F_FPA ----------------------------------------------


def test_payment_methods_read_from_fpa_not_fop(http, caplog) -> None:
    fake = _fake()
    with _patched(fake):
        r = http.get("/api/erp/factusol/formas-pago", headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "F_FPA"
    by_code = {it["codigo"]: it for it in body["items"]}
    assert by_code["002"]["nombre"] == "Transferencia"
    assert by_code["007"] == {"codigo": "007", "nombre": "60 días 2 pagos",
                              "vencimientos": 2, "dias": [30, 60]}
    assert len(body["items"]) == len(F_FPA)
    # Se lee F_FPA (con 1=1, sin filtrar por columnas) y NUNCA F_FOP.
    assert ("F_FPA", "1=1") in fake.calls
    assert not any(t == "F_FOP" for t, _ in fake.calls)
    # Y el índice de nombres que usan detalle/PDF/email también sale de F_FPA.
    names = payment_method_names(_fake(), ejercicio="2026")
    assert names["002"] == "Transferencia"
    # Gotcha nº1: si la tabla no trae la columna de código, se avisa y NO se
    # inventa nada (mejor vacío que un nombre de otra columna).
    wrong = _fake(F_FPA=[{"CODFOP": "002", "DESFOP": "Transferencia"}])
    with caplog.at_level(logging.WARNING):
        # force_refresh: la cache es por (catálogo, ejercicio), no por cliente.
        assert load_catalog(wrong, "formas_pago", ejercicio="2026", force_refresh=True) == []
    assert any("CODFPA" in rec.message for rec in caplog.records)
    assert CATALOGS["formas_pago"].table == "F_FPA"


def test_payment_method_code_normalization() -> None:
    assert normalize_code("011") == "11"
    assert normalize_code("11") == "11"
    assert normalize_code(11) == "11"
    assert normalize_code("11.0") == "11"
    assert normalize_code("000") == "0"
    assert normalize_code(" 002 ") == "2"
    assert normalize_code("GEN") == "GEN"
    assert normalize_code(None) == ""
    names = names_index([{"codigo": "011", "nombre": "Prepago"},
                         {"codigo": "002", "nombre": "Transferencia"}])
    assert resolve_name(names, "11") == resolve_name(names, "011") == "Prepago"
    assert resolve_name(names, 11) == "Prepago"
    assert resolve_name(names, "2") == resolve_name(names, "002") == "Transferencia"
    assert resolve_name(names, "") is None
    assert resolve_name(names, None) is None
    assert resolve_name(names, "099") is None


def test_invoice_detail_shows_payment_method_name_not_code(http) -> None:
    # Regresión del síntoma de Bart: «Forma de pago: Código 002».
    fake = _fake(F_FAC=[_fac(1, 260720, 3623.95, fop="002"),
                        _fac(1, 260721, 100.0, fop="11")])   # sin ceros a la izquierda
    with _patched(fake):
        r1 = http.get("/api/erp/factusol/documents/facturas/1/260720",
                      headers=auth_headers(http, "user"))
        r2 = http.get("/api/erp/factusol/documents/facturas/1/260721",
                      headers=auth_headers(http, "user"))
    assert r1.status_code == 200, r1.text
    assert r1.json()["forma_pago"] == "002"
    assert r1.json()["forma_pago_nombre"] == "Transferencia"
    assert r2.json()["forma_pago"] == "11"
    assert r2.json()["forma_pago_nombre"] == "Prepago"
    assert not any(t == "F_FOP" for t, _ in fake.calls)


# --- Error 2: CPALCO = contrapartida ----------------------------------------------------


def test_collection_shows_contrapartida_not_payment_method(http) -> None:
    fake = _fake(
        F_FAC=[_fac(5, 260004, 420.74, estfac="1"), _fac(1, 260004, 100.0, estfac="2")],
        F_LCO=[_cobro(5, 260004, 408.48, 8), _cobro(1, 260004, 100.0, "6")],
    )
    with _patched(fake):
        r5 = http.get("/api/erp/factusol/documents/facturas/5/260004",
                      headers=auth_headers(http, "user"))
        r1 = http.get("/api/erp/factusol/documents/facturas/1/260004",
                      headers=auth_headers(http, "user"))
    assert r5.status_code == 200, r5.text
    cobro = r5.json()["cobros"][0]
    assert cobro["contrapartida"] == "8"
    assert cobro["contrapartida_nombre"] == "Streamtec Sabadell"   # serie 5 = Streamtec
    assert "forma_pago" not in cobro
    assert "forma_pago_nombre" not in cobro
    assert r1.json()["cobros"][0]["contrapartida_nombre"] == "Bomedia Sabadell"  # serie 1
    # La forma de pago de la FACTURA sigue siendo otra cosa (y sale de F_FPA).
    assert r5.json()["forma_pago_nombre"] is None


def test_contrapartida_catalog_defaults_and_editable(http, session_factory) -> None:
    r = http.get("/api/erp/settings", headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    items = r.json()["contrapartidas"]
    assert [(int(i["codigo"]), i["nombre"]) for i in items] == DEFAULT_CONTRAPARTIDAS
    assert len(items) == 14
    r = http.get("/api/erp/catalogs/contrapartidas", headers=auth_headers(http, "user"))
    assert r.status_code == 200
    assert r.json()["source"] == "erp_settings"
    assert r.json()["items"] == items
    # Editable (admin): renombrar la 7, añadir la 15.
    edited = [dict(i) for i in items]
    edited[6]["nombre"] = "Streamtec Open Bank"
    edited.append({"codigo": "15", "nombre": "Stripe"})
    edited.append({"codigo": "", "nombre": ""})          # fila vacía del formulario
    r = http.patch("/api/erp/settings", json={"contrapartidas": edited},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    got = {i["codigo"]: i["nombre"] for i in r.json()["contrapartidas"]}
    assert got["7"] == "Streamtec Open Bank"
    assert got["15"] == "Stripe"
    assert len(got) == 15
    with session_factory() as s:
        assert resolve_contrapartida(s, "07") == "Streamtec Open Bank"
        assert resolve_contrapartida(s, 15) == "Stripe"
    # Persistido: el catálogo público lo refleja.
    r = http.get("/api/erp/catalogs/contrapartidas", headers=auth_headers(http, "user"))
    assert {i["codigo"]: i["nombre"] for i in r.json()["items"]} == got
    # Validación: código repetido / no numérico → 400; no admin → 403.
    r = http.patch("/api/erp/settings",
                   json={"contrapartidas": [{"codigo": "1", "nombre": "a"},
                                            {"codigo": "01", "nombre": "b"}]},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 400
    r = http.patch("/api/erp/settings", json={"contrapartidas": [{"codigo": "x", "nombre": "a"}]},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 400
    r = http.patch("/api/erp/settings", json={"contrapartidas": edited},
                   headers=auth_headers(http, "pedidos"))
    assert r.status_code == 403
    # El catálogo documenta dónde vive cada cosa (y que esta no tiene tabla).
    r = http.get("/api/erp/catalogs", headers=auth_headers(http, "user"))
    by_name = {i["name"]: i for i in r.json()["items"]}
    assert by_name["contrapartidas"]["table"] is None
    assert by_name["formas_pago"]["table"] == "F_FPA"


# --- Error 3: cuentas bancarias enlazadas ----------------------------------------------


def test_bank_account_links_to_contrapartida(http) -> None:
    admin = auth_headers(http, "admin")
    r = http.post("/api/erp/bank/accounts", json={
        "name": "Sabadell Bomedia", "iban": "ES33 0081 0202 1300 0117 1918",
        "bank_name": "Banco Sabadell", "serie": 1, "contrapartida_codigo": "6",
    }, headers=admin)
    assert r.status_code == 201, r.text
    acc = r.json()
    assert acc["contrapartida_codigo"] == "6"
    assert acc["contrapartida_nombre"] == "Bomedia Sabadell"
    # Cambiar el enlace (el código se normaliza: '08' → '8').
    r = http.patch(f"/api/erp/bank/accounts/{acc['id']}", json={"contrapartida_codigo": "08"},
                   headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["contrapartida_codigo"] == "8"
    assert r.json()["contrapartida_nombre"] == "Streamtec Sabadell"
    # El listado lleva el nombre resuelto.
    r = http.get("/api/erp/bank/accounts", headers=auth_headers(http, "user"))
    assert r.json()["items"][0]["contrapartida_nombre"] == "Streamtec Sabadell"
    # Desenlazar.
    r = http.patch(f"/api/erp/bank/accounts/{acc['id']}", json={"contrapartida_codigo": ""},
                   headers=admin)
    assert r.json()["contrapartida_codigo"] is None
    assert r.json()["contrapartida_nombre"] is None
    # Una contrapartida que no está en el catálogo no se guarda a ciegas.
    r = http.post("/api/erp/bank/accounts", json={
        "name": "Belfius MQ", "iban": "BE68 5390 0754 7034", "contrapartida_codigo": "99",
    }, headers=admin)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "account_invalid"
    r = http.patch(f"/api/erp/bank/accounts/{acc['id']}", json={"contrapartida_codigo": "99"},
                   headers=admin)
    assert r.status_code == 409


def test_paypal_contrapartida_by_store(http, session_factory) -> None:
    with session_factory() as s:
        assert paypal_contrapartida_for_store(s, "artisjet") == {
            "codigo": "12", "nombre": "Paypal MQ Europe"}
        assert paypal_contrapartida_for_store(s, "boprint") == {
            "codigo": "14", "nombre": "Paypal Streamtec"}
        assert paypal_contrapartida_for_store(s, "fluxlasers")["codigo"] == "14"
        assert paypal_contrapartida_for_store(s, "flux")["codigo"] == "14"   # slug Woo
        assert paypal_contrapartida_for_store(s, "ArtisJet")["codigo"] == "12"
        assert paypal_contrapartida_for_store(s, "otra") is None
        assert paypal_contrapartida_for_store(s, None) is None
    r = http.get("/api/erp/settings", headers=auth_headers(http, "user"))
    assert r.json()["paypal_contrapartidas_by_store"] == {
        "artisjet": "12", "boprint": "14", "fluxlasers": "14"}
    # Configurable: boprint pasa a «11 Paypal Bomedia»; el resto no cambia.
    r = http.patch("/api/erp/settings", json={"paypal_contrapartidas_by_store": {"boprint": "11"}},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    assert r.json()["paypal_contrapartidas_by_store"] == {
        "artisjet": "12", "boprint": "11", "fluxlasers": "14"}
    with session_factory() as s:
        assert paypal_contrapartida_for_store(s, "boprint") == {
            "codigo": "11", "nombre": "Paypal Bomedia"}
    r = http.get("/api/erp/catalogs/contrapartidas", headers=auth_headers(http, "user"))
    assert r.json()["paypal_by_store"]["boprint"] == "11"
    assert [s["key"] for s in r.json()["stores"]] == ["artisjet", "boprint", "fluxlasers"]
    r = http.patch("/api/erp/settings", json={"paypal_contrapartidas_by_store": {"boprint": "x"}},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 400


# --- Hallazgo de paso: serie 4 = Lambert -----------------------------------------------


def test_series_names_include_lambert(http) -> None:
    r = http.get("/api/erp/factusol/series", headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    by_serie = {i["serie"]: i for i in r.json()["items"]}
    assert by_serie[4]["nombre"] == "Lambert"
    assert by_serie[4]["is_known"] is True
    assert {by_serie[1]["nombre"], by_serie[2]["nombre"], by_serie[5]["nombre"]} == {
        "Bomedia", "MQ Europe", "Streamtec"}
    assert by_serie[3]["is_known"] is False           # sigue sin reclamar
    # Lo configurado en /erp/settings sigue mandando sobre el fallback.
    r = http.patch("/api/erp/settings", json={"factusol_series_names": {"4": "Lambert Print"}},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200
    r = http.get("/api/erp/factusol/series", headers=auth_headers(http, "user"))
    assert {i["serie"]: i["nombre"] for i in r.json()["items"]}[4] == "Lambert Print"


# --- Parte D: otros catálogos ----------------------------------------------------------


def test_catalogs_agentes_transportistas_almacen_readable(http) -> None:
    fake = _fake()
    with _patched(fake):
        r = http.get("/api/erp/catalogs", headers=auth_headers(http, "user"))
        assert r.status_code == 200, r.text
        tables = {i["name"]: i["table"] for i in r.json()["items"]}
        assert tables == {"formas_pago": "F_FPA", "agentes": "F_AGE",
                          "transportistas": "F_TRN", "almacenes": "F_ALM",
                          "familias": "F_FAM", "contrapartidas": None}

        agentes = http.get("/api/erp/catalogs/agentes", headers=auth_headers(http, "user")).json()
        assert agentes["table"] == "F_AGE"
        assert agentes["cached"] is False
        assert {a["nombre"] for a in agentes["items"]} == {"BART", "ROSA", "Brice Leroux"}
        # Orden numérico de códigos (1, 2, 10), no alfabético.
        assert [a["codigo"] for a in agentes["items"]] == ["1", "2", "10"]

        trn = http.get("/api/erp/catalogs/transportistas",
                       headers=auth_headers(http, "user")).json()
        assert [t["nombre"] for t in trn["items"]] == [
            "MRW", "DSV", "DHL", "MBE", "SELF", "TNT", "UPS"]

        alm = http.get("/api/erp/catalogs/almacenes", headers=auth_headers(http, "user")).json()
        assert alm["items"] == [{
            "codigo": "GEN", "nombre": "SAT", "direccion": "Mossen Antoni Solanas 65",
            "poblacion": "Sant Boi de Llobregat", "cp": None,
        }]

        fam = http.get("/api/erp/catalogs/familias", headers=auth_headers(http, "user")).json()
        assert fam["count"] == 2

        # Cache: la segunda lectura no vuelve a FACTUSOL; `fresh=true` sí.
        calls_before = len(fake.calls)
        again = http.get("/api/erp/catalogs/agentes", headers=auth_headers(http, "user")).json()
        assert again["cached"] is True
        assert len(fake.calls) == calls_before
        http.get("/api/erp/catalogs/agentes?fresh=true", headers=auth_headers(http, "user"))
        assert len(fake.calls) == calls_before + 1

        r = http.get("/api/erp/catalogs/inventado", headers=auth_headers(http, "user"))
        assert r.status_code == 404
    # Sin sesión → 401.
    assert http.get("/api/erp/catalogs/agentes").status_code == 401
