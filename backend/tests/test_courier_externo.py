"""Envíos con OTRO courier (no Genei): courier, «Enviado · UPS» en todos lados
(Cola SAT, ficha, hoja) y el aviso de envío al cliente también para ellos.

Sin red ni emails reales (Gmail simulado)."""
from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.integrations.genei.client import GeneiClient
from app.erp.integrations.genei.config import GeneiConfig
from app.erp.models import Order
from app.erp.models.carriers import Carrier
from app.erp.seguimiento import _envio_label, envio_vocabulary
from app.erp.seguimiento_mirror import _COL, _listas_cerradas
from app.erp.shipping_courier import (
    external_state,
    normalize_courier,
    suggest_courier,
    tracking_url_for,
)
from app.main import app
from app.models.crm import AuditLog, Company, Contact
from tests._test_helpers import auth_headers, seed_test_users

UPS = "1ZFV75016835455899"
CTT = "0033260080539700026582"


# --- courier: nombres, sugerencia y enlace ---------------------------------------


@pytest.mark.parametrize(("raw", "name"), [
    ("ups", "UPS"), ("UPS", "UPS"), ("Ctt Premium", "CTT Express"),
    ("ctt express", "CTT Express"), ("correos express", "Correos Express"),
    ("mbe", "MBE"), ("Transportes Pepe", "Transportes Pepe"), ("  ", None),
])
def test_nombre_del_courier(raw, name):
    assert normalize_courier(raw) == name


def test_sugerencia_por_formato_del_tracking():
    assert suggest_courier(UPS) == "UPS"
    assert suggest_courier("1ZW414R30495271304") == "UPS"
    assert suggest_courier(CTT) == "CTT Express"
    assert suggest_courier("123456") is None
    assert suggest_courier("") is None


def test_enlace_por_courier():
    assert tracking_url_for("UPS", UPS) == f"https://www.ups.com/track?tracknum={UPS}"
    assert tracking_url_for("CTT Express", CTT).endswith(f"Destinatarios.aspx?s={CTT}")
    assert "envio=123" in tracking_url_for("MRW", "123")
    assert tracking_url_for("GLS", "123") is None          # sin URL conocida
    assert tracking_url_for(None, UPS) is None
    assert tracking_url_for("UPS", "") is None


# --- flujo: marcar recogido con otro courier ---------------------------------------


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


