"""ERP · Pedidos de muestra con documento FACTUSOL, anular con factura y
desvincular (rev. 30/09/2026 — caso real MUESTRA-000003 ↔ 2-526110).

- A una MUESTRA se le vincula un albarán / proforma / factura de FACTUSOL y
  carga sus datos (cliente → empresa CRM, líneas, importes, serie, forma de
  pago y el vínculo): pasa a comportarse como un pedido creado desde ese
  documento, conservando su nº MUESTRA-… y el badge «muestra».
- «Anular pedido» funciona aunque tenga factura: la factura (y el albarán /
  proforma) se DESVINCULAN y siguen en FACTUSOL. Nada se escribe allí.
- «Desvincular» sin anular: la muestra convertida vuelve a modo muestra; un
  pedido normal queda «sin factura» y facturable de nuevo.

FACTUSOL simulado: cualquier escritura (write / update / delete) hace fallar
el test.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import InvoiceStatus, Order, OrderSource, OrderStatusHistory
from app.erp.sample_orders import ORDER_KIND_SAMPLE, ORDER_KIND_SAMPLE_CONVERTED
from app.erp.workflow import order_steps
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_pedido_desde_factusol import (
    FPA,
    _alb,
    _fac,
    _lal,
    _lfa,
    _lps,
    _pre,
)
from tests.test_erp_pedido_desde_factusol import FakeClient as _ReadOnlyFake

DIRECCION = {"address_line": "Keizersgracht 1", "city": "Amsterdam",
             "postal_code": "1015", "country": "Países Bajos"}


class ReadOnlyFactusol(_ReadOnlyFake):
    """Solo lectura: cualquier escritura en FACTUSOL es un fallo."""

    def write_record(self, *a, **k):  # noqa: ANN002, ANN003
        raise AssertionError(f"escritura en FACTUSOL: {a}")

    def update_record(self, *a, **k):  # noqa: ANN002, ANN003
        raise AssertionError(f"actualización en FACTUSOL: {a}")

    def delete_records(self, *a, **k):  # noqa: ANN002, ANN003
        raise AssertionError(f"borrado en FACTUSOL: {a}")


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        # Factura 2-526110 de PREMO B.V. (cliente 7001), 5.059,00 € con IVA.
        "F_FAC": [
            {**_fac(526110, serie="2", clifac="7001", total=5059.0, ref="PREMO"),
             "CNOFAC": "PREMO B.V.", "NET1FAC": 4181.0, "PIVA1FAC": 21.0},
            _fac(526111, serie="2", clifac="9999", total=100.0),
        ],
        "F_LFA": [
            _lfa(526110, 1, serie="2", art="MBO", desc="Impresora UV", cant=1, precio=4000),
            _lfa(526110, 2, serie="2", desc="Portes", cant=1, precio=181),
        ],
        "F_ALB": [{**_alb(700, serie="5", clialb="7001", total=242.0), "CNOALB": "PREMO B.V."}],
        "F_LAL": [_lal(700, 1, serie="5", art="TIN", desc="Tinta", cant=2, precio=100)],
        "F_PRE": [_pre(45, clipre="7001", total=121.0)],
        "F_LPS": [_lps(45, 1, art="SAT", desc="Hora SAT", cant=1, precio=100)],
        "F_FPA": FPA,
    }


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
        seed.add(Company(id="premo", name="PREMO B.V.", factusol_company_id="7001"))
        seed.commit()
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


def _patched(fake: _ReadOnlyFake):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings", return_value=fake,
    )


def _muestra(http) -> dict[str, Any]:
    r = http.post("/api/erp/orders/sample", json={
        "recipient_name": "PREMO B.V.", "shipping_address": DIRECCION, "reason": "muestra",
        "lines": [{"product_sku": "MUE-1", "description": "Muestra de tinta",
                   "quantity": 1, "unit_price": 0}],
    }, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    return r.json()


def _link(http, oid: str, doc_type: str, serie: int, codigo: int, **extra):
    return http.post(f"/api/erp/orders/{oid}/link-document", json={
        "doc_type": doc_type, "serie": serie, "codigo": codigo, "confirm": True, **extra,
    }, headers=auth_headers(http, "pedidos"))


def _queue(http, cola: str) -> set[str]:
    r = http.get("/api/erp/orders", params={"queue": cola},
                  headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    return {o["order_number"] for o in r.json()["items"]}


# --- A. vincular documento a una muestra ----------------------------------------


def test_muestra_vincula_factura_y_carga_sus_datos(http, session_factory) -> None:
    m = _muestra(http)
    fake = ReadOnlyFactusol(_tables())
    with _patched(fake):
        pre = http.get(f"/api/erp/orders/{m['id']}/link-document/preview",
                       params={"doc_type": "facturas", "serie": 2, "codigo": 526110},
                       headers=auth_headers(http, "pedidos"))
        r = _link(http, m["id"], "facturas", 2, 526110)
    assert pre.status_code == 200, pre.text
    assert pre.json()["company_name"] == "PREMO B.V." and pre.json()["is_sample"] is True
    assert pre.json()["linked_elsewhere"] is None
    assert r.status_code == 200, r.text
    body = r.json()
    # Mismo número y badge «muestra», pero ya es un pedido con factura.
    assert body["order_number"] == m["order_number"]
    assert body["order_kind"] == ORDER_KIND_SAMPLE_CONVERTED and body["born_as_sample"] is True
    assert body["external_source"] == "factusol_factura"
    assert body["company_id"] == "premo"
    assert body["total_amount"] == 5059.0
    assert [ln["description"] for ln in body["lines"]] == ["Impresora UV", "Portes"]
    assert body["factusol_manual_serie"] == 2
    assert body["factusol_invoice_number"] == "526110"
    assert body["factusol_invoice_serie"] == 2
    assert body["packing"]["factusol_source"]["forma_pago_nombre"] == "Recibo domiciliado"
    assert [d["numero"] for d in body["linked_documents"]] == ["2-526110"]
    assert body["sample_pending_link"] is None
    assert any("Muestra vinculada a la factura FACTUSOL 2-526110" in (h["reason"] or "")
               for h in body["status_history"])
    # Línea de vida SIN «no aplica · no facturable».
    with session_factory() as s:
        o = s.get(Order, m["id"])
        estados = {st["key"]: (st["state"], st.get("detail")) for st in order_steps(o)}
    assert all(det != "no facturable" for _state, det in estados.values()), estados
    assert estados["factura"][0] == "done"
    # «Por cobrar» la lista; PDF / envío de factura la encuentran (#493).
    assert m["order_number"] in _queue(http, "por_cobrar")
    ref = http.get(f"/api/erp/orders/{m['id']}/factusol-invoice-ref",
                   headers=auth_headers(http, "user"))
    assert ref.status_code == 200 and ref.json()["numero"] == "2-526110"
    # La bandeja también lleva la marca.
    r = http.get("/api/erp/orders", params={"q": m["order_number"]},
                 headers=auth_headers(http, "admin"))
    fila = next(o for o in r.json()["items"] if o["id"] == m["id"])
    assert fila["born_as_sample"] is True


def test_muestra_vincula_albaran_o_proforma(http, session_factory) -> None:
    alb = _muestra(http)
    pro = _muestra(http)
    fake = ReadOnlyFactusol(_tables())
    with _patched(fake):
        ra = _link(http, alb["id"], "albaranes", 5, 700)
        rp = _link(http, pro["id"], "presupuestos", 1, 45)
    assert ra.status_code == 200, ra.text
    a = ra.json()
    # Como un pedido creado desde el albarán: su nº ligado, sin factura.
    assert a["external_source"] == "factusol_albaran"
    assert a["factusol_albaran_number"] == "5-000700"
    assert a["factusol_invoice_number"] is None
    assert a["total_amount"] == 242.0 and a["company_id"] == "premo"
    assert rp.status_code == 200, rp.text
    p = rp.json()
    # Como un pedido creado desde la proforma: facturable después.
    assert p["external_source"] == "factusol_proforma"
    assert p["factusol_albaran_number"] is None and p["factusol_invoice_number"] is None
    assert [d["kind"] for d in p["linked_documents"]] == ["origen"]
    with session_factory() as s:
        o = s.get(Order, pro["id"])
        estados = {st["key"]: st["state"] for st in order_steps(o)}
    assert estados["factura"] != "skipped" and estados["albaran"] != "skipped"


def test_cliente_sin_empresa_crm_abre_el_flujo_de_vincular(http, session_factory) -> None:
    m = _muestra(http)
    fake = ReadOnlyFactusol(_tables())
    with _patched(fake):
        r = _link(http, m["id"], "facturas", 2, 526111)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "factusol_customer_unlinked"
    assert detail["codcli"] == "9999"
    # Nada cambió: sigue siendo una muestra pura.
    with session_factory() as s:
        o = s.get(Order, m["id"])
        assert o.order_kind == ORDER_KIND_SAMPLE and o.factusol_invoice_number is None
    # Tras vincular / crear la empresa, el mismo vínculo entra.
    with session_factory() as s:
        s.add(Company(id="otro", name="Otro BV", factusol_company_id="9999"))
        s.commit()
    with _patched(fake):
        r = _link(http, m["id"], "facturas", 2, 526111)
    assert r.status_code == 200, r.text
    assert r.json()["company_id"] == "otro"


def test_documento_de_otro_pedido_y_solo_muestras(http, session_factory) -> None:
    m1, m2 = _muestra(http), _muestra(http)
    fake = ReadOnlyFactusol(_tables())
    with _patched(fake):
        assert _link(http, m1["id"], "facturas", 2, 526110).status_code == 200
        otro = _link(http, m2["id"], "facturas", 2, 526110)
        # Ya convertida: no se le vincula otro documento encima.
        encima = _link(http, m1["id"], "albaranes", 5, 700)
    assert otro.status_code == 409
    assert otro.json()["detail"]["code"] == "document_linked_elsewhere"
    assert otro.json()["detail"]["order_number"] == m1["order_number"]
    assert encima.status_code == 409 and encima.json()["detail"]["code"] == "not_a_sample"


def test_reprocesar_vinculo_hecho_desde_documentos(http, session_factory) -> None:
    """MUESTRA-000003: la factura se apuntó sin cargar datos (muestra pura con
    factura). La ficha ofrece «Reprocesar vínculo» y el mismo documento entra."""
    m = _muestra(http)
    with session_factory() as s:
        o = s.get(Order, m["id"])
        o.factusol_invoice_number = "526110"
        o.factusol_invoice_serie = 2
        o.invoice_status = InvoiceStatus.INVOICED_BY_ERP.value
        s.commit()
    r = http.get(f"/api/erp/orders/{m['id']}", headers=auth_headers(http, "pedidos"))
    assert r.json()["sample_pending_link"]["numero"] == "2-526110"
    with _patched(ReadOnlyFactusol(_tables())):
        r = _link(http, m["id"], "facturas", 2, 526110)
    assert r.status_code == 200, r.text
    assert r.json()["total_amount"] == 5059.0 and r.json()["company_id"] == "premo"
    assert r.json()["sample_pending_link"] is None


def test_documentos_vincular_a_una_muestra_se_rechaza(http, session_factory) -> None:
    """ERP · Documentos «Vincular» solo apuntaba el nº (la muestra quedaba
    incoherente): con una muestra, se remite a su ficha."""
    m = _muestra(http)
    with _patched(ReadOnlyFactusol(_tables())):
        r = http.post("/api/erp/factusol/documents/facturas/2/526110/link-order",
                      json={"order_id": m["id"], "confirm": True},
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "order_is_sample"


# --- B. anular con factura -----------------------------------------------------------


def test_anular_pedido_con_factura_desvincula_sin_tocar_factusol(http, session_factory) -> None:
    m = _muestra(http)
    fake = ReadOnlyFactusol(_tables())
    with _patched(fake):
        assert _link(http, m["id"], "facturas", 2, 526110).status_code == 200
        pre = http.post(f"/api/erp/orders/{m['id']}/cancel-preview",
                        headers=auth_headers(http, "pedidos")).json()
        r = http.post(f"/api/erp/orders/{m['id']}/cancel",
                      json={"confirm": True, "delete_factusol_docs": True},
                      headers=auth_headers(http, "pedidos"))
    assert pre["can_cancel"] is True and pre["blockers"] == []
    assert pre["documents_to_unlink"][0]["message"] == (
        "La factura 2-526110 seguirá existiendo en FACTUSOL y dejará de estar "
        "vinculada a este pedido. Si hay que anularla o abonarla, hazlo en FACTUSOL."
    )
    assert pre["factusol_docs"] == []           # con factura no se borra nada
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancelled_at"]
    assert body["factusol_invoice_number"] is None and body["factusol_invoice_serie"] is None
    assert body["invoice_status"] == "not_invoiced"
    assert body["factusol_delete_job_id"] is None
    assert body["linked_documents"] == []
    assert any(h["reason"] == "Anulado; factura 2-526110 desvinculada (sigue en FACTUSOL)"
               for h in body["status_history"])
    # Fuera de las colas vivas y de «Por cobrar».
    assert m["order_number"] not in _queue(http, "por_cobrar")
    # «Restaurar» vuelve a vincularla (nadie más la tiene).
    r = http.post(f"/api/erp/orders/{m['id']}/uncancel", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["relinked"]["restored"] == ["factura 2-526110"]
    assert r.json()["factusol_invoice_number"] == "526110"
    assert r.json()["factusol_invoice_serie"] == 2


def test_anular_pedido_normal_con_factura_y_albaran(http, session_factory) -> None:
    with session_factory() as s:
        o = Order(external_source=OrderSource.MANUAL, order_number="MANUAL-000900",
                  total_amount=121, currency="EUR", factusol_invoice_number="260500",
                  factusol_invoice_serie=5, invoice_status=InvoiceStatus.INVOICED_BY_ERP.value,
                  factusol_albaran_number="5-000900")
        s.add(o)
        s.commit()
        oid = o.id
    with _patched(ReadOnlyFactusol({})):
        r = http.post(f"/api/erp/orders/{oid}/cancel", json={"confirm": True, "reason": "error"},
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["factusol_invoice_number"] is None and b["factusol_albaran_number"] is None
    assert {d["numero"] for d in b["unlinked_documents"]} == {"5-260500", "5-000900"}
    with session_factory() as s:
        reasons = [h.reason for h in s.scalars(
            select(OrderStatusHistory).where(OrderStatusHistory.order_id == oid))]
    assert (
        "Anulado (error); albarán 5-000900 desvinculado (sigue en FACTUSOL), "
        "factura 5-260500 desvinculada (sigue en FACTUSOL)"
    ) in reasons


# --- C. desvincular sin anular ------------------------------------------------------


def test_desvincular_factura_de_una_muestra_vuelve_a_modo_muestra(http, session_factory) -> None:
    m = _muestra(http)
    with _patched(ReadOnlyFactusol(_tables())):
        assert _link(http, m["id"], "facturas", 2, 526110).status_code == 200
        r = http.post(f"/api/erp/orders/{m['id']}/unlink-document",
                      json={"kind": "factura", "confirm": True},
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["unlinked"]["back_to_sample"] is True
    assert b["order_kind"] == ORDER_KIND_SAMPLE and b["external_source"] == "manual"
    assert b["total_amount"] == 0 and b["company_id"] is None
    assert [ln["description"] for ln in b["lines"]] == ["Muestra de tinta"]
    assert b["factusol_invoice_number"] is None and b["factusol_manual_serie"] is None
    assert any("Factura 2-526110 desvinculada (sigue en FACTUSOL)" in (h["reason"] or "")
               for h in b["status_history"])
    # Vuelve a no ser facturable.
    assert m["order_number"] not in _queue(http, "por_cobrar")
    with session_factory() as s:
        estados = {st["key"]: st["state"] for st in order_steps(s.get(Order, m["id"]))}
    assert estados["factura"] == "skipped"


def test_desvincular_factura_de_un_pedido_normal(http, session_factory) -> None:
    with session_factory() as s:
        o = Order(external_source=OrderSource.MANUAL, order_number="MANUAL-000901",
                  total_amount=121, currency="EUR", factusol_invoice_number="260501",
                  factusol_invoice_serie=5, invoice_status=InvoiceStatus.INVOICED_BY_ERP.value,
                  factusol_cobro_status="pendiente", company_id="premo")
        s.add(o)
        s.commit()
        oid = o.id
    r = http.post(f"/api/erp/orders/{oid}/unlink-document",
                  json={"kind": "factura", "confirm": True},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["factusol_invoice_number"] is None and b["invoice_status"] == "not_invoiced"
    assert b["factusol_cobro_status"] is None
    assert b["unlinked"]["back_to_sample"] is False
    # Facturable de nuevo: el paso Factura vuelve a estar pendiente y ya no
    # está «Por cobrar».
    assert "MANUAL-000901" not in _queue(http, "por_cobrar")
    with session_factory() as s:
        estados = {st["key"]: st["state"] for st in order_steps(s.get(Order, oid))}
    assert estados["factura"] not in ("done", "skipped")
    assert b["workflow"]["next_action"] != "registrar_cobro"
    # Sin confirmación / sin ese documento.
    assert http.post(f"/api/erp/orders/{oid}/unlink-document", json={"kind": "factura"},
                     headers=auth_headers(http, "pedidos")).status_code == 400
    nada = http.post(f"/api/erp/orders/{oid}/unlink-document",
                     json={"kind": "factura", "confirm": True},
                     headers=auth_headers(http, "pedidos"))
    assert nada.status_code == 404 and nada.json()["detail"]["code"] == "not_linked"
    with session_factory() as s:
        meta = [json.loads(h.metadata_json or "{}") for h in s.scalars(
            select(OrderStatusHistory).where(OrderStatusHistory.order_id == oid))]
    assert any(m_.get("event") == "document_unlinked" for m_ in meta)
