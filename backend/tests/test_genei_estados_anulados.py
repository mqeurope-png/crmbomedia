"""Genei — el estado 11 que dejaba envíos zombis, la referencia externa que no
se mandaba, y el NIF del remitente que DHL exige (13/10/2026).

Lo que pasaba en vivo (verificado con la API real): dos envíos de DHL cayeron
7→6→1→**11** («PENDIENTE DE ABONO», anulación con devolución) ellos solos y se
quedaron con etiqueta y tracking vivos pero sin salir jamás; BoHub no mapeaba el
11 y los daba por entregables. Uno de ellos (`EEAI1XXM`) ni siquiera llevaba
`codigo_envio_externo`. Y la diferencia con el envío bueno creado en el panel
era el NIF del remitente (`dni_salida`): la dirección registrada lo devuelve
vacío por API y DHL anula el envío sin él.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import patch

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
from app.erp.integrations.genei.config import DefaultPackage, GeneiConfig, OriginAddress
from app.erp.integrations.genei.service import (
    build_origin,
    build_shipment_payload,
    genei_state_of,
    ultimo_anulado,
)
from app.erp.models import Order
from app.erp.models.carriers import Carrier
from app.main import app
from app.models.crm import AuditLog, Company
from tests._test_helpers import auth_headers, seed_test_users

SECRET = "topsecret-webhook-token"


class FakeGenei:
    """Cliente de Genei de mentira para el endpoint de crear/eliminar."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        # La dirección registrada DEVUELVE el NIF VACÍO (como en vivo): sin el
        # `origin.vat` de Ajustes, el remitente iría sin NIF.
        self.address = {"nombre": "Streamtec SAT", "direccion": "Mossen Antoni Solanas",
                        "mail": "sat@bomedia.es", "codigo_postal": "08830",
                        "poblacion": "SANT BOI", "country_code": "ES",
                        "telefono_e164": "+34609144424", "vat_number": None}
        self.created = {"reference": "NEW1", "transactionId": 7, "paymentUrl": "https://pay/x"}

    def get_address(self, address_id):
        self.calls.append(("address", address_id))
        return self.address

    def create_shipment(self, payload):
        self.calls.append(("create", payload))
        return self.created

    def delete_shipment(self, code):
        self.calls.append(("delete", code))
        return {"deleted": True}

    def get_shipment(self, code):  # pragma: no cover - no se usa aquí
        self.calls.append(("get", code))
        return {}

    def get_tracking(self, code):  # pragma: no cover
        return {"status": 1, "data": {"estadosAgencia": [], "estadosInternos": []}}

    def get_tracking_url(self, code):  # pragma: no cover
        return None


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


def _carrier(s: Session, *, vat: str = "B64154263") -> None:
    cfg = GeneiConfig(preferred_couriers={"ES": ["DHL"]}, default_package=DefaultPackage(),
                      origin=OriginAddress(iso_country="ES", postal_code="08830",
                                           town="Sant Boi", vat=vat))
    s.add(Carrier(
        name="Genei", code="genei", has_api=True, adapter_class=GENEI_ADAPTER,
        api_base_url="https://apiv2.genei.es",
        api_credentials_encrypted=GeneiClient.encode_credentials(
            "sat@b.es", "pw", webhook_secret=SECRET),
        default_address_id="1913101", config_json=cfg.to_json(),
    ))


def _order(s: Session, *, number="ARTISJ-9694", transport="label_created",
           code="GKMFZKOI", external_sent="ARTISJ-9694") -> str:
    company = Company(name="Cliente", country="ES", address_line="C/ 1",
                      city="Madrid", postal_code="28001")
    s.add(company)
    s.flush()
    o = Order(order_number=number, external_source="manual", preparation_status="packed",
              payment_status="paid", transport_status=transport, tracking_number="JJD-999",
              company_id=company.id, shipping_name="Cliente")
    block = {"genei": {"shipment_code": code, "state_code": 1, "state_bucket": "ready",
                       "state_label": "Tramitado", "external_sent": external_sent}}
    o.packing_json = json.dumps(block)
    s.add(o)
    s.commit()
    return o.id


