"""Genei — estado REAL del envío con el tracking DETALLADO del transportista.

`GET /shipments/{code}/tracking` trae los eventos de la propia agencia
(`estadosAgencia[]`). El estado que se enseña (Enviados, ficha, hoja) es el del
último escaneo real, y el transporte no pasa a «en tránsito» sin escaneo — caso
real ALB-2-200038 (CTT `0033260080539700026674`): Genei lo daba por recogido y
CTT decía «Pendiente de entrada en red». Todo sin red (MockTransport / fakes).
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.erp.api.genei as genei_api
import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.api.sat import TAB_ENVIADOS, TAB_PENDIENTE_RECOGIDA, sat_tab_of
from app.erp.drive_managed import _ENVIO_BOHUB
from app.erp.integrations.genei import tracking_job
from app.erp.integrations.genei.client import GeneiAuthError, GeneiClient, GeneiError
from app.erp.integrations.genei.config import GeneiConfig
from app.erp.integrations.genei.service import genei_state_of
from app.erp.integrations.genei.status import READY, is_tramitado, state_of, transport_status_for
from app.erp.integrations.genei.tracking import (
    AVAILABLE_PICKUP,
    DELIVERED,
    IN_TRANSIT,
    INCIDENT,
    OUT_FOR_DELIVERY,
    PICKED_UP,
    PRE_TRANSIT,
    UNKNOWN,
    carrier_events,
    classify_carrier_text,
    summarize_tracking,
    transport_target,
)
from app.erp.integrations.genei.webhook import apply_shipment_state
from app.erp.models import Order, OrderStatusHistory
from app.erp.models.carriers import Carrier
from app.erp.seguimiento import _envio_label
from app.erp.seguimiento_mirror import _COL, _listas_cerradas
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users

SECRET = "webhook-secret-tracking"
CTT_TRACKING = "0033260080539700026674"


CTT_URL = "https://www.cttexpress.com/localizador/"


def _tracking(*events: tuple[str, str], url: str = CTT_URL) -> dict:
    """Respuesta de `/tracking` (forma del Swagger oficial) con `events` =
    [(fecha ISO, descripción del transportista)], en el orden dado."""
    return {
        "status": 1, "message": url,
        "data": {
            "estadosAgencia": [
                {"fecha": f, "codigo_estado": f"C{i}", "descripcion": d}
                for i, (f, d) in enumerate(events)
            ],
            "estadosInternos": [],
            "reintentosRetramitacionAutomaticos": {},
        },
        "errors": [],
    }


def _shipment(estado: int, *, code="GEN9", ext="ALB-2-200038") -> dict:
    return {"codigo_envio": code, "codigo_envio_externo": ext, "estado": estado,
            "codigo_seguimiento": CTT_TRACKING, "nombre_agencia": "Ctt Premium"}


PENDIENTE = ("2026-09-24T18:00:00.000Z", "PENDIENTE DE ENTRADA EN RED")
EN_TRANSITO = ("2026-09-25T09:10:00.000Z", "EN TRANSITO")
EN_REPARTO = ("2026-09-26T07:30:00.000Z", "EN REPARTO")
ENTREGADO = ("2026-09-26T12:05:00.000Z", "ENTREGADO")


# --- clasificación del texto del transportista --------------------------------


@pytest.mark.parametrize(("texto", "paso"), [
    ("PENDIENTE DE ENTRADA EN RED", PRE_TRANSIT),        # CTT 0
    ("Envío prerregistrado", PRE_TRANSIT),
    ("Label Created", PRE_TRANSIT),                       # UPS
    ("MANIFESTADA", PRE_TRANSIT),                         # GLS
    ("Recogida efectuada", PICKED_UP),
    ("Envío admitido en origen", PICKED_UP),
    ("EN TRANSITO", IN_TRANSIT),                          # CTT 1
    ("EN DELEGACIÓN DESTINO", IN_TRANSIT),
    ("EN ADUANA", IN_TRANSIT),
    ("EN REPARTO", OUT_FOR_DELIVERY),                     # CTT 2
    ("Out for Delivery", OUT_FOR_DELIVERY),
    ("RECOGERÁN EN AGENCIA", AVAILABLE_PICKUP),
    ("Envío disponible para recogida", AVAILABLE_PICKUP),
    ("ENTREGADO", DELIVERED),                             # CTT 3
    ("Delivered", DELIVERED),
    ("No entregado. Destinatario ausente", INCIDENT),     # no es «entregado»
    ("INCIDENCIA", INCIDENT),
    ("DEVOLUCIÓN", INCIDENT),
    ("zzz", UNKNOWN),
])
def test_clasifica_el_texto_del_transportista(texto, paso):
    assert classify_carrier_text(texto) == paso


def test_eventos_desordenados_se_ordenan_y_manda_el_ultimo():
    # Genei puede mandar `estadosAgencia` desordenados (su propio ejemplo lo hace).
    raw = _tracking(EN_REPARTO, PENDIENTE, EN_TRANSITO)
    events = carrier_events(raw)
    assert [e.descripcion for e in events] == [
        "PENDIENTE DE ENTRADA EN RED", "EN TRANSITO", "EN REPARTO"]
    out = summarize_tracking(raw)
    assert out["carrier_status"] == "EN REPARTO"
    assert out["carrier_step"] == OUT_FOR_DELIVERY
    assert out["carrier_status_at"].startswith("2026-09-26T07:30")
    assert out["tracking_url"] == "https://www.cttexpress.com/localizador/"
    assert len(out["carrier_events"]) == 3


def test_sin_eventos_no_hay_paso_del_transportista():
    out = summarize_tracking({"status": 1, "message": "", "data": {"estadosAgencia": []}})
    assert out["carrier_step"] is None and out["carrier_status"] is None
    assert summarize_tracking({})["carrier_events"] == []


@pytest.mark.parametrize(("genei", "paso", "destino"), [
    ("in_transit", PRE_TRANSIT, None),          # Genei «recogido», CTT sin escanear
    ("ready", PRE_TRANSIT, None),
    ("ready", IN_TRANSIT, "in_transit"),       # la agencia va por delante de Genei
    ("ready", PICKED_UP, "in_transit"),
    ("in_transit", DELIVERED, "delivered"),
    ("in_transit", OUT_FOR_DELIVERY, "in_transit"),
    ("delivered", PRE_TRANSIT, "delivered"),   # entregado/incidencia de Genei pasan
    ("incident", PRE_TRANSIT, "incident"),
    ("in_transit", None, "in_transit"),        # sin detalle: manda Genei
    ("in_transit", UNKNOWN, "in_transit"),
    ("in_transit", INCIDENT, "in_transit"),    # incidencia de agencia: se enseña
])
def test_cuanto_avanza_el_transporte(genei, paso, destino):
    assert transport_target(genei, paso) == destino


def test_estado_2_de_genei_ya_no_es_en_transito():
    st = state_of(2)
    assert st.bucket == READY
    assert transport_status_for(st.bucket) is None
    assert is_tramitado(st.bucket)        # tramitado: la etiqueta ya existe


def test_cliente_get_tracking_llama_al_endpoint_de_tracking():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return httpx.Response(200, json={"token": "t"})
        seen.append(request.url.path)
        return httpx.Response(200, json=_tracking(PENDIENTE))

    c = GeneiClient(base_url="https://apiv2.genei.es", username="u", password="p",
                    transport=httpx.MockTransport(handler))
    raw = c.get_tracking("GEN9")
    assert seen == ["/api/v2/shipments/GEN9/tracking"]
    assert summarize_tracking(raw)["carrier_status"] == "PENDIENTE DE ENTRADA EN RED"


# --- aplicar al pedido --------------------------------------------------------


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


def _order(s: Session, *, transport="label_created", number="ALB-2-200038",
           genei: dict | None = None) -> str:
    o = Order(order_number=number, external_source="manual", preparation_status="packed",
              transport_status=transport, tracking_number=CTT_TRACKING)
    o.packing_json = json.dumps({"genei": {"shipment_code": "GEN9", "state_bucket": "ready",
                                           **(genei or {})}})
    s.add(o)
    s.commit()
    return o.id


def _transport(o: Order) -> str:
    return getattr(o.transport_status, "value", o.transport_status)


def test_caso_alb_2_200038_no_se_da_por_recogido(session_factory):
    with session_factory() as s:
        oid = _order(s)
        o = s.get(Order, oid)
        _summary, movido = apply_shipment_state(s, o, _shipment(5),
                                                tracking=_tracking(PENDIENTE))
        s.commit()
        o = s.get(Order, oid)
        assert movido is False
        assert _transport(o) == "label_created"             # NO en tránsito
        g = genei_state_of(o)
        assert g["carrier_status"] == "PENDIENTE DE ENTRADA EN RED"
        assert g["carrier_step"] == PRE_TRANSIT
        assert g["tracking_url"]
        # Cola SAT: sigue esperando al transportista, no en «Enviados».
        assert sat_tab_of(o) == TAB_PENDIENTE_RECOGIDA
        # Hoja «Seguimiento (app)»: el escaneo real, no el genérico.
        assert _envio_label(o) == "Pendiente de entrada en red"


def test_evento_nuevo_actualiza_y_es_idempotente(session_factory):
    with session_factory() as s:
        oid = _order(s)
        o = s.get(Order, oid)
        apply_shipment_state(s, o, _shipment(5), tracking=_tracking(PENDIENTE))
        s.commit()
        # Llega el escaneo: en tránsito de verdad.
        _s, movido = apply_shipment_state(s, o, _shipment(5),
                                          tracking=_tracking(PENDIENTE, EN_TRANSITO))
        s.commit()
        o = s.get(Order, oid)
        assert movido is True
        assert _transport(o) == "in_transit"
        assert sat_tab_of(o) == TAB_ENVIADOS
        assert genei_state_of(o)["carrier_status"] == "EN TRANSITO"
        assert _envio_label(o) == "En tránsito"
        historial = s.query(OrderStatusHistory).filter_by(order_id=oid).count()

        # El MISMO evento otra vez (webhook repetido / sondeo): nada cambia.
        _s, movido = apply_shipment_state(s, o, _shipment(5),
                                          tracking=_tracking(PENDIENTE, EN_TRANSITO))
        s.commit()
        assert movido is False
        assert s.query(OrderStatusHistory).filter_by(order_id=oid).count() == historial
        assert len(genei_state_of(s.get(Order, oid))["carrier_events"]) == 2

        # Entregado según el transportista (aunque Genei siga en 5).
        _s, movido = apply_shipment_state(
            s, o, _shipment(5), tracking=_tracking(PENDIENTE, EN_TRANSITO, EN_REPARTO, ENTREGADO))
        s.commit()
        o = s.get(Order, oid)
        assert movido is True
        assert _transport(o) == "delivered"
        assert _envio_label(o) == "Entregado"


def test_sin_tracking_usa_el_escaneo_guardado_si_es_reciente(session_factory):
    ahora = datetime.now(UTC)
    with session_factory() as s:
        oid = _order(s, genei={"carrier_step": PRE_TRANSIT,
                               "tracking_checked_at": ahora.isoformat()})
        o = s.get(Order, oid)
        apply_shipment_state(s, o, _shipment(5))           # webhook sin detalle
        s.commit()
        assert _transport(s.get(Order, oid)) == "label_created"

        viejo = (ahora - timedelta(days=3)).isoformat()
        oid2 = _order(s, number="ALB-OLD", genei={"carrier_step": PRE_TRANSIT,
                                                  "tracking_checked_at": viejo})
        o2 = s.get(Order, oid2)
        apply_shipment_state(s, o2, _shipment(5, ext="ALB-OLD"))
        s.commit()
        # Dato viejo: no deja el pedido parado; manda Genei.
        assert _transport(s.get(Order, oid2)) == "in_transit"


def test_estado_2_por_webhook_no_marca_recogido(session_factory):
    with session_factory() as s:
        oid = _order(s, genei={"state_bucket": "processing"})
        o = s.get(Order, oid)
        apply_shipment_state(s, o, _shipment(2))
        s.commit()
        o = s.get(Order, oid)
        assert _transport(o) == "label_created"
        assert genei_state_of(o)["state_bucket"] == READY
        assert sat_tab_of(o) == TAB_PENDIENTE_RECOGIDA


def test_el_vocabulario_de_la_hoja_incluye_los_pasos_reales():
    envio = _listas_cerradas()[_COL["Envío"]]
    for etiqueta in ("Pendiente de entrada en red", "Recogido", "En reparto",
                     "Disponible en oficina", "Entregado", "En tránsito"):
        assert etiqueta in envio
        assert etiqueta in _ENVIO_BOHUB
    assert len(envio) == len(set(envio))


# --- endpoints: webhook, «Actualizar estado», Cola SAT --------------------------


class FakeApi:
    """Genei de mentira: el envío (estado grueso) y su `/tracking`."""

    def __init__(self) -> None:
        self.estado = 5
        self.tracking: dict | Exception = _tracking(PENDIENTE)
        self.calls: list[tuple[str, str]] = []

    def get_shipment(self, code):
        self.calls.append(("get", code))
        return _shipment(self.estado, code=code)

    def get_tracking(self, code):
        self.calls.append(("tracking", code))
        if isinstance(self.tracking, Exception):
            raise self.tracking
        return self.tracking


@pytest.fixture()
def fake() -> FakeApi:
    return FakeApi()


@pytest.fixture()
def api(session_factory, fake) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with patch.object(genei_api, "build_client", lambda carrier: fake):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


def _carrier(s: Session, **cfg) -> None:
    s.add(Carrier(
        name="Genei", code="genei", has_api=True, api_base_url="https://apiv2.genei.es",
        api_credentials_encrypted=GeneiClient.encode_credentials(
            "sat@b.es", "pw", webhook_secret=SECRET),
        config_json=GeneiConfig(**cfg).to_json(),
    ))
    s.commit()


def test_webhook_lee_el_tracking_y_no_da_por_recogido(api, session_factory, fake):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s)
    r = api.post(f"/api/webhooks/genei?token={SECRET}",
                 json={"status": 1, "message": "", "data": _shipment(5)})
    assert r.status_code == 200, r.text
    assert ("tracking", "GEN9") in fake.calls
    with session_factory() as s:
        o = s.get(Order, oid)
        assert _transport(o) == "label_created"
        assert genei_state_of(o)["carrier_status"] == "PENDIENTE DE ENTRADA EN RED"


def test_webhook_sin_tracking_disponible_sigue_como_antes(api, session_factory, fake):
    fake.tracking = GeneiError("GET /shipments/GEN9/tracking → 500")
    with session_factory() as s:
        _carrier(s)
        oid = _order(s)
    r = api.post(f"/api/webhooks/genei?token={SECRET}",
                 json={"status": 1, "message": "", "data": _shipment(5)})
    assert r.status_code == 200, r.text
    with session_factory() as s:
        assert _transport(s.get(Order, oid)) == "in_transit"     # manda Genei


def test_actualizar_estado_trae_el_escaneo_y_enviados_lo_muestra(api, session_factory, fake):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s)
    h = auth_headers(api)
    fake.tracking = _tracking(PENDIENTE, EN_TRANSITO, EN_REPARTO)
    r = api.post(f"/api/erp/orders/{oid}/genei/refresh", headers=h)
    assert r.status_code == 200, r.text
    state = r.json()["state"]
    assert state["carrier_status"] == "EN REPARTO"
    assert state["carrier_step"] == OUT_FOR_DELIVERY
    assert [e["descripcion"] for e in state["carrier_events"]] == [
        "PENDIENTE DE ENTRADA EN RED", "EN TRANSITO", "EN REPARTO"]
    # «Enviados»: el item lleva el estado real del transportista.
    shipped = api.get("/api/erp/sat/shipped", headers=h).json()
    items = shipped["items"] if isinstance(shipped, dict) else shipped
    item = next(i for i in items if i["id"] == oid)
    assert item["genei"]["carrier_status"] == "EN REPARTO"
    assert item["genei"]["tracking_url"]


def test_pendiente_de_recogida_muestra_el_escaneo_real(api, session_factory, fake):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s)
    h = auth_headers(api)
    api.post(f"/api/erp/orders/{oid}/genei/refresh", headers=h)
    queue = api.get("/api/erp/sat/queue", headers=h).json()
    item = next(i for i in queue[TAB_PENDIENTE_RECOGIDA] if i["id"] == oid)
    assert item["genei"]["carrier_status"] == "PENDIENTE DE ENTRADA EN RED"
    assert item["genei"]["carrier_step"] == PRE_TRANSIT


# --- sondeo periódico (worker-sync) ------------------------------------------


def test_sondeo_actualiza_los_envios_vivos_y_es_idempotente(session_factory, fake):
    with session_factory() as s:
        _carrier(s)
        vivo = _order(s)
        entregado = _order(s, number="ALB-ENT", transport="delivered",
                           genei={"state_bucket": "delivered"})
        sin_envio = _order(s, number="ALB-SIN")
        s.get(Order, sin_envio).shipping_not_required = True
        s.commit()

    fake.tracking = _tracking(PENDIENTE, EN_TRANSITO)
    with session_factory() as s:
        out = tracking_job.run_tracking_poll(s, client_factory=lambda c: fake)
    assert out == {"revisados": 1, "movidos": 1, "con_escaneo": 1, "errores": 0}
    assert {c[1] for c in fake.calls} == {"GEN9"}
    with session_factory() as s:
        o = s.get(Order, vivo)
        assert _transport(o) == "in_transit"
        assert genei_state_of(o)["carrier_status"] == "EN TRANSITO"
        assert _transport(s.get(Order, entregado)) == "delivered"

    # Dentro del intervalo no se vuelve a consultar (no martillear a Genei).
    fake.calls.clear()
    with session_factory() as s:
        out = tracking_job.run_tracking_poll(s, client_factory=lambda c: fake)
    assert out["revisados"] == 0 and fake.calls == []

    # Pasado el intervalo, se revisa otra vez; mismo evento → nada se mueve.
    luego = datetime.now(UTC) + timedelta(minutes=31)
    with session_factory() as s:
        out = tracking_job.run_tracking_poll(s, client_factory=lambda c: fake, now=luego)
    assert out["revisados"] == 1 and out["movidos"] == 0


def test_sondeo_apagado_no_hace_nada(session_factory, fake):
    with session_factory() as s:
        _carrier(s, tracking_poll_enabled=False)
        _order(s)
    with session_factory() as s:
        assert tracking_job.run_tracking_poll(s, client_factory=lambda c: fake) is None
    assert fake.calls == []


def test_sondeo_se_corta_si_genei_rechaza_las_credenciales(session_factory, fake):
    with session_factory() as s:
        _carrier(s)
        _order(s)
        _order(s, number="ALB-2")

    def boom(code):
        fake.calls.append(("get", code))
        raise GeneiAuthError("Genei ha rechazado el usuario o la contraseña guardados")

    fake.get_shipment = boom
    with session_factory() as s:
        out = tracking_job.run_tracking_poll(s, client_factory=lambda c: fake)
    assert out["revisados"] == 0
    assert len(fake.calls) == 1              # no sigue martilleando
