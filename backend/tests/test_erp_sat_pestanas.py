"""Cola SAT por pasos del taller + «Sin envío» («No requiere envío»).

  Por embalar → En preparación → Embalados → Pendiente de recogida → Enviados
  «Todos pendientes» = las cuatro primeras · «Sin envío» aparte (NO es enviado).

El backend decide la pestaña de cada pedido y los contadores (que tienen que
cuadrar con lo que lista cada pestaña).
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderLine
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users


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
def client(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _order(s: Session, number: str, *, prep: str = "in_queue", transport: str = "not_shipped",
           genei: dict | None = None, **kw) -> str:
    o = Order(order_number=number, preparation_status=prep, payment_status="paid",
              transport_status=transport, **kw)
    if genei is not None:
        o.packing_json = json.dumps({"genei": genei})
    s.add(o)
    s.flush()
    s.add(OrderLine(order_id=o.id, product_sku="SKU", product_codart="A", description="Art",
                    quantity=1, unit_price=10, line_total=10))
    s.commit()
    return o.id


def _seed(s: Session) -> dict[str, str]:
    return {
        "cola": _order(s, "PE-1"),
        "bloqueado": _order(s, "PE-2", prep="blocked"),
        "preparando": _order(s, "EP-1", prep="preparing"),
        "embalado": _order(s, "EM-1", prep="packed"),
        # Envío Genei creado pero SIN pagar/tramitar (7): sigue en «Embalados».
        "genei_sin_pagar": _order(s, "EM-2", prep="packed", genei={
            "shipment_code": "G7", "state_code": 7, "state_bucket": "created"}),
        # Tramitado (Genei 1): etiqueta lista, esperando al transportista.
        "tramitado": _order(s, "PR-1", prep="packed", genei={
            "shipment_code": "G1", "state_code": 1, "state_bucket": "ready",
            "state_label": "Tramitado"}),
        # Etiqueta de otra agencia ya puesta (label_created).
        "etiqueta": _order(s, "PR-2", prep="packed", transport="label_created"),
        # Incidencia de transporte: ya salió y tiene un problema → sigue en
        # «Embalados» (y en «Incidencias»), nunca «Pendiente de recogida».
        "incidencia": _order(s, "EM-3", prep="packed", transport="incident", genei={
            "shipment_code": "G9", "state_code": 9, "state_bucket": "incident"}),
        "en_transito": _order(s, "EN-1", prep="packed", transport="in_transit",
                              tracking_number="1Z1"),
        "entregado": _order(s, "EN-2", prep="packed", transport="delivered"),
        "sin_envio": _order(s, "SE-1", shipping_not_required=True),
        # Marcado «No requiere envío» aunque el transporte diga enviado: manda
        # la marca (va a «Sin envío», no a «Enviados»).
        "sin_envio_embalado": _order(s, "SE-2", prep="packed", transport="in_transit",
                                     shipping_not_required=True),
        # No pasó por el taller (histórico externalizado): ni en «Enviados».
        "externo": _order(s, "EX-1", prep="already_completed_externally",
                          transport="already_shipped_externally"),
    }


def _numeros(items: list[dict]) -> set[str]:
    return {i["order_number"] for i in items}


def test_cada_pestana_su_subconjunto_y_los_contadores_cuadran(client, session_factory):
    with session_factory() as s:
        _seed(s)
    h = auth_headers(client, "sat")
    q = client.get("/api/erp/sat/queue", headers=h).json()
    assert _numeros(q["por_embalar"]) == {"PE-1", "PE-2"}
    assert _numeros(q["en_preparacion"]) == {"EP-1"}
    assert _numeros(q["embalados"]) == {"EM-1", "EM-2", "EM-3"}
    assert _numeros(q["pendiente_recogida"]) == {"PR-1", "PR-2"}
    # Bloqueados arriba dentro de «Por embalar».
    assert q["por_embalar"][0]["order_number"] == "PE-2"
    counts = q["counts"]
    assert counts == {
        "por_embalar": 2, "en_preparacion": 1, "embalados": 3, "pendiente_recogida": 2,
        "pendientes": 8, "sin_envio": 2, "enviados": 2,
    }
    # Cada item dice su pestaña.
    assert {i["sat_tab"] for i in q["pendiente_recogida"]} == {"pendiente_recogida"}
    # Las dos secciones de antes siguen (compatibilidad).
    assert _numeros(q["preparing"]) == {"PE-1", "PE-2", "EP-1"}
    assert _numeros(q["ready_for_pickup"]) == {"EM-1", "EM-2", "EM-3", "PR-1", "PR-2"}

    env = client.get("/api/erp/sat/shipped", headers=h).json()
    assert _numeros(env["items"]) == {"EN-1", "EN-2"}      # «Sin envío» NO
    assert env["total"] == counts["enviados"]
    sin = client.get("/api/erp/sat/shipped?sin_envio=true", headers=h).json()
    assert _numeros(sin["items"]) == {"SE-1", "SE-2"}
    assert sin["total"] == counts["sin_envio"]
    assert {i["sat_tab"] for i in sin["items"]} == {"sin_envio"}


def test_el_envio_genei_viaja_en_el_item(client, session_factory):
    """Con envío ya creado la card ofrece «Ver envío Genei» (no «Crear»), y la
    etiqueta solo cuando está tramitado."""
    with session_factory() as s:
        ids = _seed(s)
    h = auth_headers(client, "sat")
    q = client.get("/api/erp/sat/queue", headers=h).json()
    por_num = {i["order_number"]: i for i in [*q["embalados"], *q["pendiente_recogida"]]}
    assert por_num["EM-1"]["genei"] is None
    assert por_num["EM-2"]["genei"]["shipment_code"] == "G7"
    assert por_num["EM-2"]["genei"]["label_available"] is False
    assert por_num["PR-1"]["genei"]["label_available"] is True
    one = client.get(f"/api/erp/sat/orders/{ids['tramitado']}", headers=h).json()
    assert one["sat_tab"] == "pendiente_recogida"
    assert one["genei"]["state_label"] == "Tramitado"


def test_recogida_por_genei_pasa_de_pendiente_de_recogida_a_enviados(client, session_factory):
    """Tramitado (Genei 1) → «Pendiente de recogida»; la agencia recoge (Genei
    5, webhook o «Actualizar estado») → «Enviados»."""
    from app.erp.integrations.genei.webhook import apply_shipment_state

    with session_factory() as s:
        oid = _order(s, "G-1", prep="packed", genei={"shipment_code": "GX"})
        order = s.get(Order, oid)
        apply_shipment_state(s, order, {"shipmentCode": "GX", "estado": 1})
        s.commit()
    h = auth_headers(client, "sat")
    assert client.get(f"/api/erp/sat/orders/{oid}", headers=h).json()["sat_tab"] == \
        "pendiente_recogida"
    with session_factory() as s:
        order = s.get(Order, oid)
        apply_shipment_state(s, order, {"shipmentCode": "GX", "estado": 5,
                                        "codigo_seguimiento": "TRK-5"})
        s.commit()
    item = client.get(f"/api/erp/sat/orders/{oid}", headers=h).json()
    assert item["sat_tab"] == "enviados"
    assert item["transport_status"] == "in_transit"
    q = client.get("/api/erp/sat/queue", headers=h).json()
    assert oid not in {i["id"] for i in q["pendiente_recogida"]}
    assert oid in {i["id"] for i in client.get("/api/erp/sat/shipped", headers=h).json()["items"]}


def test_no_requiere_envio_va_a_sin_envio_y_no_cuenta_como_enviado(client, session_factory):
    """Marcar «No requiere envío» = el pedido NO se envía (recogida en tienda,
    licencia…): sale de los pendientes, va a «Sin envío» y NUNCA a «Enviados»;
    la hoja lo pinta «No aplica» (no «enviado»)."""
    with session_factory() as s:
        oid = _order(s, "PE-9", prep="packed")
    h = auth_headers(client, "sat")
    r = client.post("/api/erp/sat/bulk-no-shipping", headers=h,
                    json={"order_ids": [oid], "value": True})
    assert r.status_code == 200 and r.json()["changed"] == 1
    q = client.get("/api/erp/sat/queue", headers=h).json()
    assert oid not in {i["id"] for k in ("por_embalar", "en_preparacion", "embalados",
                                         "pendiente_recogida") for i in q[k]}
    assert q["counts"]["sin_envio"] == 1 and q["counts"]["enviados"] == 0
    assert client.get("/api/erp/sat/shipped", headers=h).json()["items"] == []
    sin = client.get("/api/erp/sat/shipped?sin_envio=true", headers=h).json()["items"]
    assert [i["sat_tab"] for i in sin if i["id"] == oid] == ["sin_envio"]

    fila = client.get("/api/erp/seguimiento?en_curso=false",
                      headers=auth_headers(client, "pedidos")).json()["items"]
    row = next(i for i in fila if i["order_number"] == "PE-9")
    assert row["envio"] == "No aplica"
    assert row["estado"] != "enviado"
    assert row["preparacion"] == "Listo"              # se llegó a embalar

    # Al completar no se echa en falta un envío que no hay.
    from app.erp.api.orders import completion_avisos

    with session_factory() as s:
        avisos = completion_avisos(s.get(Order, oid))
        assert "el envío no consta como enviado en BoHub" not in avisos

    # Reversible: vuelve a su pestaña según su preparación.
    client.post("/api/erp/sat/bulk-no-shipping", headers=h,
                json={"order_ids": [oid], "value": False})
    q = client.get("/api/erp/sat/queue", headers=h).json()
    assert oid in {i["id"] for i in q["embalados"]}


def test_los_marcados_con_la_semantica_de_487_pasan_a_sin_envio_sin_migrar(session_factory):
    """#487 usó la MISMA marca para «enviado sin seguimiento». Sin tocar datos,
    esos pedidos (y los «No requiere envío» de siempre) son «Sin envío»."""
    from app.erp import seguimiento as core
    from app.erp.api.sat import sat_tab_of

    with session_factory() as s:
        oid = _order(s, "OLD-1", prep="in_queue", shipping_not_required=True)
        order = s.get(Order, oid)
        assert sat_tab_of(order) == "sin_envio"
        assert core._envio_label(order) == core.NO_APLICA
        assert core._prep_label(order) == core.NO_APLICA
        assert core._estado(order) == "pendiente"
