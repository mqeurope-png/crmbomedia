"""ERP · PORTES como línea aparte en pedidos manuales + IVA real en el PDF.

Parte A — los portes se meten en el alta manual como su PROPIA línea del
pedido (`is_shipping`), igual que los pedidos web los llevan aparte de la
mercancía; al emitir van a la banda de portes de la CABECERA del documento
(`IPOR1ALB`, donde los deja la app Woo→FACTUSOL y de donde los lee el PDF),
nunca como línea de `F_LAL`.

Parte B — el % de IVA de la línea de portes del PDF sale de su banda solo si
esa banda lleva IVA de verdad: en una entrega intracomunitaria / exportación
(banda al 21 % con importe 0,00 — factura real 2-526075) ya no se rotula
«Portes · IVA 21 %».
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order
from app.integrations.factusol.albaran_manual import (
    order_lines_for_document,
    order_portes_amount,
)
from app.integrations.factusol.jobs import create_order_albaran_job
from app.integrations.factusol.quotes import build_quote_payload
from app.integrations.factusol.vat_regime import (
    REGIME_EXPORTACION,
    REGIME_INTRACOMUNITARIO,
    REGIME_NACIONAL,
)
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_albaran_manual import CLIENTE, AlbaranFakeClient, _client, _manual
from tests.test_erp_albaran_manual import _tables as _albaran_tables
from tests.test_factusol_pdf import _header, _linea, _pdf, _texto

CLI_BE = {**CLIENTE, "CODCLI": 3392, "NIFCLI": "BE0812240188",
          "NOFCLI": "SPRL Clossetcadeaux", "NOCCLI": "Clossetcadeaux",
          "PAICLI": "056", "IFICLI": 2, "IVACLI": 2, "TIVCLI": 4}


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
        seed.add_all([
            Company(id="dupli", name="Duplicoder", factusol_company_id="2458",
                    country="ES", tax_id="B12345678"),
            Company(id="be", name="SPRL Clossetcadeaux", factusol_company_id="3392",
                    country="BE", vat="BE0812240188", tax_id="BE0812240188"),
        ])
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


def _albaran_client() -> AlbaranFakeClient:
    tables = _albaran_tables()
    tables["F_CLI"] = [dict(CLIENTE), dict(CLI_BE)]
    return _client(tables)


def _run_job(engine, fake: AlbaranFakeClient, oid: str):
    with patch("app.integrations.factusol.client.FactusolClient.from_settings",
               return_value=fake), \
         patch("app.db.session.get_engine", return_value=engine):
        return create_order_albaran_job(oid, actor_user_id=None)


def _written(fake: AlbaranFakeClient, tabla: str) -> list[dict[str, Any]]:
    return [rec for t, rec in fake.written if t == tabla]


# --- Parte A · alta manual --------------------------------------------------


def test_alta_pedido_portes_linea_aparte(http, session_factory) -> None:
    """Los portes se crean como su PROPIA línea del pedido (marcada
    `is_shipping`), no mezclados en la línea de mercancía; el total del pedido
    los incluye y la ficha los devuelve marcados."""
    r = http.post("/api/erp/orders", json={
        "company_id": "dupli",
        "lines": [
            {"product_sku": "Ink500mlCY", "description": "Tinta cyan",
             "quantity": 2, "unit_price": 40.0},
            {"product_sku": "", "description": "Portes", "quantity": 1,
             "unit_price": 19.0, "is_shipping": True},
        ],
    }, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    body = r.json()
    lines = body["lines"]
    assert len(lines) == 2
    mercancia, portes = lines[0], lines[1]
    assert mercancia["description"] == "Tinta cyan"
    assert mercancia["is_shipping"] is False
    assert mercancia["line_total"] == 80.0      # la mercancía NO lleva portes
    assert portes["description"] == "Portes"
    assert portes["is_shipping"] is True
    assert portes["line_total"] == 19.0
    # `total_amount` (con IVA) incluye los portes: (80 + 19) × 1,21.
    assert body["total_amount"] == pytest.approx(119.79)

    with session_factory() as s:
        order = s.get(Order, body["id"])
        # El builder del documento separa mercancía (líneas) y portes (banda).
        assert [ln["description"] for ln in order_lines_for_document(order)] == [
            "Tinta cyan",
        ]
        assert order_portes_amount(order) == 19.0


def test_alta_pedido_sin_portes_no_crea_linea(http) -> None:
    """Sin portes, el alta sigue exactamente igual que antes: una sola línea."""
    r = http.post("/api/erp/orders", json={
        "company_id": "dupli",
        "lines": [{"product_sku": "", "description": "Reparación", "quantity": 1,
                   "unit_price": 90.0}],
    }, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    assert [ln["is_shipping"] for ln in r.json()["lines"]] == [False]


# --- Parte A/B · emisión: los portes siguen el régimen ----------------------


def test_emision_portes_sigue_regimen(session_factory, engine) -> None:
    """Los portes van a la banda de portes de la cabecera (`IPOR1ALB`), NO a
    una línea de F_LAL, y llevan el IVA de esa banda: 21 % en un cliente
    nacional, 0 % en uno intracomunitario / de exportación (Tarea C)."""
    with session_factory() as s:
        _manual(s, "o-es", "MANUAL-000030", [
            {"sku": "Ink500mlCY", "desc": "Tinta", "qty": 2, "price": 40.0},
            {"sku": "", "desc": "Portes", "qty": 1, "price": 19.0},
        ], company_id="dupli")
        s.commit()
        # La última línea es la de portes.
        order = s.get(Order, "o-es")
        max(order.lines, key=lambda ln: ln.position).is_shipping = True
        s.commit()

    fake = _albaran_client()
    result = _run_job(engine, fake, "o-es")
    assert result["status"] == "created" and result["portes"] == 19.0
    assert result["regime"] == REGIME_NACIONAL
    (cab,) = _written(fake, "F_ALB")
    assert cab["IPOR1ALB"] == 19.0                 # portes en la CABECERA
    assert cab["NET1ALB"] == 80.0                  # neto = solo mercancía
    assert cab["BAS1ALB"] == 99.0                  # base = neto + portes
    assert cab["PIVA1ALB"] == 21.0
    assert cab["IIVA1ALB"] == 20.79                # 99 × 21 %
    assert cab["TOTALB"] == 119.79
    lineas = _written(fake, "F_LAL")
    assert len(lineas) == 1                        # los portes NO son línea
    assert lineas[0]["DESLAL"] == "Tinta"

    # Intracomunitario: misma banda de portes, sin IVA.
    with session_factory() as s:
        _manual(s, "o-be", "MANUAL-000031", [
            {"sku": "Ink500mlCY", "desc": "Tinta", "qty": 2, "price": 40.0},
            {"sku": "", "desc": "Portes", "qty": 1, "price": 19.0},
        ], company_id="be")
        s.commit()
        order = s.get(Order, "o-be")
        max(order.lines, key=lambda ln: ln.position).is_shipping = True
        s.commit()

    fake_be = _albaran_client()
    result_be = _run_job(engine, fake_be, "o-be")
    assert result_be["regime"] == REGIME_INTRACOMUNITARIO
    (cab_be,) = _written(fake_be, "F_ALB")
    assert cab_be["IPOR1ALB"] == 19.0
    assert cab_be["BAS1ALB"] == 99.0
    assert cab_be["PIVA1ALB"] == 0.0 and cab_be["IIVA1ALB"] == 0.0
    assert cab_be["TOTALB"] == 99.0                # exento: total = base
    assert len(_written(fake_be, "F_LAL")) == 1


def test_build_quote_payload_sin_portes_no_añade_columnas() -> None:
    """Sin portes el registro sale idéntico al de siempre (ni `IPOR1PRE` ni
    `BAS1PRE`): una columna de más tumba el EscribirRegistro entero."""
    customer = {"codcli": "2458", "nombre": "X", "pais": "ES"}
    lines = [{"quantity": 2, "unit_price": 40.0, "iva_pct": 21.0}]
    sin = build_quote_payload("1", ejercicio="2026", customer=customer,
                              refpre="", lines=lines)
    assert "IPOR1PRE" not in sin and "BAS1PRE" not in sin
    assert sin["NET1PRE"] == 80.0 and sin["TOTPRE"] == 96.8

    con = build_quote_payload("1", ejercicio="2026", customer=customer,
                              refpre="", lines=lines, portes=19.0)
    assert con["IPOR1PRE"] == 19.0 and con["BAS1PRE"] == 99.0
    assert con["NET1PRE"] == 80.0 and con["TOTPRE"] == 119.79

    # Exportación: portes sin IVA, como el resto del documento.
    export = build_quote_payload(
        "1", ejercicio="2026", refpre="", lines=lines, portes=19.0,
        customer={**customer, "pais": "NO", "regime": REGIME_EXPORTACION},
    )
    assert export["IPOR1PRE"] == 19.0 and export["BAS1PRE"] == 99.0
    assert export["PIVA1PRE"] == 0.0 and export["IIVA1PRE"] == 0.0
    assert export["TOTPRE"] == 99.0


# --- Parte B · el PDF no rotula «IVA 21 %» en un documento exento ----------


def test_pdf_portes_no_muestra_21_en_intracomunitario() -> None:
    """Factura intracomunitaria real (2-526075): banda 1 al 21 % con IVA
    0,00. La línea de portes NO debe rotular «· IVA 21 %»."""
    header = _header(
        "facturas",
        # Entrega intracomunitaria: % de banda 21 pero IMPORTE de IVA 0.
        NET1FAC=363.0, BAS1FAC=382.0, PIVA1FAC=21, IIVA1FAC=0.0,
        IPOR1FAC=19.0, TOTFAC=382.0,
    )
    pdf, data = _pdf("facturas", header, [_linea("facturas", 1)])
    assert data["charges"] == [{"kind": "portes", "piva": 0.0, "amount": 19.0}]
    text = _texto(pdf)
    assert "Portes" in text and "19,00" in text     # la línea sigue saliendo
    assert "IVA 21" not in text                     # …pero sin el 21 % falso
    # Y el documento sigue cuadrando: base única = total (sin IVA).
    assert "382,00" in text


def test_pdf_portes_mantiene_el_iva_en_nacional() -> None:
    """Nacional (banda con IVA real): la línea de portes sigue rotulando su
    % como hasta ahora."""
    pdf, data = _pdf("facturas", _header("facturas", IPOR1FAC=7.0),
                     [_linea("facturas", 1)])
    assert data["charges"] == [{"kind": "portes", "piva": 21.0, "amount": 7.0}]
    assert "IVA 21" in _texto(pdf)


def test_pdf_portes_banda_exenta_no_rotula_iva() -> None:
    """Portes en la banda EXENTA (banda 4): tampoco rotulan porcentaje."""
    header = _header(
        "facturas", NET4FAC=100.0, BAS4FAC=100.0, IPOR4FAC=5.0,
    )
    _pdf_bytes, data = _pdf("facturas", header, [_linea("facturas", 1)])
    assert {"kind": "portes", "piva": 0.0, "amount": 5.0} in data["charges"]
    assert json.dumps(data["charges"])  # serializable (lo consume el job/PDF)
