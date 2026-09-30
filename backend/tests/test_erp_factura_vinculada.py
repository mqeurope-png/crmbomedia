"""ERP · factura VINCULADA al pedido: una sola lectura para todo BoHub.

Bug (30/09/2026): en pedidos creados desde una factura de FACTUSOL
(`factusol_factura`) y desde albarán con la factura hecha después
(`factusol_albaran`, caso ALB-2-200038 → 2-526107) la ficha decía «aún no tiene
factura en FACTUSOL» y el «PDF de la factura» fallaba, aunque el pedido tenía
estado + número + serie igual que los web que sí funcionaban. La causa:
`/factusol-invoice-ref` buscaba la factura en F_FAC por la REFFAC del pedido
(`ART-…`/`BOP-…`), que solo llevan los pedidos web.

Ahora «tiene factura» = estado facturado + `factusol_invoice_number` +
`factusol_invoice_serie` (`app.erp.linked_invoice`), sin depender del origen ni
de `factusol_manual_serie`; sin serie → «falta la serie», nunca por el número
solo. Todo sin escribir en FACTUSOL (ni consultarlo para localizar la factura).
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.linked_invoice import (
    SIN_FACTURA,
    SIN_SERIE,
    get_linked_invoice,
    invoice_label,
    invoice_link_problem,
)
from app.erp.models import Order, OrderSource, PaymentStatus
from app.erp.order_email import available_attachments
from app.erp.seguimiento import build_rows
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users

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


def _order(s: Session, oid: str, number: str, *, source: OrderSource,
           invoice: str | None = "526107", serie: int | None = 2,
           status: str = "invoiced_by_erp", albaran: str | None = None,
           manual_serie: int | None = None) -> Order:
    o = Order(
        id=oid, external_source=source, external_id=number.split("-")[-1],
        order_number=number, company_id="acme", total_amount=121.0,
        payment_status=PaymentStatus.PAID, invoice_status=status,
        factusol_invoice_number=invoice, factusol_invoice_serie=serie,
        factusol_manual_serie=manual_serie, factusol_albaran_number=albaran,
        placed_at=datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
        approved_at=datetime(2026, 9, 20, 11, 0, tzinfo=UTC),
    )
    s.add(o)
    return o


def _seed_real_cases(session_factory) -> None:
    """Los pedidos de la tabla de producción (30/09/2026) + los que faltan."""
    with session_factory() as s:
        # El roto: desde albarán, factura vinculada después (serie 2, sin
        # serie manual).
        _order(s, "alb", "ALB-2-200038", source=OrderSource.FACTUSOL_ALBARAN,
               albaran="2-200038")
        # Creado desde una factura de FACTUSOL.
        _order(s, "fac", "FAC-2-526110", source=OrderSource.FACTUSOL_FACTURA,
               invoice="526110")
        # Manual con factura (la serie MANUAL es otra: no se usa para localizar).
        _order(s, "man", "BOP-M-0042", source=OrderSource.MANUAL, invoice="100245",
               serie=1, manual_serie=5)
        # Web emitido desde BoHub (sin cambios).
        _order(s, "web", "ARTISJ-9518", source=OrderSource.WOOCOMMERCE, invoice="526094")
        # Con número pero SIN serie (ARTISJ-9460 → 260721).
        _order(s, "nos", "ARTISJ-9460", source=OrderSource.WOOCOMMERCE, invoice="260721",
               serie=None, status="already_invoiced_externally")
        # Sin factura.
        _order(s, "none", "BOPRIN-99999", source=OrderSource.WOOCOMMERCE, invoice=None,
               serie=None, status="not_invoiced")
        s.commit()


def _no_factusol():
    """Localizar la factura del pedido NO consulta FACTUSOL."""
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        side_effect=AssertionError("no debe consultar FACTUSOL"),
    )


# --- la definición única ----------------------------------------------------------


def test_definicion_unica_de_tiene_factura() -> None:
    def o(**kw):
        base = {"invoice_status": "invoiced_by_erp", "factusol_invoice_number": "526107",
                "factusol_invoice_serie": 2, "factusol_manual_serie": None,
                "external_source": "factusol_albaran"}
        return SimpleNamespace(**{**base, **kw})

    linked = get_linked_invoice(o())
    assert (linked.serie, linked.codigo, linked.numero) == (2, 526107, "2-526107")
    assert invoice_label(o()) == "2-526107"
    # El origen no importa; la serie MANUAL tampoco (ni la sustituye).
    for source in ("factusol_factura", "manual", "woocommerce", "factusol_proforma"):
        assert get_linked_invoice(o(external_source=source)).numero == "2-526107"
    assert get_linked_invoice(o(factusol_manual_serie=5)).serie == 2
    sin_serie_con_manual = o(factusol_invoice_serie=None, factusol_manual_serie=5)
    assert invoice_link_problem(sin_serie_con_manual) == SIN_SERIE
    # Sin serie: no hay factura utilizable, se enseña el número tal cual.
    nos = o(factusol_invoice_serie=None, invoice_status="already_invoiced_externally")
    assert get_linked_invoice(nos) is None and invoice_link_problem(nos) == SIN_SERIE
    assert invoice_label(nos) == "526107"
    # Datos antiguos con la serie dentro del número.
    assert get_linked_invoice(o(factusol_invoice_number="5-260086",
                                factusol_invoice_serie=None)).numero == "5-260086"
    # Sin número, o sin estado de facturado → sin factura.
    assert invoice_link_problem(o(factusol_invoice_number=None)) == SIN_FACTURA
    assert invoice_link_problem(o(invoice_status="not_invoiced")) == SIN_FACTURA
    assert invoice_label(o(factusol_invoice_number=None)) == ""


# --- ficha: PDF de la factura / Enviar factura (clave de la factura) --------------


@pytest.mark.parametrize(("oid", "numero"), [
    ("alb", "2-526107"), ("fac", "2-526110"), ("man", "1-100245"), ("web", "2-526094"),
])
def test_invoice_ref_sale_del_vinculo_sea_cual_sea_el_origen(
    http, session_factory, oid, numero,
) -> None:
    """ALB-2-200038 y compañía: la clave de la factura (la que usan «PDF de la
    factura» y «Enviar factura al cliente») sale del vínculo guardado, sin
    buscar en FACTUSOL por la referencia del pedido. El web, igual que antes."""
    _seed_real_cases(session_factory)
    with _no_factusol():
        r = http.get(f"/api/erp/orders/{oid}/factusol-invoice-ref",
                     headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    serie, codigo = numero.split("-")
    assert r.json() == {"serie": int(serie), "codigo": int(codigo), "numero": numero}


def test_invoice_ref_sin_serie_o_sin_factura(http, session_factory) -> None:
    _seed_real_cases(session_factory)
    with _no_factusol():
        nos = http.get("/api/erp/orders/nos/factusol-invoice-ref",
                       headers=auth_headers(http, "user"))
        none = http.get("/api/erp/orders/none/factusol-invoice-ref",
                        headers=auth_headers(http, "user"))
    assert nos.status_code == 409
    assert nos.json()["detail"]["code"] == "invoice_serie_missing"
    assert "falta la serie" in nos.json()["detail"]["detail"]
    assert "260721" in nos.json()["detail"]["detail"]
    assert none.status_code == 404
    assert none.json()["detail"]["code"] == "invoice_not_linked"


def test_ficha_factura_vinculada_y_linea_de_vida(http, session_factory) -> None:
    """La ficha recibe la factura vinculada («2-526107») y la línea de vida la
    enseña con su serie; sin serie, el número y «falta la serie»."""
    _seed_real_cases(session_factory)
    with _no_factusol():
        alb = http.get("/api/erp/orders/alb", headers=auth_headers(http, "user")).json()
        nos = http.get("/api/erp/orders/nos", headers=auth_headers(http, "user")).json()
    assert alb["factusol_invoice"] == {"serie": 2, "codigo": 526107, "numero": "2-526107"}
    assert alb["factusol_invoice_problem"] is None
    steps = {st["key"]: st for st in alb["workflow"]["steps"]}
    assert steps["factura"]["state"] == "done" and steps["factura"]["detail"] == "2-526107"
    assert nos["factusol_invoice"] is None
    assert nos["factusol_invoice_problem"] == "sin_serie"
    steps = {st["key"]: st for st in nos["workflow"]["steps"]}
    assert steps["factura"]["detail"] == "260721 · falta la serie"
    rows = http.get("/api/erp/orders", headers=auth_headers(http, "user")).json()["items"]
    by_id = {x["id"]: x for x in rows}
    assert by_id["fac"]["factusol_invoice"]["numero"] == "2-526110"
    assert by_id["none"]["factusol_invoice"] is None
    assert by_id["none"]["factusol_invoice_problem"] == "sin_factura"


# --- «Enviar pedido» con la factura adjunta ---------------------------------------


class _NoPedidoClient:
    def load_table(self, *_a, **_k):
        raise AssertionError("la factura no se busca en FACTUSOL")


def test_email_del_pedido_adjunta_la_factura_vinculada(session_factory) -> None:
    _seed_real_cases(session_factory)
    with session_factory() as s:
        alb = available_attachments(s, _NoPedidoClient(), s.get(Order, "alb"), "2026")
        nos = available_attachments(s, _NoPedidoClient(), s.get(Order, "nos"), "2026")
    assert alb["factura"]["available"] is True
    assert alb["factura"]["numero"] == "2-526107"
    assert nos["factura"]["available"] is False
    assert nos["factura"]["code"] == "factura_sin_serie"
    assert "falta la serie" in nos["factura"]["reason"]


# --- «Registrar cobro» --------------------------------------------------------------


class _FakeFac:
    """F_FAC con la 526107 en la serie 2 (la del pedido) y un homónimo en la 1."""

    default_ejercicio = "2026"

    def __init__(self) -> None:
        self.loaded: list[str] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        self.loaded.append(tabla)
        if tabla == "F_FAC":
            return [
                {"TIPFAC": "1", "CODFAC": 526107, "TOTFAC": 10.0, "ESTFAC": "0",
                 "REFFAC": "", "FOPFAC": "002"},
                {"TIPFAC": "2", "CODFAC": 526107, "TOTFAC": 121.0, "ESTFAC": "0",
                 "REFFAC": "", "FOPFAC": "002"},
            ]
        if tabla == "F_FPA":
            return [{"CODFPA": "002", "DESFPA": "Transferencia"}]
        return []


def test_registrar_cobro_usa_la_factura_vinculada(http, session_factory) -> None:
    """ALB-2-200038: «Registrar cobro» resuelve la 2-526107 (no el homónimo de
    la serie 1) aunque el pedido no tenga REFFAC; sin serie, «falta la serie»
    sin tocar FACTUSOL (ni para buscar por el número)."""
    _seed_real_cases(session_factory)
    fake = _FakeFac()
    with patch("app.integrations.factusol.client.FactusolClient.from_settings",
               return_value=fake):
        alb = http.get("/api/erp/orders/alb/factusol-cobro",
                       headers=auth_headers(http, "user")).json()
    assert alb["status"] == "pendiente", alb
    assert alb["invoice"] == {"serie": 2, "codigo": 526107, "numero": "2-526107"}
    assert alb["total"] == 121.0
    with _no_factusol():
        nos = http.get("/api/erp/orders/nos/factusol-cobro",
                       headers=auth_headers(http, "user")).json()
    assert nos["status"] == "unresolved" and nos["reason"] == "sin_serie"
    assert "falta la serie" in nos["detail"]


# --- ERP · Seguimiento y la hoja «Seguimiento (app)» --------------------------------


def test_seguimiento_factura_con_serie(session_factory) -> None:
    """Columnas Factura / Fecha factura: «serie-número» para todos los orígenes;
    el creado desde una factura toma la fecha del propio documento; sin serie,
    el número tal cual (no se adivina la serie)."""
    _seed_real_cases(session_factory)
    with session_factory() as s:
        rows = {r["id"]: r for r in build_rows(s, customer_names={})}
    assert rows["alb"]["factura"] == "2-526107"
    assert rows["fac"]["factura"] == "2-526110"
    assert rows["fac"]["fecha_factura"] == "2026-09-20"
    assert rows["man"]["factura"] == "1-100245"
    assert rows["web"]["factura"] == "2-526094"
    assert rows["nos"]["factura"] == "260721"
    assert rows["none"]["factura"] == ""