def _anulado_payload(*, code="GKMFZKOI", ext="ARTISJ-9694", estado=11) -> dict:
    return {"status": 1, "message": "", "data": {
        "codigo_envio": code, "codigo_envio_externo": ext, "estado": estado,
        "nombre_estado": "PENDIENTE DE ABONO" if estado == 11 else "ENVÍO ABONADO",
        "nombre_agencia": "DHL Parcel B2C", "codigo_seguimiento": "JJD-999"}}


def _post(client, payload):
    return client.post(f"/api/webhooks/genei?token={SECRET}", json=payload)


# --- Fallo 1: estado 11/12 deja el envío fuera de «entregable» ----------------


def test_webhook_11_suelta_el_vinculo_y_no_es_entregable(client, session_factory):
    """status 11: el envío deja de contar como entregable y el pedido no enseña
    etiqueta viva (se suelta el vínculo y vuelve a «sin enviar»)."""
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, transport="label_created")
        s.commit()
    r = _post(client, _anulado_payload(estado=11))
    assert r.status_code == 200, r.text
    assert r.json()["matched"] is True
    with session_factory() as s:
        o = s.get(Order, oid)
        g = genei_state_of(o)
        # Sin envío activo: la ficha vuelve a ofrecer crear/vincular.
        assert not g.get("shipment_code")
        # Rastro de por qué: el último anulado, con su estado.
        assert g["ultimo_anulado"]["shipment_code"] == "GKMFZKOI"
        assert g["ultimo_anulado"]["state_code"] == 11
        # No es entregable: el pedido vuelve a «sin enviar».
        assert o.transport_status.value == "not_shipped"
        # Auditado.
        assert s.scalar(select(AuditLog).where(
            AuditLog.action == "erp.genei.shipment_cancelled")) is not None


def test_webhook_12_queda_registrado_como_abonado(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s)
        s.commit()
    r = _post(client, _anulado_payload(estado=12))
    assert r.status_code == 200
    with session_factory() as s:
        g = genei_state_of(s.get(Order, oid))
        assert not g.get("shipment_code")
        assert g["ultimo_anulado"]["state_code"] == 12          # abonado
        assert any(a["shipment_code"] == "GKMFZKOI" for a in g["anulados"])


def test_webhook_11_es_idempotente(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s)
        s.commit()
    for _ in range(2):
        assert _post(client, _anulado_payload(estado=11)).status_code == 200
    with session_factory() as s:
        g = genei_state_of(s.get(Order, oid))
        # Un solo registro de anulación (el segundo aviso no duplica).
        assert len(g["anulados"]) == 1


# --- Fallo 1: estado no contemplado NO se traga en silencio -------------------


def test_webhook_estado_desconocido_deja_rastro_visible(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, transport="label_created")
        s.commit()
    r = _post(client, _anulado_payload(estado=61))   # 61 no está en la tabla
    assert r.status_code == 200
    with session_factory() as s:
        o = s.get(Order, oid)
        g = genei_state_of(o)
        # Marcado como «estado desconocido» (rastro en la ficha), no ignorado.
        assert g["state_bucket"] == "unknown"
        assert "61" in g["state_label"]
        assert g.get("estado_desconocido_en")
        # El envío sigue vinculado, pero NO se da por entregable ni se mueve solo.
        assert g["shipment_code"] == "GKMFZKOI"
        assert o.transport_status.value == "label_created"


# --- Fallo 2: referencia externa obligatoria + guardada + comparada -----------


def test_crear_sin_referencia_externa_no_se_compone():
    with pytest.raises(ValueError, match="referencia externa"):
        build_shipment_payload(
            agency_id="102", origin={"name": "X"}, destination={"name": "Y"},
            packages=[], external_shipping_code="  ", notification_url=None,
        )


