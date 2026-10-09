"""ERP · Genei — el error de verdad y el envío que ya existe (09/10/2026).

Lo que pasó: el taller creó el envío a mano en el panel de Genei después de que
fallara el primer intento de BoHub, con la misma referencia externa. A partir
de ahí cada reintento chocó con la regla de duplicados y Genei respondió

    HTTP 500 {"status":0, "message":"Internal error", "data":{},
              "errors":["No se puede crear el envio. El envio externo ya
                         corresponde a un envio existente ARTISJ-9694"]}

BoHub enseñaba «Genei no responde ahora mismo: prueba en un momento: Internal
error» y devolvía 502. El mensaje invitaba a reintentar algo que nunca iba a
funcionar (cinco veces), escondía el único texto útil (`errors[]`) y el pedido
se quedaba sin envío vinculado, sin etiqueta y sin aviso al cliente.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.erp.api.genei as genei_api
import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.api.genei import GENEI_ADAPTER, genei_user_message
from app.erp.integrations.genei.client import GeneiClient, GeneiError
from app.erp.integrations.genei.config import DefaultPackage, GeneiConfig
from app.erp.integrations.genei.service import (
    duplicate_external_reference,
    find_shipment_by_external_code,
)
from app.erp.models import Order
from app.erp.models.carriers import Carrier
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users

#: El cuerpo REAL del 500 de Genei (09/10/2026), literal.
DUPLICADO = json.dumps({
    "status": 0, "message": "Internal error", "data": {},
    "errors": ["No se puede crear el envio. El envio externo ya corresponde "
               "a un envio existente ARTISJ-9694"],
})
#: El envío que el taller ya había creado a mano, como lo da `GET /shipments`.
ENVIO_EXISTENTE = {
    "codigo_envio": "5BXG6KAP", "codigo_envio_externo": "ARTISJ-9694",
    "estado": 1, "codigo_seguimiento": "TRK-9694", "nombre_agencia": "GLS",
    "email_llegada": "cliente@ejemplo.com",
}


def _error_duplicado() -> GeneiError:
    return GeneiError(
        f"POST /shipments → 500: {DUPLICADO}", status=500, body=DUPLICADO,
        errors=["No se puede crear el envio. El envio externo ya corresponde "
                "a un envio existente ARTISJ-9694"],
        remote_message="Internal error",
    )


class FakeGenei:
    """Cliente de Genei de mentira. `create_shipment` puede fallar con el 500
    del duplicado; el listado trae el envío que el taller creó a mano."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.create_error: GeneiError | None = None
        self.rows: list[dict] = [ENVIO_EXISTENTE]
        self.shipment = dict(ENVIO_EXISTENTE)
        self.tracking = {"status": 1, "data": {"estadosAgencia": [], "estadosInternos": []}}

    def get_address(self, address_id):
        self.calls.append(("address", address_id))
        return {"nombre": "SAT Bomedia", "direccion": "Mossen Antoni Solanas",
                "mail": "sat@bomedia.es", "codigo_postal": "08830",
                "poblacion": "SANT BOI", "country_code": "ES",
                "telefono_e164": "+34609144424"}

    def create_shipment(self, payload):
        self.calls.append(("create", payload))
        if self.create_error is not None:
            raise self.create_error
        return {"reference": "NUEVO1", "transactionId": 1, "paymentUrl": "https://pay/x"}

    def list_shipments(self, *, page=1, limit=50):
        self.calls.append(("list", page))
        return (self.rows, len(self.rows)) if page == 1 else ([], len(self.rows))

    def get_shipment(self, code):
        self.calls.append(("get", code))
        if code != self.shipment["codigo_envio"]:
            raise GeneiError(
                f"GET /shipments/{code} → 400", status=400,
                body=json.dumps({"message": "No se ha encontrado el envío",
                                 "errors": [f"No se ha encontrado el envío {code}"]}),
                errors=[f"No se ha encontrado el envío {code}"],
                remote_message="No se ha encontrado el envío",
            )
        return self.shipment

    def get_tracking(self, code):
        self.calls.append(("tracking", code))
        return self.tracking

    def get_tracking_url(self, code):
        return None

    def get_label(self, code):  # pragma: no cover - no se pide en estos tests
        raise GeneiError("sin etiqueta", status=404)


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def fake() -> FakeGenei:
    return FakeGenei()


