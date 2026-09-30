"""ERP · Cobros — contrapartida SUGERIDA por tienda × método de pago de Woo.

Los pedidos web de artisJet Europe pagados con tarjeta vía Mollie llegan con
el método «Carte» (gateway `mollie_wc_gateway_creditcard`): ese dinero entra por
Mollie en Belfius y va a la contrapartida 15 (Tarjetas Mollie Belfius). La
sugerencia generaliza el antiguo mapeo PayPal por tienda a una tabla de reglas
configurable (primera que casa; si ninguna, la cuenta de la serie). Solo es una
sugerencia: el operador la cambia; nunca se sugiere una cuenta que no esté en
el catálogo (el cobro daría 400).
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.crypto import encrypt
from app.db.base import Base
from app.db.session import get_session
from app.erp.bank.matching import propose
from app.erp.contrapartidas import (
    DEFAULT_CONTRAPARTIDAS,
    contrapartida_rules,
    default_contrapartida_rules,
    rule_matches,
    suggest_contrapartida_explained,
)
from app.erp.models import Order, OrderSource, PaymentStatus
from app.integrations.woocommerce.mapper import apply_payment_method
from app.main import app
from app.models.crm import Company
from app.models.integration_settings import (
    ExternalSystem,
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import auth_headers, seed_test_users

CATALOGO_CON_15 = [
    *({"codigo": str(c), "nombre": n} for c, n in DEFAULT_CONTRAPARTIDAS),
    {"codigo": "15", "nombre": "Tarjetas Mollie Belfius"},
]


# --- fixtures ---------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session_factory(engine) -> Generator[sessionmaker, None, None]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(Company(id="acme", name="Acme SL", factusol_company_id="55555"))
        for slug in ("artisjet", "boprint", "flux"):
            seed.add(IntegrationAccount(
                id=f"store-{slug}", system=ExternalSystem.WOOCOMMERCE, account_id=slug,
                display_name=slug, enabled=True, mode=IntegrationMode.LIVE,
                status=IntegrationStatus.CONFIGURED, base_url=f"https://{slug}.example",
                consumer_key_encrypted=encrypt("ck"), consumer_secret_encrypted=encrypt("cs"),
                credential_status="configured",
            ))
        seed.commit()
    yield factory


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _catalogo_con_15(http) -> None:
    """Bart da de alta la 15 en el catálogo de BoHub."""
    r = http.patch("/api/erp/settings", json={"contrapartidas": CATALOGO_CON_15},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text


def _order(s: Session, oid: str, *, store: str | None, title: str | None,
           gateway: str | None = None, invoice: str | None = "526200",
           serie: int | None = 2) -> Order:
    o = Order(
        id=oid, external_source=OrderSource.WOOCOMMERCE, external_id=oid,
        order_number=f"ORD-{oid}", company_id="acme", total_amount=121.0,
        store_id=f"store-{store}" if store else None,
        payment_status=PaymentStatus.PAID, woo_status="processing",
        payment_method=gateway, payment_method_title=title,
        factusol_invoice_number=invoice, factusol_invoice_serie=serie,
        invoice_status="invoiced_by_erp" if invoice else "not_invoiced",
        placed_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    s.add(o)
    return o


class FakeFactusol:
    """F_FAC / F_LCO / F_FPA mínimos para el estado de cobro."""

    default_ejercicio = "2026"

    def __init__(self, rows):
        self.rows = rows

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla == "F_FAC":
            if filtro.startswith("CODFAC="):
                wanted = filtro.split("=", 1)[1].strip()
                return [r for r in self.rows if str(r["CODFAC"]) == wanted]
            return list(self.rows)
        if tabla == "F_FPA":
            return [{"CODFPA": "002", "DESFPA": "Transferencia"},
                    {"CODFPA": "005", "DESFPA": "Paypal"}]
        return []


def _fac(serie: int, codigo: int, total: float = 121.0, fop: str = "002") -> dict:
    return {"TIPFAC": str(serie), "CODFAC": codigo, "TOTFAC": total, "ESTFAC": "0",
            "CNOFAC": "Cliente", "REFFAC": "", "FOPFAC": fop, "CLIFAC": "55555",
            "FECFAC": "2026-09-20T00:00:00"}


def _patched(fake):
    return patch("app.integrations.factusol.client.FactusolClient.from_settings",
                 return_value=fake)


# --- reglas iniciales (PayPal migrado + Mollie) -----------------------------------------


def test_reglas_iniciales_migran_paypal_y_anaden_mollie() -> None:
    def regla(tienda: str, metodo: str, coincidencia: str, codigo: str) -> dict:
        return {"tienda": tienda, "metodo": metodo, "coincidencia": coincidencia,
                "contrapartida": codigo}

    assert default_contrapartida_rules() == [
        regla("artisjet", "paypal", "contiene", "12"),
        regla("boprint", "paypal", "contiene", "14"),
        regla("fluxlasers", "paypal", "contiene", "14"),
        regla("artisjet", "Carte", "exacta", "15"),
        regla("artisjet", "mollie_wc_gateway_creditcard", "exacta", "15"),
    ]
    # Lo que Bart hubiera cambiado en PayPal por tienda se conserva al migrar.
    assert default_contrapartida_rules({"boprint": "11"})[1]["contrapartida"] == "11"


def test_coincidencia_sin_mayusculas_y_por_gateway() -> None:
    carte = {"tienda": "artisjet", "metodo": "Carte", "coincidencia": "exacta",
             "contrapartida": "15"}
    assert rule_matches(carte, store="artisjet", textos=["CARTE"]) == "CARTE"
    assert rule_matches(carte, store="ArtisJet", textos=["  carte "]) == "carte"
    assert rule_matches(carte, store="artisjet", textos=["Carte bancaire"]) is None
    assert rule_matches(carte, store="boprint", textos=["Carte"]) is None
    paypal = {"tienda": "", "metodo": "paypal", "coincidencia": "contiene",
              "contrapartida": "12"}
    assert rule_matches(paypal, store="cualquiera",
                        textos=[None, "ppcp-gateway", "PayPal Checkout"])
    gateway = {"tienda": "artisjet", "metodo": "mollie_wc_gateway_creditcard",
               "coincidencia": "exacta", "contrapartida": "15"}
    assert rule_matches(gateway, store="artisjet", textos=[None, "mollie_wc_gateway_creditcard"])


# --- sugerencia ---------------------------------------------------------------------------


def test_sugerencia_por_tienda_y_metodo(http, session_factory) -> None:
    _catalogo_con_15(http)
    with session_factory() as s:
        def sug(store, title=None, gateway=None, serie=2, forma=None):
            return suggest_contrapartida_explained(
                s, serie=serie, forma_nombre=forma, store=store,
                payment_method=gateway, payment_method_title=title,
            )

        cuenta, motivo = sug("artisjet", "Carte")
        assert cuenta == {"codigo": "15", "nombre": "Tarjetas Mollie Belfius"}
        assert motivo == "tienda artisJet · método Carte"
        assert sug("artisjet", "carte")[0]["codigo"] == "15"
        assert sug("artisjet", None, "mollie_wc_gateway_creditcard")[0]["codigo"] == "15"
        assert sug("artisjet", "PayPal")[0]["codigo"] == "12"
        assert sug("boprint", "PayPal", serie=5)[0]["codigo"] == "14"
        assert sug("flux", "PayPal", serie=5)[0]["codigo"] == "14"      # slug Woo de fluxlasers
        # La forma de pago de FACTUSOL (sin Woo) sigue casando como antes.
        assert sug("boprint", None, forma="Paypal", serie=5)[0]["codigo"] == "14"
        # boprint / fluxlasers no usan Mollie: «Carte» ahí no tiene regla → serie.
        cuenta, motivo = sug("boprint", "Carte", serie=5)
        assert cuenta["codigo"] == "8" and motivo == "cuenta de la serie 5"
        # Sin regla → la cuenta de la serie (2 → MQ Europe Belfius).
        cuenta, motivo = sug("artisjet", "Virement bancaire")
        assert cuenta == {"codigo": "2", "nombre": "MQ Europe Belfius"}
        assert motivo == "cuenta de la serie 2"


def test_regla_con_cuenta_fuera_del_catalogo_no_se_sugiere(session_factory) -> None:
    """Hasta que Bart dé de alta la 15, la regla de Mollie no se aplica: se
    sugiere la cuenta de la serie (nunca una cuenta con la que el cobro daría 400)."""
    with session_factory() as s:
        cuenta, motivo = suggest_contrapartida_explained(
            s, serie=2, store="artisjet", payment_method_title="Carte",
        )
    assert cuenta["codigo"] == "2" and motivo == "cuenta de la serie 2"


def test_reglas_configurables_en_orden(http, session_factory) -> None:
    admin = auth_headers(http, "admin")
    _catalogo_con_15(http)
    r = http.get("/api/erp/settings", headers=admin)
    assert [x["contrapartida"] for x in r.json()["contrapartida_rules"]] == [
        "12", "14", "14", "15", "15"]
    # Una regla general «tarjeta» → 15 por ENCIMA de las demás; «todas» = "".
    nuevas = [
        {"tienda": "", "metodo": "tarjeta", "coincidencia": "contiene", "contrapartida": "15"},
        {"tienda": "artisjet", "metodo": "paypal", "coincidencia": "contiene",
         "contrapartida": "12"},
        {"tienda": "", "metodo": "", "coincidencia": "contiene", "contrapartida": ""},   # vacía
    ]
    r = http.patch("/api/erp/settings", json={"contrapartida_rules": nuevas}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["contrapartida_rules"] == nuevas[:2]
    with session_factory() as s:
        assert contrapartida_rules(s) == nuevas[:2]
        assert suggest_contrapartida_explained(
            s, serie=5, store="boprint", payment_method_title="Tarjeta de crédito",
        )[0]["codigo"] == "15"
        # boprint + PayPal ya no tiene regla (se quitó) → PayPal de la serie 5.
        cuenta, motivo = suggest_contrapartida_explained(
            s, serie=5, store="boprint", payment_method_title="PayPal",
        )
        assert cuenta["codigo"] == "14" and "PayPal de la serie 5" in motivo
    r = http.get("/api/erp/catalogs/contrapartidas", headers=auth_headers(http, "user"))
    assert r.json()["rules"] == nuevas[:2]
    for mala, motivo in (
        ({"tienda": "", "metodo": "x", "coincidencia": "parecida", "contrapartida": "2"},
         "coincidencia"),
        ({"tienda": "", "metodo": "x", "coincidencia": "exacta", "contrapartida": "abc"},
         "contrapartida"),
        ({"tienda": "", "metodo": "", "coincidencia": "exacta", "contrapartida": "2"},
         "método"),
    ):
        r = http.patch("/api/erp/settings", json={"contrapartida_rules": [mala]}, headers=admin)
        assert r.status_code == 400 and motivo in r.text


# --- método de pago del pedido web ----------------------------------------------------------


def test_metodo_de_pago_de_woo_se_guarda_y_se_refresca() -> None:
    o = Order(order_number="X-1", external_source=OrderSource.WOOCOMMERCE)
    assert apply_payment_method(o, {"payment_method": "mollie_wc_gateway_creditcard",
                                    "payment_method_title": "Carte"}) is True
    assert (o.payment_method, o.payment_method_title) == ("mollie_wc_gateway_creditcard", "Carte")
    assert apply_payment_method(o, {"payment_method": "mollie_wc_gateway_creditcard",
                                    "payment_method_title": "Carte"}) is False
    # Woo sin método (p. ej. un payload parcial) no borra lo guardado.
    assert apply_payment_method(o, {}) is False
    assert o.payment_method_title == "Carte"


def test_backfill_del_metodo_de_pago_al_poner_al_dia(session_factory) -> None:
    from app.integrations.woocommerce.reconcile import reconcile_open_order_statuses

    with session_factory() as s:
        _order(s, "9518", store="artisjet", title=None, invoice=None)
        s.commit()

    class FakeWoo:
        def __init__(self, store):
            self.store = store

        def list_orders(self, *, status="processing", since=None, per_page=50, page=1):
            if status != "any" or page > 1 or self.store.account_id != "artisjet":
                return []
            return [{"id": 9518, "status": "processing",
                     "payment_method": "mollie_wc_gateway_creditcard",
                     "payment_method_title": "Carte"}]

        def get_order(self, order_id):
            raise AssertionError("no hace falta")

    with session_factory() as s:
        preview = reconcile_open_order_statuses(s, dry_run=True, client_factory=FakeWoo)
        assert preview["to_payment_method"] == 1
        assert preview["payment_method_samples"] == ["ORD-9518 → Carte"]
        assert s.get(Order, "9518").payment_method is None       # dry-run: nada
        done = reconcile_open_order_statuses(s, dry_run=False, client_factory=FakeWoo)
        assert done["to_payment_method"] == 1
    with session_factory() as s:
        o = s.get(Order, "9518")
        assert o.payment_method == "mollie_wc_gateway_creditcard"
        assert o.payment_method_title == "Carte"


# --- «Registrar cobro» (ficha / cola «Por cobrar») ------------------------------------------


def test_modal_registrar_cobro_sugiere_15_con_su_motivo(http, session_factory) -> None:
    _catalogo_con_15(http)
    with session_factory() as s:
        _order(s, "art", store="artisjet", title="Carte",
               gateway="mollie_wc_gateway_creditcard", invoice="526200", serie=2)
        s.commit()
    with _patched(FakeFactusol([_fac(2, 526200)])):
        r = http.get("/api/erp/orders/art/factusol-cobro", headers=auth_headers(http, "user"))
    body = r.json()
    assert r.status_code == 200 and body["status"] == "pendiente", body
    assert body["suggested_cuenta"] == {"codigo": "15", "nombre": "Tarjetas Mollie Belfius"}
    assert body["suggested_reason"] == "tienda artisJet · método Carte"
    assert body["payment_method_title"] == "Carte"
    detail = http.get("/api/erp/orders/art", headers=auth_headers(http, "user")).json()
    assert detail["payment_method_title"] == "Carte"
    assert detail["payment_method"] == "mollie_wc_gateway_creditcard"


# --- POST del cobro (lote CSV) -------------------------------------------------------------------


def _post_cobro(http, serie, codigo, **body):
    return http.post(
        f"/api/erp/factusol/documents/facturas/{serie}/{codigo}/collection",
        headers=auth_headers(http, "admin"),
        json={"confirm": True, "fecha": "2026-09-25", **body},
    )


def test_cobro_sin_cuenta_usa_la_regla_con_cuenta_la_respeta(http, session_factory) -> None:
    """Lote CSV: la fila sin contrapartida usa la regla (15 para Carte en
    artisJet); con contrapartida explícita, manda la del CSV (la sugerencia es
    editable). Nunca una cuenta fuera del catálogo."""
    _catalogo_con_15(http)
    with session_factory() as s:
        _order(s, "art", store="artisjet", title="Carte", invoice="526200", serie=2)
        s.commit()
    fake = FakeFactusol([_fac(2, 526200), _fac(9, 1)])
    with _patched(fake), patch(
        "app.integrations.factusol.jobs.enqueue_register_invoice_collection",
        return_value="job-1",
    ) as enq:
        sin = _post_cobro(http, 2, 526200)
        con = _post_cobro(http, 2, 526200, cuenta="2")
        fuera = _post_cobro(http, 2, 526200, cuenta="99")
        nada = _post_cobro(http, 9, 1)        # serie sin empresa ni pedido
    assert sin.status_code == 202, sin.text
    assert sin.json()["contrapartida"] == {"codigo": "15", "nombre": "Tarjetas Mollie Belfius"}
    assert sin.json()["contrapartida_sugerida_por"] == "tienda artisJet · método Carte"
    assert enq.call_args_list[0].args[2] == "15"
    assert con.status_code == 202
    assert con.json()["contrapartida"]["codigo"] == "2"
    assert con.json()["contrapartida_sugerida_por"] is None
    assert enq.call_args_list[1].args[2] == "2"
    assert fuera.status_code == 400 and fuera.json()["detail"]["code"] == "unknown_account"
    assert nada.status_code == 400 and nada.json()["detail"]["code"] == "missing_account"
    assert enq.call_count == 2


def test_cobro_de_factura_sin_pedido_sugiere_la_cuenta_de_la_serie(http, session_factory) -> None:
    with _patched(FakeFactusol([_fac(5, 260900)])):
        r = http.get("/api/erp/factusol/documents/facturas/5/260900/cobro",
                     headers=auth_headers(http, "user"))
    body = r.json()
    assert body["suggested_cuenta"]["codigo"] == "8"
    assert body["suggested_reason"] == "cuenta de la serie 5"
    assert body["order_number"] is None


# --- conciliación bancaria: un cobro Mollie (15) no se duplica en Belfius ---------------------


def test_conciliacion_no_casa_una_factura_ya_cobrada_por_15() -> None:
    """La factura cobrada por la 15 queda con saldo 0 en F_LCO: un abono de
    Belfius por el mismo importe no se propone contra ella (no se duplica)."""
    movimiento = {"importe": 121.0, "fecha_oper": None, "concepto": "MOLLIE PAYOUT",
                  "payer": "Mollie"}
    cobrada = {"serie": 2, "codigo": 526200, "numero": "2-526200", "cliente_codigo": "55555",
               "cliente_nombre": "Cliente", "fecha": "2026-09-20", "total": 121.0,
               "saldo_pendiente": 0.0, "estado": "2"}
    assert propose(movimiento, [cobrada]) == []