def test_crear_guarda_la_referencia_que_se_manda(client, session_factory, fake):
    with session_factory() as s:
        _carrier(s)
        company = Company(name="Cli", country="ES")
        s.add(company)
        s.flush()
        o = Order(order_number="ARTISJ-9800", external_source="manual",
                  preparation_status="packed", payment_status="paid",
                  transport_status="not_shipped", company_id=company.id,
                  shipping_name="Cli")
        s.add(o)
        s.commit()
        oid = o.id
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments", headers=auth_headers(client, "sat"),
                    json={"agency_id": "102",
                          "destination": {"name": "Cli", "address": "C/ 1", "postalCode": "17138",
                                          "town": "Puilboreau", "isoCountry": "FR"},
                          "packages": [{"weight": 2.65, "height": 25, "width": 15, "length": 15}]})
    assert r.status_code == 201, r.text
    create = next(c[1] for c in fake.calls if c[0] == "create")
    assert create["externalShippingCode"] == "ARTISJ-9800"
    with session_factory() as s:
        # Se guarda lo que se MANDÓ (no solo lo que vuelve), para ver un desajuste.
        assert genei_state_of(s.get(Order, oid))["external_sent"] == "ARTISJ-9800"


def test_el_webhook_avisa_si_la_referencia_externa_no_casa(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, number="ARTISJ-9694", external_sent="ARTISJ-9694")
        s.commit()
    # El webhook trae OTRA referencia externa para el mismo envío.
    r = _post(client, {"status": 1, "data": {
        "codigo_envio": "GKMFZKOI", "codigo_envio_externo": "ARTISJ-0000", "estado": 5,
        "codigo_seguimiento": "JJD-999", "nombre_agencia": "DHL Parcel B2C"}})
    assert r.status_code == 200
    with session_factory() as s:
        mis = genei_state_of(s.get(Order, oid))["external_mismatch"]
        assert mis["enviado"] == "ARTISJ-9694" and mis["devuelto"] == "ARTISJ-0000"


# --- Fallo 3: borrar / anular libera la referencia ----------------------------


def test_borrar_el_envio_libera_la_referencia_para_crear_otro(client, session_factory, fake):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, code="GKMFZKOI")
        s.commit()
    # Borrar el envío.
    assert client.request("DELETE", f"/api/erp/orders/{oid}/genei/shipment",
                          headers=auth_headers(client, "sat")).status_code == 200
    with session_factory() as s:
        assert not genei_state_of(s.get(Order, oid)).get("shipment_code")
    # Y ahora se puede crear otro para el mismo pedido (no «shipment_exists»).
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments", headers=auth_headers(client, "sat"),
                    json={"agency_id": "102",
                          "destination": {"name": "Cli", "address": "C/ 1", "postalCode": "17138",
                                          "town": "Puilboreau", "isoCountry": "FR"},
                          "packages": [{"weight": 2.65, "height": 25, "width": 15, "length": 15}]})
    assert r.status_code == 201, r.text


def test_un_envio_anulado_por_webhook_deja_crear_otro(client, session_factory, fake):
    """El de EEAI1XXM: cae a 11 por webhook (como si lo hubieran anulado desde el
    panel) y el pedido queda en condiciones de tener otro envío."""
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, transport="label_created")
        s.commit()
    assert _post(client, _anulado_payload(estado=11)).status_code == 200
    # Sin «shipment_exists»: se puede crear otro.
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments", headers=auth_headers(client, "sat"),
                    json={"agency_id": "102",
                          "destination": {"name": "Cli", "address": "C/ 1", "postalCode": "17138",
                                          "town": "Puilboreau", "isoCountry": "FR"},
                          "packages": [{"weight": 2.65, "height": 25, "width": 15, "length": 15}]})
    assert r.status_code == 201, r.text


