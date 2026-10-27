"""«Crear pedido» desde un albarán o una proforma: primero el resumen, y el
pedido solo se crea al confirmar.

El resumen (`resumen` de la vista previa del documento y `convert-preview` de
la proforma) se calcula con el mismo planificador de líneas y el mismo
criterio de importe que la creación: estas pruebas comprueban que dice la
verdad (misma empresa, mismas líneas, mismo importe) y que mirarlo no crea
nada.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderLine
from app.integrations.factusol.quotes import convert_quote_to_order
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_pedido_desde_factusol import FakeClient, _alb, _lal, _lps, _pre

LARGA = ("Horno de secado DTF de 60 cm con bandeja extraíble, temporizador digital, "
         "termostato regulable hasta 180 grados, ventilación forzada y kit de "
         "instalación incluido, además de un año de garantía ampliada y la formación "
         "de puesta en marcha en las instalaciones del cliente por nuestro técnico")


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(Company(name="Acme SL", tax_id="B12345678", factusol_company_id="55555"))
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


def _fake() -> FakeClient:
    return FakeClient({
        "F_ALB": [_alb(77, serie="5", total=1597.2)],
        "F_LAL": [_lal(77, 1, serie="5", art="DM3H", desc=LARGA, cant=1, precio=1200.0),
                  _lal(77, 2, serie="5", art="FILM-30", desc="Film DTF 30 cm", cant=3,
                       precio=45.0, dto=10)],
        "F_PRE": [_pre(40, total=1597.2, ref="Kit DTF")],
        "F_LPS": [_lps(40, 1, art="DM3H", desc=LARGA, cant=1, precio=1200.0, iva=21),
                  _lps(40, 2, art="", desc="Nota: entrega en 15 días", cant=0, precio=0),
                  _lps(40, 3, art="FILM-30", desc="Film DTF 30 cm", cant=3, precio=45.0,
                       dto=10, iva=21)],
    })


def _patch(fake: Any):
    return patch("app.integrations.factusol.client.FactusolClient.from_settings",
                 return_value=fake)


def _contar(session_factory) -> int:
    with session_factory() as s:
        return int(s.scalar(select(func.count()).select_from(Order)) or 0)


def _lineas_pedido(session_factory, order_id: str) -> list[dict[str, Any]]:
    with session_factory() as s:
        return [{"description": ln.description, "quantity": float(ln.quantity),
                 "unit_price": float(ln.unit_price), "line_total": float(ln.line_total)}
                for ln in s.scalars(select(OrderLine).where(OrderLine.order_id == order_id)
                                    .order_by(OrderLine.position))]


def _lineas_resumen(resumen: dict[str, Any]) -> list[dict[str, Any]]:
    return [{k: (float(ln[k]) if k != "description" else ln[k])
             for k in ("description", "quantity", "unit_price", "line_total")}
            for ln in resumen["lineas"]]


def test_albaran_ver_el_resumen_no_crea_nada_y_confirmar_crea_uno_solo(http, session_factory):
    fake = _fake()
    h = auth_headers(http, "pedidos")
    params = {"doc_type": "albaranes", "serie": 5, "codigo": 77}
    with _patch(fake):
        r = http.get("/api/erp/orders/from-factusol/preview", params=params, headers=h)
    assert r.status_code == 200, r.text
    assert "resumen" in r.json()
    assert _contar(session_factory) == 0                      # mirar no crea nada

    body = {**params, "create_albaran": False}
    with _patch(fake):
        r1 = http.post("/api/erp/orders/from-factusol", headers=h, json=body)
        r2 = http.post("/api/erp/orders/from-factusol", headers=h, json=body)
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "already_imported"
    assert _contar(session_factory) == 1                      # uno y solo uno
    assert fake.writes == []


def test_el_resumen_del_albaran_cuadra_con_el_pedido_creado(http, session_factory):
    fake = _fake()
    h = auth_headers(http, "pedidos")
    params = {"doc_type": "albaranes", "serie": 5, "codigo": 77}
    with _patch(fake):
        resumen = http.get("/api/erp/orders/from-factusol/preview", params=params,
                           headers=h).json()["resumen"]
        creado = http.post("/api/erp/orders/from-factusol", headers=h,
                           json={**params, "create_albaran": False}).json()
    assert resumen["origen"]["numero"] == "5-000077" and resumen["origen"]["label"] == "albarán"
    assert resumen["company_id"] == creado["company_id"] and resumen["company_name"] == "Acme SL"
    assert resumen["fecha"].startswith("2026-09-05")
    assert creado["placed_at"].startswith("2026-09-05")
    # Mismas líneas (la descripción larga se reparte igual en las dos)…
    assert _lineas_resumen(resumen) == _lineas_pedido(session_factory, creado["id"])
    assert len(resumen["lineas"]) == 3
    # …y mismo importe.
    assert resumen["total"] == pytest.approx(creado["total_amount"])
    assert resumen["total"] == 1597.2


def test_el_resumen_de_convertir_la_proforma_cuadra_con_el_pedido(http, session_factory):
    fake = _fake()
    h = auth_headers(http, "pedidos")
    with _patch(fake):
        r = http.get("/api/erp/factusol/quotes/40/convert-preview", params={"serie": 1},
                     headers=h)
    assert r.status_code == 200, r.text
    resumen = r.json()
    assert resumen["already_existed"] is None
    assert resumen["origen"] == {"doc_type": "presupuestos", "label": "proforma", "serie": 1,
                                 "codigo": 40, "numero": "1-000040", "referencia": "Kit DTF"}
    assert _contar(session_factory) == 0

    with session_factory() as s:
        hecho = convert_quote_to_order(fake, s, "40", ejercicio="2026", serie=1)
    assert hecho["already_existed"] is False
    assert resumen["company_id"] == hecho["company_id"] and resumen["company_linked"] is True
    assert _lineas_resumen(resumen) == _lineas_pedido(session_factory, hecho["order_id"])
    assert resumen["total"] == pytest.approx(hecho["total"])

    # Ya convertida: el resumen lo dice y no ofrece otro pedido.
    with _patch(fake):
        otra = http.get("/api/erp/factusol/quotes/40/convert-preview", params={"serie": 1},
                        headers=h).json()
    assert otra["already_existed"]["order_id"] == hecho["order_id"]
    assert fake.writes == []
