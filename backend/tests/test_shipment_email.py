"""Aviso de ENVÍO al cliente (nº de seguimiento + enlace) mandado por BoHub, en
el idioma del cliente y desde el remitente de la marca (no por Genei).

Sin red ni emails reales: Gmail y Genei van simulados.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.erp.api.genei as genei_api
import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.api.genei import GENEI_ADAPTER
from app.erp.integrations.genei.client import GeneiClient
from app.erp.integrations.genei.config import GeneiConfig
from app.erp.integrations.genei.service import genei_state_of
from app.erp.models import Order
from app.erp.models.carriers import Carrier
from app.erp.shipment_email import (
    MAX_AUTO_ATTEMPTS,
    brand_store,
    build_shipment_email,
    customer_order_ref,
    maybe_send_shipment_email,
    shipment_email_language,
    shipment_from_alias,
)
from app.main import app
from app.models.crm import AuditLog, Company, Contact
from app.models.integration_settings import IntegrationAccount
from tests._test_helpers import auth_headers, seed_test_users

SECRET = "webhook-secret-aviso"
CTT = "0033260080539700026674"
URL = "https://www.cttexpress.com/localizador-de-envios/?sc=0033260080539700026674"


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


def _carrier(s: Session, **cfg) -> None:
    s.add(Carrier(
        name="Genei", code="genei", has_api=True, adapter_class=GENEI_ADAPTER,
        api_base_url="https://apiv2.genei.es", default_address_id="1304422",
        api_credentials_encrypted=GeneiClient.encode_credentials(
            "sat@b.es", "pw", webhook_secret=SECRET),
        config_json=GeneiConfig(**cfg).to_json(),
    ))
    s.commit()


def _order(s: Session, *, number="MAN-0001", language=None, source="manual",
           store=None, external_id=None, genei: dict | None = None,
           company_lang=None, company_country=None) -> str:
    company = Company(name="La Maison SARL", language=company_lang, country=company_country)
    s.add(company)
    s.flush()
    contact = Contact(first_name="Alexandre", last_name="Dupont",
                      email=f"contacto-{number.lower()}@lamaison.fr", company_id=company.id)
    s.add(contact)
    s.flush()
    store_id = None
    if store:
        acc = IntegrationAccount(system="woocommerce", account_id=store, display_name=store)
        s.add(acc)
        s.flush()
        store_id = acc.id
    o = Order(order_number=number, external_source=source, external_id=external_id,
              preparation_status="packed", payment_status="paid",
              transport_status="label_created", company_id=company.id,
              contact_id=contact.id, language=language, store_id=store_id)
    block = {"shipment_code": "GEN9", "state_bucket": "ready", "courier": "Ctt Premium"}
    block.update(genei or {})
    o.packing_json = json.dumps({"genei": block})
    s.add(o)
    s.commit()
    return o.id


PENDING = {"dest_email": "compras@lamaison.fr", "dest_name": "Alexandre Dupont",
           "dest_country": "FR", "customer_email": {"status": "pending", "attempts": 0}}


def _patch_send(fail: Exception | None = None):
    msg = MagicMock()
    msg.id = "msg-1"
    msg.thread_id = "thread-1"
    if fail is not None:
        return patch("app.integrations.gmail.service.send_email", side_effect=fail)
    return patch("app.integrations.gmail.service.send_email", return_value=msg)


# --- idioma, remitente, datos ----------------------------------------------------


def test_idioma_cascada_pedido_cliente_pais_destino_defecto(session_factory):
    with session_factory() as s:
        o1 = s.get(Order, _order(s, number="M-1", language="de", company_lang="fr",
                                 genei={"dest_country": "NL"}))
        assert shipment_email_language(s, o1) == ("de", "pedido")
        o2 = s.get(Order, _order(s, number="M-2", company_lang="fr",
                                 genei={"dest_country": "NL"}))
        assert shipment_email_language(s, o2) == ("fr", "cliente")
        o3 = s.get(Order, _order(s, number="M-3", genei={"dest_country": "NL"}))
        assert shipment_email_language(s, o3) == ("nl", "pais_destino")
        o4 = s.get(Order, _order(s, number="M-4", company_country="DE"))
        assert shipment_email_language(s, o4) == ("de", "pais_cliente")
        # Sin idioma en ningún sitio: español (fallback).
        o5 = s.get(Order, _order(s, number="M-5"))
        assert shipment_email_language(s, o5) == ("es", "defecto")
        # País reconocido pero fuera de los grupos (p. ej. Italia): inglés.
        o6 = s.get(Order, _order(s, number="M-6", genei={"dest_country": "IT"}))
        assert shipment_email_language(s, o6) == ("en", "pais_destino")


def test_remitente_web_por_tienda_y_manual_por_idioma(session_factory):
    with session_factory() as s:
        artis = s.get(Order, _order(s, number="ARTISJ-9553", source="woocommerce",
                                    store="artisjet", external_id="9553"))
        assert shipment_from_alias(s, artis, "es") == ("info@artisjet-printers.eu", "tienda")
        boprint = s.get(Order, _order(s, number="BOPRIN-99927", source="woocommerce",
                                      external_id="99927"))       # sin cuenta: por prefijo
        assert brand_store(s, boprint) == "boprint"
        assert shipment_from_alias(s, boprint, "fr") == ("pedidos@streamtec.es", "tienda")
        flux = s.get(Order, _order(s, number="FLUXLA-1200", source="woocommerce",
                                   store="fluxlasers", external_id="1200"))
        assert shipment_from_alias(s, flux, "en") == ("pedidos@streamtec.es", "tienda")
        manual = s.get(Order, _order(s, number="MAN-0042"))
        assert shipment_from_alias(s, manual, "es") == ("pedidos@streamtec.es", "idioma")
        for lang in ("en", "fr", "de", "nl"):
            assert shipment_from_alias(s, manual, lang) == \
                ("info@artisjet-printers.eu", "idioma")
        # Tienda sin remitente configurado: por idioma, como un manual.
        otra = s.get(Order, _order(s, number="OTRA-1", source="woocommerce", store="otra"))
        assert shipment_from_alias(s, otra, "es") == ("pedidos@streamtec.es", "idioma")


def test_numero_de_pedido_que_conoce_el_cliente(session_factory):
    with session_factory() as s:
        web = s.get(Order, _order(s, number="ARTISJ-9553", source="woocommerce",
                                  store="artisjet", external_id="9553"))
        assert customer_order_ref(web) == "9553"
        manual = s.get(Order, _order(s, number="MAN-0042"))
        assert customer_order_ref(manual) == "MAN-0042"


def test_el_aviso_lleva_tracking_enlace_y_datos_en_el_idioma(session_factory):
    with session_factory() as s:
        o = s.get(Order, _order(s, genei={**PENDING, "tracking": CTT, "tracking_url": URL}))
        mail = build_shipment_email(s, o)
        assert mail["lang"] == "fr" and mail["lang_source"] == "pais_destino"
        assert mail["to"] == "compras@lamaison.fr"
        assert mail["from_alias"] == "info@artisjet-printers.eu"
        assert CTT in mail["subject"] and "MAN-0001" in mail["subject"]
        assert "Numéro de suivi : " + CTT in mail["body_text"]
        assert URL in mail["body_text"]
        assert "Ctt Premium" in mail["body_text"]
        assert "Alexandre Dupont" in mail["body_text"]
        assert mail["missing"] == []
        # Sin URL: texto de respaldo en su idioma, sin romper la frase.
        es = s.get(Order, _order(s, number="M-9", language="es",
                                 genei={**PENDING, "tracking": CTT}))
        mail = build_shipment_email(s, es)
        assert "Sigue el envío aquí: en la web de la agencia de transporte" in mail["body_text"]
        assert "{" not in mail["body_text"]


# --- disparo automático: una sola vez, en cuanto hay tracking --------------------


def test_se_envia_una_vez_cuando_hay_tracking_y_no_se_duplica(session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, genei=dict(PENDING))
    with session_factory() as s, _patch_send() as send:
        o = s.get(Order, oid)
        assert maybe_send_shipment_email(s, o) is None          # aún sin tracking
        assert send.call_count == 0
        assert genei_state_of(o)["customer_email"]["status"] == "pending"

        o.tracking_number = CTT
        g = json.loads(o.packing_json)
        g["genei"]["tracking"] = CTT
        o.packing_json = json.dumps(g)
        s.commit()
        out = maybe_send_shipment_email(s, o, tracking_url_fetcher=lambda code: URL)
        assert out is not None and out["sent"] is True
        assert send.call_count == 1
        kw = send.call_args.kwargs
        assert kw["to"] == ["compras@lamaison.fr"]
        assert kw["from_alias"] == "info@artisjet-printers.eu"
        assert URL in kw["body_text"] and URL in kw["body_html"]
        ce = genei_state_of(s.get(Order, oid))["customer_email"]
        assert ce["status"] == "sent" and ce["lang"] == "fr" and ce["automatic"] is True

        # Refrescos / webhooks / sondeo repetidos: NO se vuelve a mandar.
        for _ in range(3):
            assert maybe_send_shipment_email(s, s.get(Order, oid)) is None
        assert send.call_count == 1
        audit = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.shipment_emailed", AuditLog.target_id == oid)).all()
        assert len(audit) == 1
        assert "compras@lamaison.fr" in audit[0].message and "francés" in audit[0].message


def test_envios_antiguos_sin_marca_no_se_avisan_solos(session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, genei={"tracking": CTT, "dest_email": "x@y.es"})
    with session_factory() as s, _patch_send() as send:
        assert maybe_send_shipment_email(s, s.get(Order, oid)) is None
        assert send.call_count == 0


def test_interruptor_apagado_no_envia(session_factory):
    with session_factory() as s:
        _carrier(s, customer_email_enabled=False)
        oid = _order(s, genei={**PENDING, "tracking": CTT})
    with session_factory() as s, _patch_send() as send:
        assert maybe_send_shipment_email(s, s.get(Order, oid)) is None
        assert send.call_count == 0


def test_si_gmail_falla_se_reintenta_con_limite(session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, genei={**PENDING, "tracking": CTT})
    with session_factory() as s, _patch_send(fail=RuntimeError("gmail caído")) as send:
        for _ in range(MAX_AUTO_ATTEMPTS + 2):
            assert maybe_send_shipment_email(s, s.get(Order, oid)) is None
        assert send.call_count == MAX_AUTO_ATTEMPTS           # y luego, a mano
        ce = genei_state_of(s.get(Order, oid))["customer_email"]
        assert ce["status"] == "error" and "gmail caído" in ce["error"]


# --- endpoints: pagar, webhook, reenviar, crear ----------------------------------


class FakeGenei:
    def __init__(self) -> None:
        self.estado = 1
        self.calls: list[str] = []

    def get_shipment(self, code):
        self.calls.append("get")
        return {"codigo_envio": code, "estado": self.estado, "codigo_seguimiento": CTT,
                "nombre_agencia": "Ctt Premium", "email_llegada": "compras@lamaison.fr"}

    def get_tracking(self, code):
        return {"status": 1, "message": URL, "data": {"estadosAgencia": []}}

    def get_tracking_url(self, code):
        return URL

    def pay_transaction(self, tid):
        self.calls.append("pay")
        return {"status": 1}

    def get_address(self, _):
        return {"nombre": "SAT Bomedia", "direccion": "C/ X 1", "mail": "sat@b.es",
                "codigo_postal": "08830", "poblacion": "Sant Boi", "country_code": "ES",
                "telefono_e164": "+34600000000"}

    def create_shipment(self, payload):
        self.calls.append("create")
        return {"reference": "GEN9", "transactionId": 555, "paymentUrl": "https://pay"}


@pytest.fixture()
def fake() -> FakeGenei:
    return FakeGenei()


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


def test_pagar_y_tramitar_envia_el_aviso_una_vez(api, session_factory, fake):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, genei={**PENDING, "state_bucket": "created", "transaction_id": "555"})
    h = auth_headers(api)
    with _patch_send() as send:
        r = api.post(f"/api/erp/orders/{oid}/genei/pay", headers=h)
        assert r.status_code == 200, r.text
        assert send.call_count == 1
        assert r.json()["state"]["customer_email"]["status"] == "sent"
        # «Actualizar estado» después: no se duplica.
        assert api.post(f"/api/erp/orders/{oid}/genei/refresh", headers=h).status_code == 200
        assert send.call_count == 1


def test_si_el_aviso_falla_el_pago_no_se_rompe(api, session_factory, fake):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, genei={**PENDING, "state_bucket": "created", "transaction_id": "555"})
    with _patch_send(fail=RuntimeError("gmail caído")):
        r = api.post(f"/api/erp/orders/{oid}/genei/pay", headers=auth_headers(api))
    assert r.status_code == 200, r.text
    assert "pay" in fake.calls
    with session_factory() as s:
        g = genei_state_of(s.get(Order, oid))
        assert g["paid_at"]                                  # pagado y guardado
        assert g["customer_email"]["status"] == "error"


def test_webhook_envia_el_aviso_en_cuanto_hay_tracking(api, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, genei=dict(PENDING))
    payload = {"status": 1, "data": {"codigo_envio": "GEN9", "codigo_envio_externo": "MAN-0001",
                                     "estado": 1, "codigo_seguimiento": CTT}}
    with _patch_send() as send:
        for _ in range(2):                                   # webhook repetido
            r = api.post(f"/api/webhooks/genei?token={SECRET}", json=payload)
            assert r.status_code == 200, r.text
        assert send.call_count == 1
    with session_factory() as s:
        assert genei_state_of(s.get(Order, oid))["customer_email"]["status"] == "sent"


def test_reenvio_manual_desde_la_ficha(api, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, genei={**PENDING, "tracking": CTT, "tracking_url": URL,
                               "customer_email": {"status": "sent", "sends": 1}})
    h = auth_headers(api)
    prev = api.get(f"/api/erp/orders/{oid}/genei/customer-email", headers=h).json()
    assert prev["lang"] == "fr" and prev["from_alias"] == "info@artisjet-printers.eu"
    assert prev["to"] == "compras@lamaison.fr" and prev["status"]["status"] == "sent"
    with _patch_send() as send:
        r = api.post(f"/api/erp/orders/{oid}/genei/customer-email", headers=h,
                     json={"to": "otra@lamaison.fr", "lang": "es"})
        assert r.status_code == 200, r.text
        kw = send.call_args.kwargs
        assert kw["to"] == ["otra@lamaison.fr"]
        assert kw["from_alias"] == "pedidos@streamtec.es"   # manual en español
        assert "Nº de seguimiento: " + CTT in kw["body_text"]
    ce = r.json()["state"]["customer_email"]
    assert ce["sends"] == 2 and ce["automatic"] is False


def test_reenvio_sin_tracking_avisa_claro(api, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, genei=dict(PENDING))
    r = api.post(f"/api/erp/orders/{oid}/genei/customer-email", headers=auth_headers(api))
    assert r.status_code == 409
    assert "tracking" in r.json()["detail"]["detail"]


def test_crear_envio_guarda_destinatario_y_deja_el_aviso_pendiente(api, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, genei={"shipment_code": None})
        o = s.get(Order, oid)
        o.packing_json = None
        s.commit()
    r = api.post(f"/api/erp/orders/{oid}/genei/shipments", headers=auth_headers(api), json={
        "agency_id": "2", "packages": [],
        "destination": {"name": "La Maison", "contact": "Alexandre", "address": "12 Rue",
                        "postalCode": "75001", "town": "Paris", "isoCountry": "FR",
                        "email": "compras@lamaison.fr", "phone": "+33100000000"},
    })
    assert r.status_code == 201, r.text
    with session_factory() as s:
        g = genei_state_of(s.get(Order, oid))
        assert g["dest_email"] == "compras@lamaison.fr"
        assert g["dest_country"] == "FR"
        assert g["customer_email"]["status"] == "pending"
        assert g["created_by_user_id"]


# --- Ajustes: plantillas y remitentes ----------------------------------------------


def test_ajustes_plantillas_y_remitentes_del_aviso(api):
    h = auth_headers(api)
    cfg = api.get("/api/erp/settings", headers=h).json()
    assert set(cfg["shipment_email_templates"]) == {"es", "en", "de", "fr", "nl"}
    assert cfg["shipment_email_from"] == {"es": "pedidos@streamtec.es",
                                          "otros": "info@artisjet-printers.eu"}
    r = api.patch("/api/erp/settings", headers=h, json={
        "shipment_email_templates": {"es": {"subject": "Enviado {pedido}", "body": ""}},
        "shipment_email_from": {"es": "envios@streamtec.es", "otros": ""},
    })
    assert r.status_code == 200, r.text
    cfg = api.get("/api/erp/settings", headers=h).json()
    assert cfg["shipment_email_templates"]["es"]["subject"] == "Enviado {pedido}"
    assert cfg["shipment_email_templates"]["es"]["body"]          # vacío = por defecto
    assert cfg["shipment_email_from"] == {"es": "envios@streamtec.es",
                                          "otros": "info@artisjet-printers.eu"}
    ej = api.post("/api/erp/settings/shipment-email/preview", headers=h,
                  json={"lang": "de"}).json()
    assert ej["lang"] == "de" and "0033260080539700026674" in ej["body_text"]
    assert ej["from_alias_example"] == "info@artisjet-printers.eu"