def test_borrar_conserva_el_historial_de_anulados(session_factory):
    """Borrar a mano un envío no borra el rastro de por qué el anterior no salió."""
    from app.erp.integrations.genei.service import clear_genei_state, record_cancelled_and_unlink

    with session_factory() as s:
        oid = _order(s)
        o = s.get(Order, oid)
        record_cancelled_and_unlink(o, {"shipment_code": "GKMFZKOI", "state_code": 11,
                                        "state_label": "Pendiente de abono"},
                                    reason="anulado", when="2026-10-13T00:00:00Z")
        # Luego se crea y borra otro: el historial del anulado SE CONSERVA.
        g0 = genei_state_of(o)
        o.packing_json = json.dumps({"genei": {
            "shipment_code": "OTRO1", "anulados": g0["anulados"],
            "ultimo_anulado": g0["ultimo_anulado"]}})
        s.flush()
        clear_genei_state(o)
        g = genei_state_of(o)
        assert not g.get("shipment_code")
        assert g["ultimo_anulado"]["shipment_code"] == "GKMFZKOI"


# --- Fallo 4: el NIF del remitente (lo que DHL exigía) ------------------------


def test_build_origin_usa_el_nif_configurado_cuando_la_direccion_lo_da_vacio():
    # Dirección registrada sin NIF (como en vivo): se usa el de Ajustes.
    origin = build_origin({"nombre": "Streamtec SAT", "vat_number": None,
                           "country_code": "ES", "telefono_e164": "+34609144424"},
                          vat_override="B64154263")
    assert origin["dni"] == "B64154263"
    # Si la dirección SÍ trae NIF, manda el de la dirección.
    assert build_origin({"vat_number": "A11111111"}, vat_override="B64154263")["dni"] == "A11111111"


def test_crear_envio_manda_el_nif_del_remitente(client, session_factory, fake):
    """Fallo 4: el envío lleva `origin.dni` (= `dni_salida`), que es lo que el
    envío bueno del panel tenía y los de DHL por API (anulados) no."""
    with session_factory() as s:
        _carrier(s, vat="B64154263")
        company = Company(name="Cli", country="ES")
        s.add(company)
        s.flush()
        o = Order(order_number="ARTISJ-9801", external_source="manual",
                  preparation_status="packed", payment_status="paid",
                  transport_status="not_shipped", company_id=company.id, shipping_name="Cli")
        s.add(o)
        s.commit()
        oid = o.id
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments", headers=auth_headers(client, "sat"),
                    json={"agency_id": "102",
                          "destination": {"name": "Cli", "address": "C/ 1", "postalCode": "17138",
                                          "town": "Puilboreau", "isoCountry": "FR"},
                          "packages": [{"weight": 2.65, "height": 25, "width": 15, "length": 15}]})
    assert r.status_code == 201, r.text
    create = next(c[1] for c in fake.calls if c[0] == "create")
    assert create["origin"]["dni"] == "B64154263"


def test_el_nif_del_remitente_se_ve_en_la_config(client, session_factory):
    with session_factory() as s:
        _carrier(s, vat="B64154263")
        s.commit()
    r = client.get("/api/erp/genei/config", headers=auth_headers(client))
    assert r.status_code == 200
    assert r.json()["origin"]["vat"] == "B64154263"


def test_ultimo_anulado_helper(session_factory):
    with session_factory() as s:
        oid = _order(s)
        o = s.get(Order, oid)
        assert ultimo_anulado(o) is None
        from app.erp.integrations.genei.service import record_cancelled_and_unlink
        record_cancelled_and_unlink(o, {"shipment_code": "GKMFZKOI", "state_code": 11,
                                        "state_label": "Pendiente de abono"},
                                    reason="anulado", when="2026-10-13T00:00:00Z")
        assert ultimo_anulado(o)["shipment_code"] == "GKMFZKOI"