@pytest.fixture()
def client(session_factory, fake) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with patch.object(genei_api, "build_client", lambda carrier: fake), \
         TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed(s: Session) -> str:
    cfg = GeneiConfig(preferred_couriers={"ES": ["GLS"]}, default_package=DefaultPackage())
    s.add(Carrier(
        name="Genei", code="genei", has_api=True, adapter_class=GENEI_ADAPTER,
        api_base_url="https://apiv2.genei.es",
        api_credentials_encrypted=GeneiClient.encode_credentials("sat@b.es", "pw"),
        default_address_id="1304422", config_json=cfg.to_json(),
    ))
    company = Company(name="Cliente", country="ES")
    s.add(company)
    s.flush()
    order = Order(order_number="ARTISJ-9694", preparation_status="packed",
                  payment_status="paid", transport_status="not_shipped",
                  external_source="manual", company_id=company.id)
    s.add(order)
    s.commit()
    return order.id


def _crear(client, oid: str):
    return client.post(f"/api/erp/orders/{oid}/genei/shipments",
                       headers=auth_headers(client, "sat"), json={
        "agency_id": "2",
        "destination": {"name": "Cliente", "address": "C/ Mayor 1",
                        "postalCode": "28001", "town": "Madrid", "isoCountry": "ES"},
        "packages": [{"weight": 1, "height": 10, "width": 10, "length": 10}],
    })


# --- 1. el mensaje que se enseña --------------------------------------------


def test_el_mensaje_es_el_de_errors_no_internal_error() -> None:
    """`errors[]` dice qué pasa; `message` dice «Internal error», que no
    significa nada. Y con un 500 CON cuerpo no se dice «no responde»."""
    texto = genei_user_message(_error_duplicado())
    assert "ya corresponde a un envio existente ARTISJ-9694" in texto
    assert "Internal error" not in texto
    assert "no responde ahora mismo" not in texto


def test_sin_respuesta_de_verdad_si_dice_no_responde() -> None:
    """Tiempo de espera agotado: ahí sí, porque reintentar tiene sentido."""
    exc = GeneiError("Genei no responde ahora mismo (tiempo de espera agotado): "
                     "prueba en un momento.", no_response=True)
    assert "no responde ahora mismo" in genei_user_message(exc)


def test_la_referencia_sale_del_mensaje() -> None:
    assert duplicate_external_reference(_error_duplicado().errors) == "ARTISJ-9694"
    assert duplicate_external_reference(["Saldo insuficiente"]) is None


# --- 2. el código HTTP y el camino de recuperación ---------------------------


def test_referencia_duplicada_vincula_el_envio_que_ya_existe(client, session_factory, fake):
    """El arreglo que vale dinero: en vez de 502 y un pedido huérfano, BoHub
    busca el envío por su referencia externa, lo vincula y NO crea otro."""
    with session_factory() as s:
        oid = _seed(s)
    fake.create_error = _error_duplicado()
    r = _crear(client, oid)
    # El endpoint de crear conserva su 201: el pedido acaba con envío, aunque
    # lo haya traído en vez de crearlo (`linked`).
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["linked"] is True
    assert body["summary"]["shipment_code"] == "5BXG6KAP"
    assert body["summary"]["tracking"] == "TRK-9694"
    assert body["summary"]["courier"] == "GLS"
    # Una sola creación (la que falló): buscar y vincular nunca crea otra.
    assert [c[0] for c in fake.calls].count("create") == 1
    with session_factory() as s:
        order = s.get(Order, oid)
        estado = json.loads(order.packing_json)["genei"]
        assert estado["shipment_code"] == "5BXG6KAP"
        assert estado["linked_from"] == "duplicado"
        assert estado["external_reference"] == "ARTISJ-9694"
        # Resuelto: el error del intento ya no se queda colgado en la ficha.
        assert "last_error" not in estado
        assert order.tracking_number == "TRK-9694"


def test_si_no_aparece_en_el_listado_devuelve_409_con_el_error_real(
    client, session_factory, fake,
):
    """Sin envío que vincular, el 409 (no 502) lleva el texto de Genei y la
    referencia, que es la pista para buscarlo en el panel."""
    with session_factory() as s:
        oid = _seed(s)
    fake.create_error = _error_duplicado()
    fake.rows = []                                   # no aparece en el listado
    r = _crear(client, oid)
    assert r.status_code == 409, r.text
    detalle = r.json()["detail"]
    assert detalle["code"] == "genei_shipment_exists"
    assert detalle["external_reference"] == "ARTISJ-9694"
    assert "ya corresponde a un envio existente" in detalle["detail"]
    assert "Internal error" not in detalle["detail"]
    with session_factory() as s:
        estado = json.loads(s.get(Order, oid).packing_json or "{}").get("genei", {})
        assert not estado.get("shipment_code")       # no se ha vinculado nada
        # El error de Genei queda EN EL PEDIDO, no solo en el log del
        # contenedor: el del primer fallo del 09/10 se perdió al recrearse
        # `api` en el despliegue de las 07:19.
        assert "ARTISJ-9694" in estado["last_error"]["detail"]
        assert estado["last_error"]["status"] == 500