@pytest.fixture()
def api(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _carrier(s: Session, **cfg) -> None:
    s.add(Carrier(name="Genei", code="genei", has_api=True, api_base_url="https://apiv2.genei.es",
                  api_credentials_encrypted=GeneiClient.encode_credentials("a@b.es", "pw"),
                  config_json=GeneiConfig(**cfg).to_json()))
    s.commit()


def _order(s: Session, *, number="MAN-0100", genei: dict | None = None,
           ship_email: str | None = "envios@cliente.de", country="DE") -> str:
    company = Company(name="Kunde GmbH", country=country)
    s.add(company)
    s.flush()
    contact = Contact(first_name="Hans", last_name="Müller", email=f"hans-{number}@cliente.de",
                      company_id=company.id)
    s.add(contact)
    s.flush()
    o = Order(order_number=number, external_source="manual", preparation_status="packed",
              payment_status="paid", transport_status="label_created",
              company_id=company.id, contact_id=contact.id)
    packing: dict = {"shipping_address": {"name": "Kunde GmbH", "address_line": "Hauptstr. 1",
                                          "city": "Berlin", "postal_code": "10115",
                                          "country": country, "email": ship_email}}
    if genei:
        packing["genei"] = genei
    o.packing_json = json.dumps(packing)
    s.add(o)
    s.commit()
    return o.id


def _patch_send(fail: Exception | None = None):
    msg = MagicMock()
    msg.id = "msg-1"
    msg.thread_id = "thread-1"
    if fail is not None:
        return patch("app.integrations.gmail.service.send_email", side_effect=fail)
    return patch("app.integrations.gmail.service.send_email", return_value=msg)


def _shipped_item(api, h, oid):
    items = api.get("/api/erp/sat/shipped", headers=h).json()["items"]
    return next(i for i in items if i["id"] == oid)


def test_recogido_con_ups_se_ve_en_enviados_ficha_y_hoja(api, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s)
    h = auth_headers(api)
    with _patch_send() as send:
        r = api.post(f"/api/erp/orders/{oid}/mark-picked-up", headers=h,
                     json={"tracking_number": UPS, "courier": "ups"})
        assert r.status_code == 200, r.text
    # Cola SAT → «Enviados»: courier, tracking enlazado y tipo «externo».
    item = _shipped_item(api, h, oid)
    assert item["shipment_kind"] == "externo"
    assert item["courier"] == "UPS"
    assert item["tracking_number"] == UPS
    assert item["tracking_url"] == f"https://www.ups.com/track?tracknum={UPS}"
    # Ficha.
    info = api.get(f"/api/erp/orders/{oid}/shipment", headers=h).json()
    assert info["kind"] == "externo" and info["courier"] == "UPS"
    assert info["tracking_url"].endswith(UPS) and info["picked_up_at"]
    # Hoja «Seguimiento (app)»: Envío, Tracking y Fecha recogido.
    with session_factory() as s:
        o = s.get(Order, oid)
        assert _envio_label(o) == "Enviado · UPS"
    rows = api.get("/api/erp/seguimiento", headers=h).json()
    fila = next(r for r in rows["items"] if r["id"] == oid)
    assert fila["envio"] == "Enviado · UPS"
    assert fila["tracking"] == UPS
    assert fila["recogido"]
    # Aviso al cliente: UNO, al email de envío, en su idioma (alemán, país DE),
    # con el enlace de UPS.
    assert send.call_count == 1
    kw = send.call_args.kwargs
    assert kw["to"] == ["envios@cliente.de"]
    assert f"https://www.ups.com/track?tracknum={UPS}" in kw["body_text"]
    assert "Sendungsnummer: " + UPS in kw["body_text"]
    assert kw["from_alias"] == "info@artisjet-printers.eu"      # manual, no español


def test_recogido_sin_courier_y_completarlo_despues(api, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s)
    h = auth_headers(api)
    with _patch_send() as send:
        assert api.post(f"/api/erp/orders/{oid}/mark-picked-up", headers=h).status_code == 200
        item = _shipped_item(api, h, oid)
        assert item["courier"] is None and item["shipment_kind"] == "externo"
        with session_factory() as s:
            assert _envio_label(s.get(Order, oid)) == "Enviado · otro courier"
        assert send.call_count == 0                 # sin tracking: no sale nada
        # Luego, desde la ficha / «Enviados»: courier y tracking.
        r = api.patch(f"/api/erp/orders/{oid}/tracking", headers=h,
                      json={"tracking_number": CTT, "courier": "CTT Express"})
        assert r.status_code == 200, r.text
        assert r.json()["courier"] == "CTT Express"
        assert send.call_count == 1                 # ahora sí, una vez
        assert CTT in send.call_args.kwargs["body_text"]
        # Guardar otra vez / corregir: no se duplica.
        api.patch(f"/api/erp/orders/{oid}/tracking", headers=h,
                  json={"tracking_number": CTT, "courier": "MRW"})
        assert send.call_count == 1
    item = _shipped_item(api, h, oid)
    assert item["courier"] == "MRW"
    with session_factory() as s:
        assert _envio_label(s.get(Order, oid)) == "Enviado · MRW"


def test_solo_courier_no_borra_el_tracking(api, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s)
    h = auth_headers(api)
    api.patch(f"/api/erp/orders/{oid}/tracking", headers=h, json={"tracking_number": UPS})
    r = api.patch(f"/api/erp/orders/{oid}/tracking", headers=h, json={"courier": "UPS"})
    assert r.json() == {"id": oid, "tracking_number": UPS, "courier": "UPS"}


def test_sin_email_de_envio_usa_el_del_cliente_y_si_no_hay_no_envia(api, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, number="MAN-0101", ship_email=None)
    h = auth_headers(api)
    with _patch_send() as send:
        api.post(f"/api/erp/orders/{oid}/mark-picked-up", headers=h,
                 json={"tracking_number": UPS, "courier": "UPS"})
        assert send.call_args.kwargs["to"] == ["hans-MAN-0101@cliente.de"]


def test_reenvio_manual_no_se_duplica_con_el_automatico(api, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s)
    h = auth_headers(api)
    with _patch_send() as send:
        api.post(f"/api/erp/orders/{oid}/mark-picked-up", headers=h,
                 json={"tracking_number": UPS, "courier": "UPS"})
        assert send.call_count == 1
        prev = api.get(f"/api/erp/orders/{oid}/genei/customer-email", headers=h).json()
        assert prev["status"]["status"] == "sent" and prev["lang"] == "de"
        r = api.post(f"/api/erp/orders/{oid}/genei/customer-email", headers=h, json={})
        assert r.status_code == 200, r.text
        assert send.call_count == 2                 # el reenvío manual, y ya
        api.patch(f"/api/erp/orders/{oid}/tracking", headers=h,
                  json={"tracking_number": UPS, "courier": "UPS"})
        assert send.call_count == 2
    with session_factory() as s:
        ce = external_state(s.get(Order, oid))["customer_email"]
        assert ce["sends"] == 2 and ce["status"] == "sent"
        audit = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.shipment_emailed", AuditLog.target_id == oid)).all()
        assert len(audit) == 2


def test_courier_sin_url_el_aviso_dice_en_la_web_de(api, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, country="ES")
    h = auth_headers(api)
    with _patch_send() as send:
        api.post(f"/api/erp/orders/{oid}/mark-picked-up", headers=h,
                 json={"tracking_number": "ABC123", "courier": "MBE"})
        body = send.call_args.kwargs["body_text"]
    assert "Sigue el envío aquí: en la web de MBE" in body
    assert "ABC123" in body


def test_recogidos_antes_del_cambio_no_se_avisan_solos(api, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s)
        o = s.get(Order, oid)
        o.transport_status = "in_transit"          # recogido antes del deploy
        s.commit()
    h = auth_headers(api)
    with _patch_send() as send:
        api.patch(f"/api/erp/orders/{oid}/tracking", headers=h,
                  json={"tracking_number": UPS, "courier": "UPS"})
        assert send.call_count == 0
        # A mano sí se puede.
        r = api.post(f"/api/erp/orders/{oid}/genei/customer-email", headers=h, json={})
        assert r.status_code == 200, r.text
        assert send.call_count == 1


def test_un_envio_de_genei_no_cambia(api, session_factory):
    genei = {"shipment_code": "GEN9", "state_bucket": "in_transit", "courier": "Ctt Premium",
             "tracking": CTT, "tracking_url": "https://genei/track"}
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, genei=genei)
        o = s.get(Order, oid)
        o.transport_status = "in_transit"
        o.tracking_number = CTT
        s.commit()
    h = auth_headers(api)
    item = _shipped_item(api, h, oid)
    assert item["shipment_kind"] == "genei"
    assert item["courier"] == "Ctt Premium"
    assert item["tracking_url"] == "https://genei/track"
    # El courier de un envío Genei no se toca a mano.
    api.patch(f"/api/erp/orders/{oid}/tracking", headers=h,
              json={"tracking_number": CTT, "courier": "UPS"})
    with session_factory() as s:
        o = s.get(Order, oid)
        assert external_state(o) == {}
        assert _envio_label(o) == "En tránsito"     # el de Genei (sin escaneo)


def test_la_hoja_admite_los_enviado_con_courier():
    envio = _listas_cerradas()[_COL["Envío"]]
    for v in ("Enviado · UPS", "Enviado · CTT Express", "Enviado · otro courier"):
        assert v in envio
        assert v in envio_vocabulary()
    assert len(envio) == len(set(envio))
