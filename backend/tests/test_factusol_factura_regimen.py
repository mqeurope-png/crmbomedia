"""Tarea C · Parte 2 — los documentos que CALCULA BoHub respetan el régimen de
IVA del cliente: proformas (`build_quote_payload`) y albarán manual desde las
líneas (Tarea A) salen SIN IVA para intracomunitario / exportación y con IVA
para nacional; la conversión proforma → pedido respeta un 0 % explícito; y la
factura web (copia del F_PCL) avisa si el cliente no es nacional y el pedido
lleva IVA (no se recalculan importes).
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

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.api.factusol import _customer_from_company
from app.erp.models import Order, OrderSource, OrderStatusHistory
from app.erp.orders_from_factusol import build_order
from app.integrations.factusol.jobs import create_order_albaran_job
from app.integrations.factusol.quotes import build_quote_payload, header_says_no_iva
from app.integrations.factusol.service import emit_invoice, regime_warning_for_pcl
from app.integrations.factusol.vat_regime import (
    REGIME_EXPORTACION,
    REGIME_INTRACOMUNITARIO,
    REGIME_NACIONAL,
)
from app.main import app
from app.models.crm import Company
from tests._test_helpers import seed_test_users
from tests.test_erp_albaran_manual import CLIENTE, AlbaranFakeClient, _client, _manual
from tests.test_erp_albaran_manual import _tables as _albaran_tables
from tests.test_factusol_service import FakeFactusol, _pcl_row

LINES = [
    {"codart": "99cy", "description": "Tinta", "quantity": 2, "unit_price": 100.0,
     "discount_pct": 0, "iva_pct": 21.0},
    {"codart": "", "description": "Portes", "quantity": 1, "unit_price": 10.0,
     "discount_pct": 0, "iva_pct": 21.0},
]

CLI_3392_MAL = {**CLIENTE, "CODCLI": 3392, "NIFCLI": "BE0812240188",
                "NOFCLI": "SPRL Clossetcadeaux", "NOCCLI": "Clossetcadeaux",
                "PAICLI": "056", "IFICLI": 0, "IVACLI": 0, "TIVCLI": 1}
CLI_525_EXPORT = {**CLIENTE, "CODCLI": 525, "NIFCLI": "NO 976 029 100",
                  "NOFCLI": "Nasjonalbiblioteket", "NOCCLI": "Nasjonalbiblioteket",
                  "PAICLI": "578", "IFICLI": 0, "IVACLI": 3, "TIVCLI": 3}
CLI_2458_ES = {**CLIENTE, "IFICLI": 0, "IVACLI": 0, "TIVCLI": 1}


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
            # Sin país en el CRM: manda la ficha F_CLI (IVACLI=3, exportación).
            Company(id="no", name="Nasjonalbiblioteket", factusol_company_id="525",
                    tax_id="NO 976 029 100"),
            Company(id="no-crm", name="Noruega CRM", factusol_company_id="525",
                    country="NO", tax_id="NO 976 029 100"),
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
    tables["F_CLI"] = [dict(CLI_2458_ES), dict(CLI_3392_MAL), dict(CLI_525_EXPORT)]
    return _client(tables)


def _run_job(engine, fake: AlbaranFakeClient, oid: str):
    with patch("app.integrations.factusol.client.FactusolClient.from_settings",
               return_value=fake), \
         patch("app.db.session.get_engine", return_value=engine):
        return create_order_albaran_job(oid, actor_user_id=None)


def _header(fake: AlbaranFakeClient) -> dict[str, Any]:
    (cab,) = [rec for t, rec in fake.written if t == "F_ALB"]
    return cab


def _quote(customer: dict[str, Any]) -> dict[str, Any]:
    return build_quote_payload("1", ejercicio="2026", customer=customer, refpre="",
                               lines=LINES)


# --- proformas + albarán manual ---------------------------------------------


def test_emision_intracomunitario_sin_iva(session_factory, engine) -> None:
    """Cliente UE con NIF-IVA: la proforma que calcula BoHub sale con IVA 0 en
    los importes (banda 1 a 0 %, total = base) y país real; el albarán manual
    desde las líneas también, aunque la ficha F_CLI (mal configurada) diga
    nacional — y avisa para corregirla."""
    with session_factory() as s:
        customer = _customer_from_company(s, "be")
    assert customer["regime"] == REGIME_INTRACOMUNITARIO and customer["pais"] == "BE"
    payload = _quote(customer)
    assert payload["NET1PRE"] == 210.0
    assert payload["PIVA1PRE"] == 0.0 and payload["IIVA1PRE"] == 0.0
    assert payload["TOTPRE"] == 210.0
    assert payload["CPAPRE"] == "056"

    with session_factory() as s:
        _manual(s, "o-be", "MANUAL-000020", [
            {"sku": "Ink500mlCY", "desc": "Tinta", "qty": 2, "price": 100.0},
        ], company_id="be")
        s.commit()
    fake = _albaran_client()
    result = _run_job(engine, fake, "o-be")
    assert result["status"] == "created" and result["regime"] == REGIME_INTRACOMUNITARIO
    assert "está como Nacional (con IVA)" in result["regime_warning"]
    cab = _header(fake)
    assert cab["CLIALB"] == 3392 and cab["CPAALB"] == "056"
    assert cab["NET1ALB"] == 200.0 and cab["PIVA1ALB"] == 0.0
    assert cab["IIVA1ALB"] == 0.0 and cab["TOTALB"] == 200.0
    assert fake.updated == []                          # F_CLI no se toca desde aquí
    with session_factory() as s:
        block = json.loads(s.get(Order, "o-be").packing_json)["factusol_albaran"]
        assert block["regime"] == REGIME_INTRACOMUNITARIO
        assert "Corrige la ficha" in block["regime_warning"]


def test_emision_exportacion_sin_iva(session_factory, engine) -> None:
    """Fuera de la UE: proforma sin IVA con CPAPRE real (578); y el albarán
    manual de una empresa SIN país en el CRM toma el régimen de la ficha F_CLI
    (IVACLI=3 → exportación) sin aviso."""
    with session_factory() as s:
        customer = _customer_from_company(s, "no-crm")
    assert customer["regime"] == REGIME_EXPORTACION
    payload = _quote(customer)
    assert payload["PIVA1PRE"] == 0.0 and payload["IIVA1PRE"] == 0.0
    assert payload["TOTPRE"] == payload["NET1PRE"] == 210.0
    assert payload["CPAPRE"] == "578"

    with session_factory() as s:
        _manual(s, "o-no", "MANUAL-000021", [
            {"sku": "CDR80", "desc": "CD", "qty": 10, "price": 1.0},
        ], company_id="no")
        s.commit()
    fake = _albaran_client()
    result = _run_job(engine, fake, "o-no")
    assert result["status"] == "created" and result["regime"] == REGIME_EXPORTACION
    assert result["regime_warning"] is None
    cab = _header(fake)
    assert cab["NET1ALB"] == 10.0 and cab["PIVA1ALB"] == 0.0 and cab["TOTALB"] == 10.0


def test_emision_nacional_con_iva(session_factory, engine) -> None:
    """Cliente español: todo como antes — banda 1 al 21 % en la proforma y en
    el albarán manual (líneas al 21 % por defecto)."""
    with session_factory() as s:
        customer = _customer_from_company(s, "dupli")
    assert customer["regime"] == REGIME_NACIONAL
    payload = _quote(customer)
    assert payload["PIVA1PRE"] == 21.0 and payload["IIVA1PRE"] == 44.1
    assert payload["TOTPRE"] == 254.1 and payload["CPAPRE"] == "724"

    with session_factory() as s:
        _manual(s, "o-es", "MANUAL-000022", [
            {"sku": "Ink500mlCY", "desc": "Tinta", "qty": 2, "price": 40.0},
        ], company_id="dupli")
        s.commit()
    fake = _albaran_client()
    result = _run_job(engine, fake, "o-es")
    assert result["regime"] == REGIME_NACIONAL and result["regime_warning"] is None
    cab = _header(fake)
    assert cab["NET1ALB"] == 80.0 and cab["PIVA1ALB"] == 21.0
    assert cab["IIVA1ALB"] == 16.8 and cab["TOTALB"] == 96.8


def test_conversion_proforma_respeta_iva_cero_explicito(session_factory) -> None:
    """Fase 1: el 0 % marcado por la CABECERA (`iva_explicit`, proforma
    intracomunitaria / exportación) se guarda al 0 %; un 0 de línea a secas
    (`IVALPS` es un código) y la línea sin dato siguen cayendo al 21 %."""
    assert header_says_no_iva(0.0, 100.0) is True
    assert header_says_no_iva(0, 0) is False          # sin base no dice nada
    assert header_says_no_iva(None, 100.0) is False   # columna ausente ≠ 0 %
    assert header_says_no_iva(21.0, 100.0) is False
    with session_factory() as s:
        order = build_order(
            s, source=OrderSource.MANUAL, external_id="x", order_number="MANUAL-1",
            company_id="be", contact_id=None, placed_at=None, notes=None,
            packing_extra=None, actor_user_id=None, history_reason="test",
            lines=[
                {"codart": "a", "description": "cero explícito", "quantity": 1,
                 "unit_price": 100.0, "iva_pct": 0.0, "iva_explicit": True},
                {"codart": "b", "description": "código 0", "quantity": 1,
                 "unit_price": 100.0, "iva_pct": 0.0},
                {"codart": "c", "description": "sin dato", "quantity": 1,
                 "unit_price": 100.0, "iva_pct": None},
            ],
        )
        s.commit()
        rates = [float(ln.tax_rate) for ln in sorted(order.lines, key=lambda x: x.position)]
    assert rates == [0.0, 21.0, 21.0]


# --- factura web: aviso, sin recalcular --------------------------------------


def test_emision_web_avisa_iva_cliente_intracomunitario(session_factory) -> None:
    """La factura de un pedido web COPIA el F_PCL (lo que pagó el cliente); si
    la empresa es intracomunitaria / exportación y el pedido lleva IVA, se
    avisa en el resultado y en el historial. Nacional o sin IVA → sin aviso."""
    with session_factory() as s:
        be = Order(order_number="BOPRIN-99866", total_amount=186.34, company_id="be",
                   external_source=OrderSource.WOOCOMMERCE)
        es = Order(order_number="BOPRIN-99867", total_amount=186.34, company_id="dupli",
                   external_source=OrderSource.WOOCOMMERCE)
        s.add_all([be, es])
        s.commit()
        be_id, es_id = be.id, es.id
        warning = regime_warning_for_pcl(s, be, _pcl_row())
        assert warning and "Intracomunitario" in warning and "BOPRIN-99866" in warning
        assert regime_warning_for_pcl(s, es, _pcl_row()) is None
        assert regime_warning_for_pcl(
            s, be, _pcl_row(PIVA1PCL=0.0, IIVA1PCL=0.0, IIVA2PCL=0.0, IIVA3PCL=0.0),
        ) is None

    with session_factory() as s:
        client = FakeFactusol(pcl_row=_pcl_row(), lpc_rows=[], f_fac_last=526066)
        result = emit_invoice(s, be_id, client)
        assert result["codfac"] == "526067"
        assert "Intracomunitario" in result["regime_warning"]
        # Los importes se copian tal cual: NO se recalculan.
        (cab,) = [rec for t, rec in client.writes if t == "F_FAC"]
        assert cab["PIVA1FAC"] == 21.0 and cab["IIVA1FAC"] == 21.0
        hist = s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == be_id,
        )).all()
        assert any("regime_warning" in (h.metadata_json or "") for h in hist)

    with session_factory() as s:
        client = FakeFactusol(pcl_row=_pcl_row(), lpc_rows=[], f_fac_last=526066)
        result = emit_invoice(s, es_id, client)
        assert "regime_warning" not in result           # nacional: sin aviso