def test_genei_sin_respuesta_sigue_siendo_502(client, session_factory, fake):
    with session_factory() as s:
        oid = _seed(s)
    fake.create_error = GeneiError(
        "Genei no responde ahora mismo (tiempo de espera agotado): prueba en "
        "un momento.", no_response=True,
    )
    r = _crear(client, oid)
    assert r.status_code == 502, r.text
    assert "no responde ahora mismo" in r.json()["detail"]["detail"]


# --- 3. vincular a mano por código -------------------------------------------


def test_vincular_por_codigo_trae_transportista_seguimiento_y_cola(
    client, session_factory, fake,
):
    with session_factory() as s:
        oid = _seed(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments/link",
                    headers=auth_headers(client, "sat"),
                    json={"shipment_code": "5BXG6KAP"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["linked"] is True
    assert body["summary"]["courier"] == "GLS"
    assert body["summary"]["tracking"] == "TRK-9694"
    # Nunca se crea nada en Genei al vincular.
    assert [c[0] for c in fake.calls].count("create") == 0
    with session_factory() as s:
        order = s.get(Order, oid)
        estado = json.loads(order.packing_json)["genei"]
        assert estado["shipment_code"] == "5BXG6KAP"
        assert estado["linked_from"] == "manual"
        # Entra en la cola de seguimiento como cualquier envío de BoHub: con
        # su estado y su último refresco.
        assert estado["state_bucket"] and estado["refreshed_at"]
        assert order.tracking_number == "TRK-9694"


def test_vincular_un_codigo_que_genei_no_conoce(client, session_factory, fake):
    with session_factory() as s:
        oid = _seed(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments/link",
                    headers=auth_headers(client, "sat"),
                    json={"shipment_code": "ARTISJ-9694"})
    # La referencia externa NO vale en `GET /shipments/{x}` (probado en vivo):
    # el error que se enseña es el de Genei, no «no responde».
    assert r.status_code == 502, r.text
    assert "No se ha encontrado el envío" in r.json()["detail"]["detail"]


def test_un_pedido_con_envio_no_deja_crear_ni_vincular_otro(client, session_factory, fake):
    with session_factory() as s:
        oid = _seed(s)
    assert _crear(client, oid).status_code == 201          # el primero entra
    r = _crear(client, oid)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "shipment_exists"
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments/link",
                    headers=auth_headers(client, "sat"),
                    json={"shipment_code": "5BXG6KAP"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "shipment_exists"
    assert [c[0] for c in fake.calls].count("create") == 1


def test_no_se_vincula_el_envio_de_otro_pedido(client, session_factory, fake):
    """Lo contrario de arreglarlo: con la etiqueta y el seguimiento de otro
    pedido, el bulto sale con la dirección equivocada y el cliente recibe un
    seguimiento que no es el suyo."""
    with session_factory() as s:
        oid = _seed(s)
    fake.shipment = {**ENVIO_EXISTENTE, "codigo_envio_externo": "ARTISJ-9693"}
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments/link",
                    headers=auth_headers(client, "sat"),
                    json={"shipment_code": "5BXG6KAP"})
    assert r.status_code == 409, r.text
    detalle = r.json()["detail"]
    assert detalle["code"] == "genei_shipment_other_order"
    assert "ARTISJ-9693" in detalle["detail"]
    with session_factory() as s:
        estado = json.loads(s.get(Order, oid).packing_json or "{}").get("genei", {})
        assert not estado.get("shipment_code")


def test_no_se_vincula_un_envio_que_ya_tiene_otro_pedido(client, session_factory, fake):
    with session_factory() as s:
        oid = _seed(s)
        otro = Order(order_number="ARTISJ-9694", preparation_status="packed",
                     payment_status="paid", transport_status="not_shipped",
                     external_source="manual")
        otro.packing_json = json.dumps({"genei": {"shipment_code": "5BXG6KAP"}})
        s.add(otro)
        s.commit()
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments/link",
                    headers=auth_headers(client, "sat"),
                    json={"shipment_code": "5BXG6KAP"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "genei_shipment_other_order"


def test_un_envio_cerrado_no_se_vincula_solo(client, session_factory, fake):
    """Con dos envíos para la misma referencia (el primer intento anulado y el
    bueno) se coge el VIVO y el más reciente; si el único que hay está
    cerrado, no se vincula nada: dejaría el pedido bloqueado y sin etiqueta."""
    with session_factory() as s:
        oid = _seed(s)
    fake.create_error = _error_duplicado()
    fake.rows = [{**ENVIO_EXISTENTE, "codigo_envio": "CERRADO1", "estado": 77,
                  "fecha_creacion": "2026-10-09"}]
    r = _crear(client, oid)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "genei_shipment_exists"

    # Con el cerrado y el bueno, se vincula el bueno.
    fake.rows = [
        {**ENVIO_EXISTENTE, "codigo_envio": "CERRADO1", "estado": 77,
         "fecha_creacion": "2026-10-08"},
        {**ENVIO_EXISTENTE, "fecha_creacion": "2026-10-09"},
    ]
    r = _crear(client, oid)
    assert r.status_code == 201, r.text
    assert r.json()["summary"]["shipment_code"] == "5BXG6KAP"


def test_el_vinculo_guarda_lo_mismo_que_la_creacion(client, session_factory, fake):
    """Sin `transaction_id`, la ficha ofrece «Pagar y tramitar» y su error
    invita a ELIMINAR el envío que creó el taller."""
    with session_factory() as s:
        oid = _seed(s)
    fake.shipment = {**ENVIO_EXISTENTE, "estado": 7, "transactionId": 777,
                     "paymentUrl": "https://pay/9694",
                     "fecha_creacion": "2026-10-09T07:21:00"}
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments/link",
                    headers=auth_headers(client, "sat"),
                    json={"shipment_code": "5BXG6KAP"})
    assert r.status_code == 200, r.text
    with session_factory() as s:
        estado = json.loads(s.get(Order, oid).packing_json)["genei"]
        assert estado["transaction_id"] == "777"
        assert estado["payment_url"] == "https://pay/9694"
        # La fecha es la del envío en Genei, no la del vínculo.
        assert estado["created_at"] == "2026-10-09T07:21:00"


def test_un_codigo_con_espacio_no_saca_el_volcado_tecnico(client, session_factory):
    with session_factory() as s:
        oid = _seed(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments/link",
                    headers=auth_headers(client, "sat"),
                    json={"shipment_code": "5BXG6 KAP"})
    assert r.status_code == 422, r.text        # ni siquiera se llama a Genei
    assert "GET /shipments" not in r.text


def test_el_webhook_no_pisa_el_envio_que_ya_tiene_el_pedido(session_factory) -> None:
    """El webhook empareja por referencia externa: un aviso tardío de un envío
    borrado dejaría el pedido apuntando a un código muerto."""
    from app.erp.integrations.genei.webhook import apply_shipment_state

    with session_factory() as s:
        oid = _seed(s)
        order = s.get(Order, oid)
        order.packing_json = json.dumps({"genei": {"shipment_code": "BUENO1"}})
        s.commit()
        apply_shipment_state(s, order, {**ENVIO_EXISTENTE, "codigo_envio": "VIEJO9"})
        s.commit()
        estado = json.loads(s.get(Order, oid).packing_json)["genei"]
        assert estado["shipment_code"] == "BUENO1"
        # Y en un pedido SIN envío sí lo deja vinculado.
        otro = Order(order_number="X-1", preparation_status="packed",
                     payment_status="paid", transport_status="not_shipped",
                     external_source="manual")
        s.add(otro)
        s.flush()
        apply_shipment_state(s, otro, ENVIO_EXISTENTE)
        s.commit()
        assert json.loads(otro.packing_json)["genei"]["shipment_code"] == "5BXG6KAP"


# --- la búsqueda por referencia externa --------------------------------------


def test_busqueda_por_referencia_externa_recorre_paginas() -> None:
    class Paginado:
        """Página llena = hay más; página corta = se acabó (Genei no siempre
        manda `count`, así que el corte NO puede salir del total)."""

        def __init__(self) -> None:
            self.pages: list[int] = []

        def list_shipments(self, *, page=1, limit=50):
            self.pages.append(page)
            llena = [{"codigo_envio": f"X{page}-{i}", "codigo_envio_externo": "OTRA"}
                     for i in range(50)]
            if page == 2:
                return [*llena[:10], ENVIO_EXISTENTE], None
            return llena, None

    cli = Paginado()
    row = find_shipment_by_external_code(cli, "artisj-9694")   # sin distinguir mayúsculas
    assert row is not None and row["codigo_envio"] == "5BXG6KAP"
    assert cli.pages == [1, 2]


def test_la_busqueda_no_rompe_si_el_listado_falla() -> None:
    class Roto:
        def list_shipments(self, *, page=1, limit=50):
            raise GeneiError("GET /shipments → 404", status=404)

    assert find_shipment_by_external_code(Roto(), "ARTISJ-9694") is None
