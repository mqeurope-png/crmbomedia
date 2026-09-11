"""ERP · Fase 2 — al convertir una proforma / pedido de cliente NO-web en
pedido de BoHub: crear el ALBARÁN en FACTUSOL y confirmar el pago (opción B).

Cliente FACTUSOL simulado con escrituras en memoria (el mismo de la cadena
E3-B): F_PRE/F_LPS, F_PCL/F_LPC, F_ALB/F_LAL con filas REALES (tipos de
`--alb-row`), F_FAC/F_LFA y F_LCO (plantilla real de F-4-B). Cubre:

- el albarán con `COD*` entero y `ESTALB=0`, copia del origen, enlace por línea;
- pedido de cliente manual → albarán (`DOC='C'`) y el GUARDARRAÍL web
  (pedido Woo de BoHub y F_PCL de tienda web: nada se escribe);
- idempotencia (nº guardado + enlace `DOC/DTP/DCO` ya existente en FACTUSOL);
- pago opción B: «pagado» apunta el pago SIN emitir factura y el cobro F-4-B
  se registra solo al emitir la factura desde la ficha o al facturar el
  albarán desde el explorador; «sin pago» → pendiente, sin cobro;
- el guard de esquema del dry-run: si la fila real no cuadra, no se escribe.
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
from app.erp.factusol_albaran import (
    PaymentIn,
    WebOrderNoAlbaran,
    albaran_blocker,
    create_albaran_for_order,
    payment_intent,
    pending_collection,
    record_payment_intent,
    resolve_payment,
    web_pedido_reason,
)
from app.erp.models import Order, OrderSource, OrderStatusHistory
from app.integrations.factusol.chain import convert_document
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.service import emit_invoice
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_chain import (
    LINEAS_LPS,
    PRESUPUESTO,
    WriteFakeClient,
    _live_alb_row,
    _live_lal_row,
)

# ---------------------------------------------------------------------------
# Datos: presupuesto 5-27 (DUPLICODER), pedido de cliente MANUAL 5-123, pedido
# de cliente WEB 5-5 (REFPCL BOP-099917, el de un pedido Woo de BoHub),
# albaranes reales de la serie 5, F_LCO con una fila real de plantilla.
# ---------------------------------------------------------------------------

PEDIDO_MANUAL = {
    "TIPPCL": "5", "CODPCL": 123, "CLIPCL": 2458, "CNOPCL": "DUPLICODER, S.L.",
    "FECPCL": "2026-09-02T00:00:00", "ESTPCL": 0, "REFPCL": "ENCARGO TALLER 7",
    "TOTPCL": 60.5, "NET1PCL": 50.0, "FOPPCL": "011", "PENPCL": 0, "ALMPCL": "GEN",
}
PEDIDO_WEB = {
    "TIPPCL": "5", "CODPCL": 5, "CLIPCL": 2458, "CNOPCL": "DUPLICODER, S.L.",
    "FECPCL": "2026-09-02T00:00:00", "ESTPCL": 0, "REFPCL": "BOP-099917",
    "TOTPCL": 99.0, "NET1PCL": 81.82, "FOPPCL": "002", "PENPCL": 0, "ALMPCL": "GEN",
}
LINEAS_LPC = [
    {"TIPLPC": "5", "CODLPC": 123, "POSLPC": 1, "ARTLPC": "CDR80WPT",
     "DESLPC": "CD TQ 700 MB", "CANLPC": 100, "PRELPC": 0.5, "TOTLPC": 50.0,
     "PENLPC": 0},
    {"TIPLPC": "5", "CODLPC": 5, "POSLPC": 1, "ARTLPC": "99cy",
     "DESLPC": "Tinta", "CANLPC": 1, "PRELPC": 81.82, "TOTLPC": 81.82,
     "PENLPC": 0},
]
LCO_REAL = {
    "ANTLCO": 0, "CAJLCO": 0, "CFALCO": 260001, "CPALCO": 8,
    "CPTLCO": "COBRO FACTURA Nº: 5 - 260001", "FALLCO": "2026-01-21T00:00:00",
    "FECLCO": "2026-01-20T00:00:00", "FPALCO": "", "FUMLCO": "1900-01-01T00:00:00",
    "IMPLCO": 411.28, "LINLCO": 1, "MULLCO": 51, "OBSLCO": "", "PCALCO": 0,
    "PROLCO": "", "TERLCO": 0, "TFALCO": "5", "TIDLCO": "", "TIPLCO": 0,
    "TPVIDLCO": "", "TRALCO": 0, "UALLCO": 8, "UUMLCO": 0,
}
FPA = [{"CODFPA": "002", "DESFPA": "Transferencia"},
       {"CODFPA": "011", "DESFPA": "Recibo domiciliado"}]


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "F_PRE": [dict(PRESUPUESTO)],
        "F_LPS": [dict(r) for r in LINEAS_LPS],
        "F_PCL": [dict(PEDIDO_MANUAL), dict(PEDIDO_WEB)],
        "F_LPC": [dict(r) for r in LINEAS_LPC],
        "F_ALB": [_live_alb_row(500003, "5", CLIALB=2458, CNOALB="OTRO SL",
                                TOTALB=12.0, FOPALB="002")],
        "F_LAL": [_live_lal_row(500003, "5", ARTLAL="x", DESLAL="y", CANLAL=1,
                                PRELAL=12.0, TOTLAL=12.0, IVALAL=21)],
        "F_FAC": [], "F_LFA": [],
        "F_LCO": [dict(LCO_REAL)],
        "F_FPA": FPA,
    }


def _client(tables: dict[str, list[dict[str, Any]]] | None = None) -> WriteFakeClient:
    return WriteFakeClient(tables if tables is not None else _tables())


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
        seed.add(Company(id="dupli", name="Duplicoder SL", factusol_company_id="2458"))
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db(session_factory) -> Generator[Session, None, None]:
    with session_factory() as s:
        yield s


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _patched(fake: WriteFakeClient):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


def _import(db: Session, fake: WriteFakeClient, doc_type: str, serie: int,
            codigo: int, *, payment: dict[str, Any] | None = None) -> Order:
    """Pedido de BoHub desde el documento (Fase 1) + paso de pago (Fase 2)."""
    from app.erp.orders_from_factusol import create_order_from_factusol_document

    order = create_order_from_factusol_document(
        db, fake, doc_type=doc_type, serie=serie, codigo=codigo, ejercicio="2026",
    )
    if payment is not None:
        record_payment_intent(db, order, resolve_payment(db, PaymentIn(**payment)))
    db.commit()
    return order


def _written(fake: WriteFakeClient, tabla: str) -> list[dict[str, Any]]:
    return [p for t, p in fake.written if t == tabla]


def _reasons(db: Session, order: Order) -> list[str]:
    return [
        h.reason or "" for h in db.scalars(
            select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.id)
        )
    ]


# --- A) el albarán ----------------------------------------------------------------


def test_convertir_proforma_crea_albaran_factusol(db) -> None:
    """Presupuesto 5-27 → pedido → albarán 5-500004 con el motor E3-B: cabecera
    copiada del origen real (cliente, importes, forma de pago), `CODALB`/`CODLAL`
    ENTEROS, `ESTALB=0` (no heredado del ESTPRE=1), enlace `P/5/27` en cada
    línea, PEDALB vacío; el nº queda en el pedido y el origen se marca."""
    fake = _client()
    order = _import(db, fake, "presupuestos", 5, 27,
                    payment={"paid": False, "forma_pago": "002",
                             "forma_pago_nombre": "Transferencia"})
    result = create_albaran_for_order(db, fake, order, ejercicio="2026")
    assert result["status"] == "created" and result["numero"] == "5-500004"

    (header,) = _written(fake, "F_ALB")
    assert header["TIPALB"] == "5" and header["CODALB"] == 500004
    assert isinstance(header["CODALB"], int)
    assert header["ESTALB"] == 0                       # NO hereda ESTPRE=1
    assert header["CNOALB"] == "DUPLICODER, S.L." and header["CLIALB"] == 2458
    assert header["TOTALB"] == 186.34 and header["FOPALB"] == "002"
    assert "USUALB" not in header and "IMPALB" not in header
    assert header.get("PEDALB", "") == ""              # sin documento padre
    lineas = _written(fake, "F_LAL")
    assert len(lineas) == 2                            # la de la serie 2 no
    for ln in lineas:
        assert ln["CODLAL"] == 500004 and isinstance(ln["CODLAL"], int)
        assert (ln["DOCLAL"], ln["DTPLAL"], ln["DCOLAL"]) == ("P", "5", 27)
    assert lineas[1]["ARTLAL"] == "" and lineas[1]["DESLAL"] == "Portes"
    # El presupuesto queda «Aceptado» (ya lo estaba: idempotente, sin reescribir).
    assert result["origin_marked"] is True
    # Nº guardado en el pedido + historial + traza en packing.
    db.refresh(order)
    assert order.factusol_albaran_number == "5-500004"
    packing = json.loads(order.packing_json)
    assert packing["factusol_albaran"]["numero"] == "5-500004"
    assert packing["factusol_albaran"]["source"]["codigo"] == 27
    assert any("Albarán FACTUSOL 5-500004 creado" in r for r in _reasons(db, order))
    # Nada de facturas ni cobros: solo el albarán.
    assert _written(fake, "F_FAC") == [] and _written(fake, "F_LCO") == []


def test_convertir_pedido_noweb_crea_albaran(db) -> None:
    """Pedido de cliente MANUAL (REFPCL sin patrón de tienda) → albarán con
    enlace `C/5/123`; `PENPCL`/`PENLPC` (sin equivalente en F_ALB/F_LAL) se
    descartan; ESTPCL=0 no viaja (ESTALB=0 fijado); el F_PCL NO se marca
    (el valor «con albarán» de ESTPCL no está confirmado)."""
    fake = _client()
    order = _import(db, fake, "pedidos", 5, 123)
    assert web_pedido_reason(db, PEDIDO_MANUAL) is None
    result = create_albaran_for_order(db, fake, order, ejercicio="2026")
    assert result["status"] == "created" and result["numero"] == "5-500004"
    (header,) = _written(fake, "F_ALB")
    assert header["REFALB"] == "ENCARGO TALLER 7" and header["FOPALB"] == "011"
    assert header["CODALB"] == 500004 and header["ESTALB"] == 0
    assert "PENALB" not in header
    (linea,) = _written(fake, "F_LAL")
    assert (linea["DOCLAL"], linea["DTPLAL"], linea["DCOLAL"]) == ("C", "5", 123)
    assert "PENLAL" not in linea and linea["ARTLAL"] == "CDR80WPT"
    assert result["origin_marked"] is False
    assert "no está confirmado" in (result["origin_mark_warning"] or "")
    assert fake.updated == []                          # F_PCL intacto
    db.refresh(order)
    assert order.factusol_albaran_number == "5-500004"


def test_convertir_pedido_web_NO_crea_albaran(db, http) -> None:
    """Guardarraíl web: (1) un pedido de origen `woocommerce` nunca genera
    albarán en BoHub (lo crea WooCommerce); (2) un F_PCL que es un pedido web
    (REFPCL `BOP-099917` = el BOPRIN-99917 de BoHub) tampoco, ni desde el
    explorador. Nada se escribe."""
    woo = Order(external_source=OrderSource.WOOCOMMERCE, external_id="99917",
                order_number="BOPRIN-99917", company_id="dupli", total_amount=99)
    db.add(woo)
    db.commit()
    fake = _client()
    # (1) pedido web de BoHub.
    assert albaran_blocker(woo) == (
        "web_order_no_albaran",
        "Los pedidos web no generan albarán en BoHub: lo crea WooCommerce.",
    )
    with pytest.raises(WebOrderNoAlbaran):
        create_albaran_for_order(db, fake, woo, ejercicio="2026")
    r = http.post(f"/api/erp/orders/{woo.id}/albaran", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "web_order_no_albaran"
    # (2) el F_PCL del pedido web, importado como pedido de BoHub.
    reason = web_pedido_reason(db, PEDIDO_WEB)
    assert reason and "BOPRIN-99917" in reason
    imported = _import(db, fake, "pedidos", 5, 5)
    with pytest.raises(FactusolError, match="WooCommerce"):
        create_albaran_for_order(db, fake, imported, ejercicio="2026")
    db.refresh(imported)
    assert imported.factusol_albaran_number is None
    # (3) tampoco desde el explorador de documentos (409 antes de encolar).
    with _patched(fake):
        r2 = http.post("/api/erp/factusol/documents/pedidos/5/5/convert",
                       json={"target": "albaranes"},
                       headers=auth_headers(http, "pedidos"))
    assert r2.status_code == 409, r2.text
    assert r2.json()["detail"]["code"] == "web_pedido_no_albaran"
    assert fake.written == []


def test_albaran_idempotente(db) -> None:
    """Ni dos albaranes del mismo pedido ni uno nuevo si FACTUSOL ya tiene el
    suyo: (a) segunda llamada → `already`, una sola escritura; (b) pedido sin
    nº pero con albarán enlazado por `DOC/DTP/DCO` en FACTUSOL → se vincula
    ese (`linked`) sin escribir."""
    fake = _client()
    order = _import(db, fake, "presupuestos", 5, 27)
    first = create_albaran_for_order(db, fake, order, ejercicio="2026")
    again = create_albaran_for_order(db, fake, order, ejercicio="2026")
    assert first["status"] == "created" and again["status"] == "already"
    assert again["numero"] == "5-500004"
    assert len(_written(fake, "F_ALB")) == 1

    # (b) otro pedido (otro presupuesto) cuyo albarán ya existe en FACTUSOL.
    tables = _tables()
    tables["F_PRE"].append({**PRESUPUESTO, "CODPRE": 30, "REFPRE": "Obra Y"})
    tables["F_LPS"].append({**LINEAS_LPS[0], "CODLPS": 30})
    tables["F_ALB"].append(_live_alb_row(500009, "5", CLIALB=2458))
    tables["F_LAL"].append(_live_lal_row(500009, "5", DOCLAL="P", DTPLAL="5",
                                         DCOLAL=30))
    fake2 = _client(tables)
    order2 = _import(db, fake2, "presupuestos", 5, 30)
    linked = create_albaran_for_order(db, fake2, order2, ejercicio="2026")
    assert linked["status"] == "linked" and linked["numero"] == "5-500009"
    assert fake2.written == []
    db.refresh(order2)
    assert order2.factusol_albaran_number == "5-500009"
    assert any("ya existía" in r for r in _reasons(db, order2))


# --- B) pago, opción B ---------------------------------------------------------------


def test_convertir_pagado_B_no_emite_factura_apunta_pago(db, http) -> None:
    """«Pagado» al convertir: NO se emite factura ni se escribe F_LCO. El pedido
    queda `paid` con la intención de cobro (contrapartida resuelta contra el
    catálogo, fecha) y el albarán encolado; el cobro espera a la factura."""
    fake = _client()
    with (
        _patched(fake),
        patch("app.integrations.factusol.jobs.enqueue_create_order_albaran",
              return_value="job-alb-1") as enq,
    ):
        r = http.post("/api/erp/orders/from-factusol", json={
            "doc_type": "presupuestos", "serie": 5, "codigo": 27,
            "payment": {"paid": True, "forma_pago": "002",
                        "forma_pago_nombre": "Transferencia",
                        "contrapartida": "Streamtec (Sabadell)",
                        "fecha": "11/09/2026"},
        }, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["payment_status"] == "paid"
    assert body["invoice_status"] == "not_invoiced"
    assert body["factusol_invoice_number"] is None
    assert body["albaran_job_id"] == "job-alb-1"
    enq.assert_called_once_with(body["id"], enq.call_args.args[1])
    fp = body["factusol_payment"]
    assert fp["paid"] is True and fp["contrapartida"] == "8"
    assert fp["contrapartida_nombre"] == "Streamtec Sabadell"
    assert fp["fecha"] == "2026-09-11" and fp["cobro"] is None
    assert fp["forma_pago_nombre"] == "Transferencia"
    # Nada escrito en FACTUSOL desde la API: ni factura ni cobro ni albarán.
    assert fake.written == []
    with db as s:
        order = s.get(Order, body["id"])
        assert pending_collection(order) is not None
        assert any("Pago confirmado al convertir" in x for x in _reasons(s, order))
    # Sin cuenta conocida no se apunta nada (400) y sin cuenta con `paid` → 422.
    with _patched(_client()):
        bad = http.post("/api/erp/orders/from-factusol", json={
            "doc_type": "pedidos", "serie": 5, "codigo": 123,
            "payment": {"paid": True, "contrapartida": "Banco Inventado"},
        }, headers=auth_headers(http, "pedidos"))
        assert bad.status_code == 400 and bad.json()["detail"]["code"] == "unknown_account"
        bad2 = http.post("/api/erp/orders/from-factusol", json={
            "doc_type": "pedidos", "serie": 5, "codigo": 123,
            "payment": {"paid": True},
        }, headers=auth_headers(http, "pedidos"))
        assert bad2.status_code == 422
    with db as s:
        assert s.scalar(select(Order).where(Order.external_id == "5-000123")) is None


def test_cobro_se_registra_al_emitir_factura(db) -> None:
    """Con el pago apuntado (B) y el albarán creado, «Emitir factura» desde la
    ficha factura ESE albarán (cadena E3-B) y, al existir la factura, registra
    el cobro F-4-B tal cual: una línea en F_LCO copiada de la fila real (misma
    serie/contrapartida), importe = total, y `ESTFAC=2`. El pedido queda
    vinculado a la factura y el cobro anotado; una segunda emisión no duplica."""
    fake = _client()
    order = _import(db, fake, "presupuestos", 5, 27, payment={
        "paid": True, "forma_pago": "002", "contrapartida": "8",
        "fecha": "2026-09-11",
    })
    create_albaran_for_order(db, fake, order, ejercicio="2026")
    assert _written(fake, "F_FAC") == []                # B: sin factura aún

    result = emit_invoice(db, order.id, fake)
    assert result["from_albaran"] == "5-500004"
    assert result["numero"] == "5-000001" and result["codfac"] == "1"
    (factura,) = _written(fake, "F_FAC")
    assert factura["TIPFAC"] == "5" and factura["CODFAC"] == 1
    assert factura["ESTFAC"] == 0                       # nace pendiente de cobro
    assert factura["CNOFAC"] == "DUPLICODER, S.L." and factura["TOTFAC"] == 186.34
    lfa = _written(fake, "F_LFA")
    assert len(lfa) == 2 and all(
        (ln["DOCLFA"], ln["DTPLFA"], ln["DCOLFA"]) == ("A", "5", 500004) for ln in lfa
    )
    # El cobro F-4-B: SOLO F_LCO, copiando la fila real y sobrescribiendo lo mínimo.
    (cobro,) = _written(fake, "F_LCO")
    assert cobro["TFALCO"] == "5" and cobro["CFALCO"] == 1 and cobro["LINLCO"] == 1
    assert cobro["IMPLCO"] == 186.34 and cobro["CPALCO"] == 8
    assert cobro["FECLCO"] == "2026-09-11T00:00:00"
    assert cobro["CPTLCO"] == "COBRO FACTURA Nº: 5 - 1"
    assert cobro["FPALCO"] == "" and cobro["MULLCO"] == 51   # heredados
    assert ("F_FAC", {"TIPFAC": 5, "CODFAC": 1, "ESTFAC": "2"}) in fake.updated
    # El albarán queda «Facturado» y el pedido vinculado con su cobro anotado.
    assert ("F_ALB", {"TIPALB": 5, "CODALB": 500004, "ESTALB": "1"}) in fake.updated
    db.refresh(order)
    assert order.factusol_invoice_number == "1"
    assert order.invoice_status.value == "invoiced_by_erp"
    fp = payment_intent(order)
    assert fp["cobro"]["registered"] is True and fp["cobro"]["numero"] == "5-000001"
    assert fp["cobro"]["linlco"] == 1 and fp["cobro"]["importe"] == 186.34
    assert pending_collection(order) is None
    assert result["cobro"]["registered"] is True
    assert any("Cobro de 186.34 € registrado" in r for r in _reasons(db, order))
    # Segunda emisión: ya tiene factura.
    with pytest.raises(FactusolError, match="ya tiene factura"):
        emit_invoice(db, order.id, fake)
    assert len(_written(fake, "F_LCO")) == 1


def test_cobro_se_registra_al_facturar_albaran_desde_explorador(db) -> None:
    """La otra vía a la factura: `albaranes → facturas` desde ERP · Documentos.
    La cadena localiza el pedido por su nº de albarán, lo vincula y registra
    el cobro apuntado (mismo motor F-4-B)."""
    fake = _client()
    order = _import(db, fake, "presupuestos", 5, 27, payment={
        "paid": True, "contrapartida": "Streamtec (Sabadell)", "fecha": "2026-09-11",
    })
    create_albaran_for_order(db, fake, order, ejercicio="2026")
    result = convert_document(
        db, fake, source_type="albaranes", target_type="facturas",
        tip=5, cod=500004, ejercicio="2026",
    )
    assert result["numero"] == "5-000001"
    assert result["order"]["order_number"] == order.order_number
    assert result["order"]["linked"] is True
    assert result["order"]["cobro"]["registered"] is True
    assert len(_written(fake, "F_LCO")) == 1
    db.refresh(order)
    assert order.factusol_invoice_number == "1"
    assert pending_collection(order) is None


def test_convertir_sin_pago_no_cobra(db) -> None:
    """«Sin pago»: se apunta solo la forma de pago; el pedido sigue pendiente
    de pago, sin intención de cobro. Al emitir la factura NO se escribe F_LCO
    ni se marca cobrada."""
    fake = _client()
    order = _import(db, fake, "presupuestos", 5, 27, payment={
        "paid": False, "forma_pago": "002", "forma_pago_nombre": "Transferencia",
    })
    db.refresh(order)
    assert order.payment_status.value == "pending"
    fp = payment_intent(order)
    assert fp["paid"] is False and fp["forma_pago"] == "002"
    assert fp["contrapartida"] is None and pending_collection(order) is None
    assert any("Sin pago al convertir" in r for r in _reasons(db, order))
    create_albaran_for_order(db, fake, order, ejercicio="2026")
    (header,) = _written(fake, "F_ALB")
    assert header["FOPALB"] == "002"
    result = emit_invoice(db, order.id, fake)
    assert result["cobro"] is None
    assert _written(fake, "F_LCO") == []
    assert ("F_FAC", {"TIPFAC": 5, "CODFAC": 1, "ESTFAC": "2"}) not in fake.updated
    db.refresh(order)
    assert order.payment_status.value == "pending"
    assert order.factusol_invoice_number == "1"


# --- guard de esquema -------------------------------------------------------------------


def test_guard_esquema_albaran_no_cuadra_no_escribe(db) -> None:
    """El chequeo del dry-run corre ANTES de escribir: si la fila real de F_ALB
    trae un tipo distinto al del registro (aquí CLIALB como texto), no se
    escribe NADA, el error nombra la columna y el pedido se queda sin nº."""
    tables = _tables()
    tables["F_ALB"] = [_live_alb_row(500003, "5", CLIALB="2458")]   # str, no int
    fake = _client(tables)
    order = _import(db, fake, "presupuestos", 5, 27)
    with pytest.raises(FactusolError, match="no cuadra") as exc:
        create_albaran_for_order(db, fake, order, ejercicio="2026")
    assert "CLIALB" in str(exc.value) and "No se ha escrito nada" in str(exc.value)
    assert fake.written == [] and fake.updated == []
    db.refresh(order)
    assert order.factusol_albaran_number is None
    # Columna fuera de las vivas: tampoco (la allowlist ya la habría quitado,
    # pero el guard lo dice igualmente si algo se cuela).
    from app.integrations.factusol.chain import schema_problems
    from app.integrations.factusol.documents import DOC_SPECS

    problems = schema_problems(
        _client(), dst=DOC_SPECS["albaranes"],
        cabecera={"TIPALB": "5", "CODALB": 500004, "INVENTADA": 1},
        lineas=[{"TIPLAL": "5", "CODLAL": 500004, "CANLAL": "2"}],
        serie=5, ejercicio="2026",
    )
    assert any("INVENTADA" in p for p in problems)
    assert any("F_LAL.CANLAL" in p for p in problems)


# --- endpoints: albarán bajo demanda + proforma → pedido con pago -------------------


def test_endpoint_albaran_reintento_y_estado(db, http) -> None:
    """`POST /orders/{id}/albaran` reencola (202); 409 si ya tiene albarán o si
    el pedido no procede de un documento FACTUSOL; la bandeja y la ficha
    exponen el nº."""
    fake = _client()
    order = _import(db, fake, "presupuestos", 5, 27)
    manual = Order(external_source=OrderSource.MANUAL, order_number="MANUAL-000001",
                   company_id="dupli", total_amount=1)
    db.add(manual)
    db.commit()
    headers = auth_headers(http, "pedidos")
    with patch("app.integrations.factusol.jobs.enqueue_create_order_albaran",
               return_value="job-alb-9") as enq:
        r = http.post(f"/api/erp/orders/{order.id}/albaran", headers=headers)
    assert r.status_code == 202 and r.json() == {
        "job_id": "job-alb-9", "order_id": order.id, "status": "queued",
    }
    enq.assert_called_once()
    r2 = http.post(f"/api/erp/orders/{manual.id}/albaran", headers=headers)
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "albaran_not_applicable"
    create_albaran_for_order(db, fake, order, ejercicio="2026")
    r3 = http.post(f"/api/erp/orders/{order.id}/albaran", headers=headers)
    assert r3.status_code == 409
    assert r3.json()["detail"]["code"] == "already_has_albaran"
    detail = http.get(f"/api/erp/orders/{order.id}", headers=headers).json()
    assert detail["factusol_albaran_number"] == "5-500004"
    bandeja = http.get("/api/erp/orders", headers=headers).json()["items"]
    assert {it["order_number"]: it["factusol_albaran_number"] for it in bandeja} == {
        "PRO-000027": "5-500004", "MANUAL-000001": None,
    }
    # Solo lectura no puede.
    assert http.post(f"/api/erp/orders/{order.id}/albaran",
                     headers=auth_headers(http, "user")).status_code in (401, 403)


def test_convertir_proforma_endpoint_lleva_pago_y_albaran(db, http) -> None:
    """«Convertir en pedido» (ficha de empresa): el paso de pago se valida en
    el endpoint y viaja resuelto al job del worker serial junto con
    `create_albaran`."""
    _ = db
    with patch("app.integrations.factusol.jobs.enqueue_convert_quote_to_order",
               return_value="job-q1") as enq:
        r = http.post("/api/erp/factusol/quotes/574/convert-to-order", json={
            "payment": {"paid": True, "forma_pago": "002",
                        "contrapartida": "Bomedia (Sabadell)", "fecha": "2026-09-11"},
        }, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 202, r.text
    args, kwargs = enq.call_args
    assert args[0] == "574"
    assert kwargs["payment"]["paid"] is True and kwargs["payment"]["contrapartida"] == "6"
    assert kwargs["payment"]["fecha"] == "2026-09-11" and kwargs["create_albaran"] is True
    # Sin cuerpo (como antes de la Fase 2): sin pago, con albarán.
    with patch("app.integrations.factusol.jobs.enqueue_convert_quote_to_order",
               return_value="job-q2") as enq2:
        r2 = http.post("/api/erp/factusol/quotes/574/convert-to-order",
                       headers=auth_headers(http, "pedidos"))
    assert r2.status_code == 202
    assert enq2.call_args.kwargs == {"payment": None, "create_albaran": True}
    bad = http.post("/api/erp/factusol/quotes/574/convert-to-order", json={
        "payment": {"paid": True, "contrapartida": "Cuenta Inventada"},
    }, headers=auth_headers(http, "pedidos"))
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "unknown_account"


def test_job_proforma_a_pedido_crea_albaran_y_apunta_pago(db) -> None:
    """El job de «Convertir en pedido» (worker serial) encadena: pedido →
    pago apuntado → albarán; el resultado lleva el nº para la UI. Idempotente
    al repetirse (mismo pedido, `already`)."""
    from app.erp.factusol_albaran import apply_conversion_extras
    from app.integrations.factusol.quotes import convert_quote_to_order

    fake = _client()
    first = convert_quote_to_order(fake, db, "27", ejercicio="2026")
    # `convert_quote_to_order` asume TIPPRE=1 (proformas de BoHub); el fixture
    # es de la serie 5: se simula la fuente como la deja Fase 1.
    order = db.get(Order, first["order_id"])
    packing = json.loads(order.packing_json)
    packing["factusol_source"]["serie"] = 5
    order.packing_json = json.dumps(packing)
    db.commit()
    payment = resolve_payment(db, PaymentIn(paid=True, contrapartida="8", fecha="2026-09-11"))
    extra = apply_conversion_extras(
        db, fake, order_id=order.id, payment=payment, create_albaran=True,
        ejercicio="2026",
    )
    assert extra["albaran"]["status"] == "created"
    assert extra["albaran"]["numero"] == "5-500004"
    assert extra["payment"]["paid"] is True and extra["albaran_error"] is None
    again = apply_conversion_extras(
        db, fake, order_id=order.id, payment=payment, create_albaran=True,
        ejercicio="2026",
    )
    assert again["albaran"]["status"] == "already"
    assert len(_written(fake, "F_ALB")) == 1
    assert _written(fake, "F_FAC") == [] and _written(fake, "F_LCO") == []
