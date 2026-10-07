"""Genei — la etiqueta se trae sola y el transporte no depende de ella (07/10/2026).

FLUXLA-5849: envío tramitado a las 11:37, con tracking y aviso al cliente ya
enviado, y para BoHub «sin enviar» porque la etiqueta (y la transición) solo
llegaban cuando alguien pulsaba «🖨 Imprimir etiqueta». Aquí:

- tramitado → el transporte pasa a «etiqueta creada» solo (webhook / refresh),
  aunque la etiqueta falle;
- la etiqueta se pide sola, con reintentos (30 s, 2 min, 10 min, 1 h) si Genei
  aún no la tiene, sin duplicar; si se agotan, queda constancia y el Cuadre lo
  avisa;
- si una persona la trajo antes, el automático no hace nada;
- el arreglo de una pasada y las dos comprobaciones nuevas del Cuadre.

Genei y Redis son dobles: ninguna llamada sale a la red.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.erp.api.genei as genei_api
import app.erp.api.shipping as shipping_api
import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.cuadre import engine as cuadre_engine
from app.erp.integrations.genei import label_job
from app.erp.integrations.genei.client import GeneiClient, GeneiError, GeneiLabel
from app.erp.integrations.genei.service import genei_state_of
from app.erp.models import CuadreFinding, Order, OrderStatusHistory, ShipmentFile
from app.erp.models.carriers import Carrier
from app.main import app
from app.storage.local import LocalShippingStorage
from tests._test_helpers import auth_headers, seed_test_users

SECRET = "topsecret-webhook-token"
AHORA = datetime(2026, 10, 7, 11, 37, 36, tzinfo=UTC)


class Genei:
    """Doble de `GeneiClient`: la etiqueta falla (`label_not_ready`) las
    primeras `fallos` veces y luego llega."""

    def __init__(self, fallos: int = 0, status: int = 400) -> None:
        self.fallos = fallos
        self.status = status
        self.labels = 0
        self.shipment = {"codigo_envio": "3B9QGGHO", "codigo_envio_externo": "FLUXLA-5849",
                         "estado": 1, "codigo_seguimiento": "0033260080539700033061",
                         "nombre_agencia": "Ctt Estándar"}

    def get_label(self, code):
        self.labels += 1
        if self.labels <= self.fallos:
            raise GeneiError(f"GET /shipments/{code}/label → {self.status}", status=self.status,
                             body="aún no")
        return GeneiLabel(content=b"%PDF-1.5 etiqueta", kind="pdf",
                          filename=f"Etiqueta_{code}.pdf")

    def get_shipment(self, code):
        return self.shipment

    def get_tracking(self, code):
        return {"status": 1, "message": "", "data": {"estadosAgencia": []}}

    def get_tracking_url(self, code):
        return None


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with f() as seed:
        seed_test_users(seed)
        seed.commit()
    yield f
    Base.metadata.drop_all(engine)


@pytest.fixture()
def genei() -> Genei:
    return Genei()


@pytest.fixture()
def encolados(monkeypatch) -> list[tuple]:
    """La cola de RQ: se apunta qué se encola (pedido, intento, espera)."""
    lista: list[tuple] = []
    monkeypatch.setattr(label_job, "_enqueue", lambda oid, intento, espera:
                        lista.append((oid, intento, espera)))
    return lista


@pytest.fixture()
def http(factory, tmp_path, genei, encolados) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    storage = LocalShippingStorage(base_dir=str(tmp_path))
    with patch.object(shipping_api, "get_shipping_storage", lambda: storage), \
         patch.object(genei_api, "build_client", lambda carrier: genei):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


def _carrier(s: Session) -> None:
    s.add(Carrier(
        name="Genei", code="genei", has_api=True, api_base_url="https://apiv2.genei.es",
        api_credentials_encrypted=GeneiClient.encode_credentials(
            "sat@b.es", "pw", webhook_secret=SECRET),
    ))
    s.commit()


def _fluxla_5849(s: Session, *, transport: str = "not_shipped", bucket: str = "ready",
                 code: int = 1, prep: str = "packed", **extra) -> str:
    """El pedido tal como estaba en producción el 07/10/2026."""
    o = Order(order_number="FLUXLA-5849", external_source="manual",
              preparation_status=prep, transport_status=transport,
              tracking_number="0033260080539700033061")
    genei = {
        "shipment_code": "3B9QGGHO", "courier": "Ctt Estándar",
        "created_at": "2026-10-07T11:37:07+00:00", "paid_at": "2026-10-07T11:37:27+00:00",
        "webhook_at": "2026-10-07T11:37:36+00:00", "state_code": code,
        "state_label": "Tramitado", "state_bucket": bucket,
        "tracking": "0033260080539700033061",
        "customer_email": {"status": "sent", "automatic": True,
                           "sent_at": "2026-10-07T11:37:37+00:00", "count": 1},
        **extra,
    }
    o.packing_json = json.dumps({"genei": genei})
    s.add(o)
    s.commit()
    return o.id


def _webhook(http: TestClient, estado: int = 1):
    return http.post(f"/api/webhooks/genei?token={SECRET}", json={
        "status": 1, "message": "", "data": {
            "codigo_envio": "3B9QGGHO", "codigo_envio_externo": "FLUXLA-5849",
            "estado": estado, "codigo_seguimiento": "0033260080539700033061",
            "nombre_agencia": "Ctt Estándar"}})


def _transporte(factory, oid: str) -> str:
    with factory() as s:
        return s.get(Order, oid).transport_status.value


def _arcos(factory, oid: str, destino: str) -> int:
    with factory() as s:
        return s.scalar(select(func.count(OrderStatusHistory.id)).where(
            OrderStatusHistory.order_id == oid, OrderStatusHistory.to_status == destino))


def _etiquetas(factory, oid: str) -> int:
    with factory() as s:
        return s.scalar(select(func.count(ShipmentFile.id)).where(
            ShipmentFile.order_id == oid, ShipmentFile.kind == "etiqueta",
            ShipmentFile.replaced_at.is_(None)))


def _auto(factory, oid: str) -> dict:
    with factory() as s:
        return genei_state_of(s.get(Order, oid)).get("label_auto") or {}


def _intento(factory, oid: str, intento: int, genei: Genei, programados: list,
             ahora: datetime = AHORA) -> dict:
    with factory() as s:
        return label_job.auto_fetch_label(
            s, oid, attempt=intento, client=genei, now=ahora,
            schedule=lambda o, i, d: programados.append((o, i, d)))


# --- 1 · el transporte no depende de la etiqueta ---------------------------------------


def test_webhook_tramitado_pasa_a_etiqueta_creada_aunque_la_etiqueta_falle(
    http, factory, genei, encolados, tmp_path,
):
    with factory() as s:
        _carrier(s)
        oid = _fluxla_5849(s, bucket="processing", code=6)
    genei.fallos = 99                                   # Genei aún no tiene el PDF
    assert _webhook(http).status_code == 200
    assert _transporte(factory, oid) == "label_created"
    # La etiqueta se pide sola (1.er intento, al momento) y, si falla, se reintenta.
    assert encolados == [(oid, 1, 0)]
    programados: list[tuple] = []
    r = _intento(factory, oid, 1, genei, programados)
    assert r["result"] == "reintento" and programados == [(oid, 2, 30)]
    assert _transporte(factory, oid) == "label_created"
    assert _etiquetas(factory, oid) == 0


def test_actualizar_estado_tambien_lo_pasa(http, factory, genei, encolados):
    with factory() as s:
        _carrier(s)
        oid = _fluxla_5849(s)
    r = http.post(f"/api/erp/orders/{oid}/genei/refresh", headers=auth_headers(http, "sat"))
    assert r.status_code == 200, r.text
    assert _transporte(factory, oid) == "label_created"
    assert r.json()["state"]["label_auto"]["status"] == "esperando"
    assert r.json()["state"]["label_attached"] is False


def test_sin_embalar_no_se_fuerza(http, factory, encolados):
    """La transición sigue pasando por la máquina de estados: sin embalar, no."""
    with factory() as s:
        _carrier(s)
        oid = _fluxla_5849(s, prep="preparing", bucket="processing", code=6)
    assert _webhook(http).status_code == 200
    assert _transporte(factory, oid) == "not_shipped"


# --- 2 · la etiqueta se trae sola --------------------------------------------------------


def test_label_not_ready_reintenta_y_al_tercero_se_adjunta_una_sola_vez(
    http, factory, genei, tmp_path,
):
    with factory() as s:
        _carrier(s)
        oid = _fluxla_5849(s, transport="label_created")
    genei.fallos = 2
    programados: list[tuple] = []
    assert _intento(factory, oid, 1, genei, programados)["result"] == "reintento"
    assert _intento(factory, oid, 2, genei, programados)["result"] == "reintento"
    assert programados == [(oid, 2, 30), (oid, 3, 120)]
    assert _auto(factory, oid)["last_error"] == "Genei aún no tiene la etiqueta."
    r = _intento(factory, oid, 3, genei, programados)
    assert r["result"] == "adjunta" and r["transition_applied"] is False   # ya movido
    assert _etiquetas(factory, oid) == 1
    auto = _auto(factory, oid)
    assert (auto["status"], auto["attempts"], auto["via"]) == ("adjunta", 3, "automatica")
    with factory() as s:
        o = s.get(Order, oid)
        assert genei_state_of(o)["label_fetched_at"]
        f = s.scalar(select(ShipmentFile).where(ShipmentFile.order_id == oid))
        assert f.source == "genei_api" and f.filename == "Etiqueta_3B9QGGHO.pdf"
    # Un intento repetido (job duplicado) no la adjunta otra vez.
    assert _intento(factory, oid, 4, genei, programados)["result"] == "ya_estaba"
    assert _etiquetas(factory, oid) == 1 and genei.labels == 3
    assert _arcos(factory, oid, "label_created") == 0      # nunca se movió dos veces


def test_reintentos_agotados_dejan_constancia_y_el_cuadre_lo_ve(http, factory, genei):
    with factory() as s:
        _carrier(s)
        oid = _fluxla_5849(s, transport="label_created")
    genei.fallos = 99
    programados: list[tuple] = []
    for intento in range(1, 6):
        r = _intento(factory, oid, intento, genei, programados)
    assert r["result"] == "agotada"
    assert [d for (_o, _i, d) in programados] == [30, 120, 600, 3600]
    auto = _auto(factory, oid)
    assert (auto["status"], auto["attempts"]) == ("agotada", 5) and auto["gave_up_at"]
    assert _transporte(factory, oid) == "label_created"
    # Visible en la ficha y en la Cola SAT.
    estado = http.get(f"/api/erp/orders/{oid}/genei/prefill",
                      headers=auth_headers(http, "sat")).json()["state"]
    assert estado["label_auto"]["status"] == "agotada" and estado["label_attached"] is False
    # Y el Cuadre lo avisa pasadas 2 h desde que se tramitó.
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual",
                               ahora=AHORA + timedelta(hours=3))
        f = s.scalar(select(CuadreFinding).where(
            CuadreFinding.check_id == "envio_tramitado_sin_etiqueta"))
        assert f is not None and f.severidad == "media" and f.estado == "abierto"
        assert "se rindió tras 5 intentos" in json.loads(f.detalle_json)["detalle"]
    # Una nueva señal (webhook) no relanza una cadena agotada: queda a mano.
    with factory() as s:
        assert label_job.maybe_schedule_auto_label(s, s.get(Order, oid)) is False


def test_si_una_persona_la_trajo_antes_el_automatico_no_hace_nada(
    http, factory, genei, encolados,
):
    with factory() as s:
        _carrier(s)
        oid = _fluxla_5849(s)
    r = http.post(f"/api/erp/orders/{oid}/genei/label", headers=auth_headers(http, "sat"))
    assert r.status_code == 201, r.text
    assert _transporte(factory, oid) == "label_created"          # el flujo manual, igual
    assert r.json()["state"]["label_attached"] is True
    programados: list[tuple] = []
    assert _intento(factory, oid, 1, genei, programados)["result"] == "ya_estaba"
    assert genei.labels == 1 and programados == [] and _etiquetas(factory, oid) == 1
    assert _auto(factory, oid)["status"] == "adjunta"
    with factory() as s:
        assert label_job.maybe_schedule_auto_label(s, s.get(Order, oid)) is False


def test_webhook_repetido_no_duplica_transiciones_ni_cadenas_ni_adjuntos(
    http, factory, genei, encolados,
):
    with factory() as s:
        _carrier(s)
        oid = _fluxla_5849(s)
    for _ in range(2):
        assert _webhook(http).status_code == 200
    assert _arcos(factory, oid, "label_created") == 1
    assert encolados == [(oid, 1, 0)]                    # una sola cadena
    _intento(factory, oid, 1, genei, [])
    for _ in range(2):
        assert _webhook(http).status_code == 200
    assert encolados == [(oid, 1, 0)] and _etiquetas(factory, oid) == 1
    assert _arcos(factory, oid, "label_created") == 1


def test_una_cadena_perdida_se_puede_relanzar(factory):
    with factory() as s:
        oid = _fluxla_5849(s, label_auto={"status": "esperando", "attempts": 2,
                                          "next_attempt_at": "2026-10-07T08:00:00+00:00"})
        o = s.get(Order, oid)
        assert label_job.needs_auto_label(s, o, now=AHORA)           # 3,5 h sin noticias
        assert not label_job.needs_auto_label(s, o, now=datetime(2026, 10, 7, 8, 30,
                                                                  tzinfo=UTC))


def test_la_cola_sat_dice_como_va_la_etiqueta(http, factory, genei, encolados):
    with factory() as s:
        _carrier(s)
        oid = _fluxla_5849(s, label_auto={"status": "esperando", "attempts": 1})
    r = http.get("/api/erp/sat/queue", headers=auth_headers(http, "sat"))
    assert r.status_code == 200, r.text
    item = next(i for i in r.json()["pendiente_recogida"] if i["id"] == oid)
    assert item["genei"]["label_available"] is True and item["has_etiqueta"] is False
    assert item["genei"]["label_auto"]["status"] == "esperando"


# --- 3 · arreglo de una pasada y Cuadre ----------------------------------------------------


def test_el_cuadre_detecta_fluxla_5849_y_desaparece_tras_el_arreglo(factory):
    with factory() as s:
        oid = _fluxla_5849(s)
        _fluxla_otro = Order(order_number="FLUXLA-5850", external_source="manual",
                             preparation_status="packed", transport_status="not_shipped")
        _fluxla_otro.packing_json = json.dumps({"genei": {"shipment_code": "X", "state_code": 6,
                                                          "state_bucket": "processing"}})
        s.add(_fluxla_otro)
        s.commit()
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual", ahora=AHORA)
        f = s.scalar(select(CuadreFinding).where(
            CuadreFinding.check_id == "envio_tramitado_sin_enviar"))
        assert (f.entidad_id, f.severidad, f.estado) == (oid, "alta", "abierto")
        det = json.loads(f.detalle_json)
        assert "Ctt Estándar" in det["detalle"] and "0033260080539700033061" in det["detalle"]
        assert "el cliente ya recibió el aviso" in det["detalle"]
        assert det["enlace"] == f"/erp/orders/{oid}"

        previa = label_job.fix_stuck_transport(s, dry_run=True)
        assert previa["movidos"] == ["FLUXLA-5849"]                # el de Genei 6, no
        assert s.get(Order, oid).transport_status.value == "not_shipped"
        with patch.object(label_job, "_enqueue", lambda *a: None):
            hecho = label_job.fix_stuck_transport(s, dry_run=False)
        assert hecho["movidos"] == ["FLUXLA-5849"] and hecho["sin_mover"] == []

    assert _transporte(factory, oid) == "label_created"
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual", ahora=AHORA)
        f = s.scalar(select(CuadreFinding).where(
            CuadreFinding.check_id == "envio_tramitado_sin_enviar"))
        assert f.estado == "resuelto"
        # Idempotente: otra pasada no mueve nada más.
        assert label_job.fix_stuck_transport(s, dry_run=False)["movidos"] == []


def test_el_arreglo_dice_cuales_no_pudo_mover(factory):
    with factory() as s:
        _fluxla_5849(s, prep="preparing")
        with patch.object(label_job, "_enqueue", lambda *a: None):
            r = label_job.fix_stuck_transport(s, dry_run=False)
    assert r["movidos"] == []
    assert r["sin_mover"][0]["order_number"] == "FLUXLA-5849"
    assert "preparing" in r["sin_mover"][0]["motivo"]


def test_sin_etiqueta_no_avisa_antes_de_tiempo_ni_con_etiqueta(http, factory, genei):
    with factory() as s:
        oid = _fluxla_5849(s, transport="label_created")
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual",
                               ahora=AHORA + timedelta(minutes=30))
        assert s.scalar(select(CuadreFinding).where(
            CuadreFinding.check_id == "envio_tramitado_sin_etiqueta")) is None
    _intento(factory, oid, 1, genei, [])                    # llega la etiqueta
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual",
                               ahora=AHORA + timedelta(hours=5))
        assert s.scalar(select(CuadreFinding).where(
            CuadreFinding.check_id == "envio_tramitado_sin_etiqueta")) is None
