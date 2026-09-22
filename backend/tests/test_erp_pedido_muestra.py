"""Pedido de MUESTRA / envío no facturable.

Un pedido de muestra se manda a un cliente o prospecto (o es una pieza de
cortesía) y su único objetivo es prepararlo y enviarlo: NO lleva empresa
vinculada a FACTUSOL, ni serie, ni NIF, y no pasa por facturación. Aquí se
fija ese contrato: el alta, la numeración propia, que entra directa a la Cola
SAT, que lo fiscal sale «No aplica» y se rechaza, que no aparece en las colas
de facturación, y quién puede crearla.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, PreparationStatus
from app.erp.sample_orders import ORDER_KIND_SAMPLE, is_sample_order
from app.erp.workflow import order_steps
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users

DIRECCION = {
    "address_line": "Calle Muestra 1", "city": "Barcelona",
    "postal_code": "08001", "country": "España",
}
LINEAS = [{"product_sku": "MBO-250", "description": "Cabezal de muestra",
           "quantity": 1, "unit_price": 0}]


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


def _crear(http, role: str = "comercial", **over):
    payload = {
        "recipient_name": "Prospecto SL", "shipping_address": DIRECCION,
        "reason": "muestra", "lines": LINEAS, **over,
    }
    return http.post("/api/erp/orders/sample", json=payload,
                     headers=auth_headers(http, role))


# --- alta ---------------------------------------------------------------------


def test_alta_sin_empresa_ni_serie_ni_factusol(http, session_factory):
    """El alta NO pide empresa vinculada, serie ni NIF: solo destinatario y qué
    se manda. Se guarda con su numeración propia y marcada como muestra."""
    r = _crear(http)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["order_number"].startswith("MUESTRA-")
    assert body["company_id"] is None
    assert body["order_kind"] == ORDER_KIND_SAMPLE

    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.order_number == body["order_number"]))
        assert is_sample_order(o)
        assert o.shipping_name == "Prospecto SL"
        assert o.company_id is None
        # No se ha elegido serie de empresa emisora: no se va a facturar.
        assert o.factusol_manual_serie is None
        assert "Calle Muestra 1" in (o.packing_json or "")


def test_numeracion_propia_y_correlativa(http):
    primera = _crear(http).json()["order_number"]
    segunda = _crear(http).json()["order_number"]
    assert primera == "MUESTRA-000001"
    assert segunda == "MUESTRA-000002"


def test_entra_directa_a_la_cola_sat(http, session_factory):
    """Sin puerta de facturación: al crearse ya está «en cola» para el taller."""
    body = _crear(http).json()
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.order_number == body["order_number"]))
        assert o.preparation_status == PreparationStatus.IN_QUEUE.value

    r = http.get("/api/erp/sat/queue", headers=auth_headers(http, "sat"))
    assert r.status_code == 200, r.text
    nums = {i["order_number"] for i in r.json()["preparing"]}
    assert body["order_number"] in nums


def test_exige_destinatario_y_direccion(http):
    """Es un ENVÍO: sin dirección no hay nada que preparar."""
    r = _crear(http, shipping_address={"country": "España"})
    assert r.status_code == 422
    r2 = http.post("/api/erp/orders/sample",
                   json={"recipient_name": "", "shipping_address": DIRECCION,
                         "lines": LINEAS},
                   headers=auth_headers(http, "comercial"))
    assert r2.status_code == 422


# --- nada fiscal ---------------------------------------------------------------


def test_los_pasos_fiscales_salen_no_aplica(http, session_factory):
    body = _crear(http).json()
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.order_number == body["order_number"]))
        estados = {st["key"]: st["state"] for st in order_steps(o)}
    assert estados["creado"] == "done"
    # «Aprobado» sí se cumple: la muestra entra a la Cola SAT al crearse.
    assert estados["aprobado"] == "done"
    for paso in ("pagado", "albaran", "factura", "cobro"):
        assert estados[paso] == "skipped", paso
    assert estados["factura_enviada"] == "skipped"


def test_no_deja_emitir_factura_ni_albaran(http):
    oid = _crear(http).json()["id"]
    admin = auth_headers(http, "admin")

    factura = http.post(f"/api/erp/orders/{oid}/emit-factusol-invoice", headers=admin)
    assert factura.status_code == 409, factura.text
    assert factura.json()["detail"]["code"] == "order_not_billable"

    albaran = http.post(f"/api/erp/orders/{oid}/albaran", headers=admin)
    assert albaran.status_code == 409, albaran.text
    assert albaran.json()["detail"]["code"] == "order_not_billable"


def test_no_entra_en_por_facturar_ni_por_cobrar(http):
    numero = _crear(http).json()["order_number"]
    admin = auth_headers(http, "admin")
    for cola in ("por_facturar", "por_cobrar"):
        r = http.get("/api/erp/orders", params={"queue": cola}, headers=admin)
        assert r.status_code == 200, r.text
        assert numero not in {o["order_number"] for o in r.json()["items"]}, cola
    # Sí está en «por enviar»: es lo único que queda por hacer.
    r = http.get("/api/erp/orders", params={"queue": "por_enviar"}, headers=admin)
    assert numero in {o["order_number"] for o in r.json()["items"]}


def test_aparece_en_seguimiento_como_no_facturable(http):
    numero = _crear(http).json()["order_number"]
    r = http.get("/api/erp/seguimiento", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    fila = next((i for i in r.json()["items"] if i["order_number"] == numero), None)
    assert fila is not None, "la muestra debe verse en el seguimiento"
    assert fila["factura"] == "No aplica"
    assert fila["cobro_label"] == "—"          # sin factura → sin cobro
    assert fila["origen_label"] == "Muestra"


# --- permisos ------------------------------------------------------------------


@pytest.mark.parametrize("role", ["comercial", "pedidos", "sat", "admin"])
def test_pueden_crear_comercial_pedidos_sat_y_admin(http, role):
    assert _crear(http, role=role).status_code == 201


@pytest.mark.parametrize("role", ["user", "viewer"])
def test_los_roles_de_solo_lectura_no_crean_muestras(http, role):
    assert _crear(http, role=role).status_code == 403


def test_el_comercial_ve_la_muestra_que_el_mismo_crea(http):
    """El filtro de pedidos WEB del Comercial no afecta a las muestras: no son
    pedidos web."""
    numero = _crear(http, role="comercial").json()["order_number"]
    r = http.get("/api/erp/orders", headers=auth_headers(http, "comercial"))
    assert numero in {o["order_number"] for o in r.json()["items"]}


# --- regresión -----------------------------------------------------------------


def test_un_pedido_normal_sigue_siendo_facturable(http, session_factory):
    """Los pedidos corrientes no cambian: sin `order_kind` siguen su ciclo."""
    with session_factory() as s:
        o = Order(order_number="MANUAL-000999", external_source="manual",
                  preparation_status="pending_review", payment_status="paid",
                  transport_status="not_shipped")
        s.add(o)
        s.commit()
        assert not is_sample_order(o)
        estados = {st["key"]: st["state"] for st in order_steps(o)}
    assert estados["factura"] != "skipped"
    assert estados["cobro"] != "skipped"
