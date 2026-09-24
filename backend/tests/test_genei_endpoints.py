"""ERP · Genei — endpoints de la Cola SAT (PR-1). Sin red: el `GeneiClient` se
sustituye por un fake que registra las llamadas y devuelve respuestas canónicas.
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
import app.erp.api.shipping as shipping_api
import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.api.genei import GENEI_ADAPTER
from app.erp.integrations.genei.client import GeneiClient, GeneiLabel
from app.erp.integrations.genei.config import DefaultPackage, GeneiConfig
from app.erp.models import Order, ShipmentFile
from app.erp.models.carriers import Carrier
from app.erp.models.shipping import ShipmentPackage
from app.main import app
from app.models.crm import Company
from app.storage.local import LocalShippingStorage
from tests._test_helpers import auth_headers, seed_test_users


class FakeGenei:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.prices = [
            {"agencyId": 1, "name": "Correos Domicilio", "price": 6.0},
            {"agencyId": 2, "name": "GLS Domicilio", "price": 4.5},
            {"agencyId": 4, "name": "GLS Oficina", "price": 2.0},
        ]
        # Forma REAL de la respuesta de creación (verificada en vivo): el código
        # va en `reference` y NO viene el estado (nace en 7, pdte de pago). Esto
        # es lo que devuelve GeneiClient.create_shipment tras `_as_dict`.
        self.created = {"reference": "GEN123", "transactionId": 555,
                        "paymentUrl": "https://pay/x"}
        self.shipment = {"shipmentCode": "GEN123", "estado": 5,
                         "codigo_seguimiento": "TRK-9", "nombre_agencia": "GLS"}

    def agency_prices(self, **kw):
        self.calls.append(("prices", kw))
        return self.prices

    def create_shipment(self, payload):
        self.calls.append(("create", payload))
        return self.created

    def get_address(self, address_id):
        self.calls.append(("address", address_id))
        # Forma real de GET /addresses/{id}: el remitente por defecto de la
        # cuenta (verificado en vivo, con el teléfono en E.164).
        return {
            "nombre": "SAT Bomedia", "direccion": "Mossen Antoni Solanas",
            "mail": "sat@bomedia.es", "codigo_postal": "08830",
            "poblacion": "SANT BOI DE LLOBREGAT", "country_code": "ES",
            "telefono": "609144424", "telefono_e164": "+34609144424",
            "prefijo_telefonico": 34, "vat_number": None, "observaciones": "",
        }

    def pay_transaction(self, transaction_id):
        self.calls.append(("pay", transaction_id))
        return {"status": 1, "message": "Paid"}

    def get_shipment(self, code):
        self.calls.append(("get", code))
        return self.shipment

    def get_label(self, code):
        self.calls.append(("label", code))
        return GeneiLabel(content=b"%PDF-1.5 etiqueta", kind="pdf",
                          filename=f"Etiqueta_{code}.pdf")

    def delete_shipment(self, code):
        self.calls.append(("delete", code))
        return {"deleted": True}


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
def client(session_factory, tmp_path, fake) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    storage = LocalShippingStorage(base_dir=str(tmp_path))
    with patch.object(shipping_api, "get_shipping_storage", lambda: storage), \
         patch.object(genei_api, "build_client", lambda carrier: fake):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


def _seed_carrier(s: Session, *, with_creds=True) -> None:
    cfg = GeneiConfig(preferred_couriers={"ES": ["GLS"]}, default_package=DefaultPackage())
    s.add(Carrier(
        name="Genei", code="genei", has_api=True, adapter_class=GENEI_ADAPTER,
        api_base_url="https://apiv2.genei.es",
        api_credentials_encrypted=(GeneiClient.encode_credentials("sat@b.es", "pw")
                                   if with_creds else None),
        default_address_id="1304422", config_json=cfg.to_json(),
    ))
    s.commit()


def _seed_order(s: Session) -> str:
    company = Company(name="La Maison", vat="FR123", country="ES",
                      address_line="C/ Mayor 1", city="Madrid", postal_code="28001")
    s.add(company)
    s.flush()
    o = Order(order_number="MAN-0001", preparation_status="packed", payment_status="paid",
              transport_status="not_shipped", external_source="manual",
              company_id=company.id, shipping_name="Alexandre")
    o.packing_json = json.dumps({"shipping_address": {
        "address_line": "12 Rue de Paris", "city": "Paris",
        "postal_code": "75001", "country": "FR",
    }})
    s.add(o)
    s.commit()
    return o.id


# --- config -----------------------------------------------------------------


def test_config_put_then_get_hides_password(client, session_factory):
    h = auth_headers(client)
    r = client.put("/api/erp/genei/config", headers=h, json={
        "username": "sat@bomedia.es", "password": "s3cret",
        "default_address_id": "1304422",
        "preferred_couriers": {"ES": ["GLS", "Correos"]},
        "default_package": {"weight": 2, "height": 10, "width": 15, "length": 20},
        "origin": {"iso_country": "ES", "postal_code": "08201", "town": "Sabadell"},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True
    assert body["username"] == "sat@bomedia.es"
    assert "password" not in json.dumps(body)          # nunca se devuelve
    assert body["preferred_couriers"]["ES"] == ["GLS", "Correos"]
    assert body["default_package"]["weight"] == 2
    # La credencial quedó cifrada (no en claro) en la fila del carrier.
    with session_factory() as s:
        carrier = s.scalar(select(Carrier).where(Carrier.code == "genei"))
        assert carrier.api_credentials_encrypted
        assert "s3cret" not in carrier.api_credentials_encrypted


def test_config_update_keeps_password_when_only_email_changes(client, session_factory):
    with session_factory() as s:
        _seed_carrier(s)
    h = auth_headers(client)
    r = client.put("/api/erp/genei/config", headers=h, json={"username": "nuevo@b.es"})
    assert r.status_code == 200
    with session_factory() as s:
        carrier = s.scalar(select(Carrier).where(Carrier.code == "genei"))
        creds = GeneiClient._decode_credentials(carrier.api_credentials_encrypted)
        assert creds == {"username": "nuevo@b.es", "password": "pw"}


# --- prefill / prices -------------------------------------------------------


def test_prefill_resolves_destination(client, session_factory):
    with session_factory() as s:
        _seed_carrier(s)
        oid = _seed_order(s)
    r = client.get(f"/api/erp/orders/{oid}/genei/prefill", headers=auth_headers(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True
    dest = body["destination"]
    assert dest["name"] == "Alexandre"
    assert dest["town"] == "Paris" and dest["postalCode"] == "75001"
    assert dest["isoCountry"] == "FR"
    assert dest["dni"] == "FR123"               # NIF de la empresa
    assert body["missing"] == []                # destino completo
    assert body["origin_address_id"] == "1304422"
    assert body["is_packed"] is True            # pedido embalado → puede crear
    assert body["packages"] == []               # sin bultos medidos → cae al default


def test_prefill_returns_real_measured_packages(client, session_factory):
    # Los bultos reales del SAT (shipment_packages) llegan al prefill; depth_cm
    # se mapea a `length`. Así el modal no abre con el genérico 1/20/20/20.
    with session_factory() as s:
        _seed_carrier(s)
        oid = _seed_order(s)
        s.add(ShipmentPackage(order_id=oid, position=1, weight_kg=2.5,
                              height_cm=30, width_cm=20, depth_cm=15))
        s.add(ShipmentPackage(order_id=oid, position=2, weight_kg=1.0,
                              height_cm=10, width_cm=10, depth_cm=10))
        s.commit()
    r = client.get(f"/api/erp/orders/{oid}/genei/prefill", headers=auth_headers(client))
    assert r.status_code == 200, r.text
    pkgs = r.json()["packages"]
    assert len(pkgs) == 2
    assert pkgs[0] == {"weight": 2.5, "height": 30.0, "width": 20.0, "length": 15.0}
    assert pkgs[1]["weight"] == 1.0 and pkgs[1]["length"] == 10.0


def test_prefill_web_order_uses_shipping_block(client, session_factory):
    # Pedido web (Woo) sin empresa/contacto: el destino sale del bloque
    # `shipping_address` que dejó el mapper (nombre, tel, email, NIF).
    with session_factory() as s:
        _seed_carrier(s)
        o = Order(order_number="BOPRIN-1001", preparation_status="packed",
                  payment_status="paid", transport_status="not_shipped",
                  external_source="woocommerce")
        o.packing_json = json.dumps({"shipping_address": {
            "name": "Alexandre Dubois", "address_line": "12 Rue", "city": "Paris",
            "postal_code": "75001", "country": "FR",
            "phone": "+33 1 23", "email": "a@x.fr", "nif": "FR9999",
        }})
        s.add(o)
        s.commit()
        oid = o.id
    r = client.get(f"/api/erp/orders/{oid}/genei/prefill", headers=auth_headers(client))
    assert r.status_code == 200, r.text
    dest = r.json()["destination"]
    assert dest["name"] == "Alexandre Dubois"
    assert dest["email"] == "a@x.fr" and dest["phone"] == "+33 1 23"
    assert dest["dni"] == "FR9999"
    assert dest["isoCountry"] == "FR" and dest["town"] == "Paris"
    assert r.json()["missing"] == []


def test_prices_proposes_cheapest_preferred_home(client, session_factory, fake):
    with session_factory() as s:
        _seed_carrier(s)
        oid = _seed_order(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/prices", headers=auth_headers(client), json={
        "destination": {"isoCountry": "ES", "postalCode": "28001", "town": "Madrid"},
        "packages": [{"weight": 0.5, "height": 2, "width": 10, "length": 10}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # Preferido ES = GLS; la GLS a domicilio (4.5) gana, no la de oficina (2.0).
    assert body["default"]["agency_id"] == "2"
    assert {o["agency_id"] for o in body["home_options"]} == {"1", "2"}
    assert fake.calls[0][0] == "prices"  # se llamó al cliente Genei


# --- crear / etiqueta / estado / eliminar -----------------------------------


def test_create_shipment_stores_state(client, session_factory, fake):
    with session_factory() as s:
        _seed_carrier(s)
        oid = _seed_order(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments", headers=auth_headers(client), json={
        "agency_id": "2",
        "destination": {"name": "Alexandre", "address": "12 Rue", "postalCode": "75001",
                        "town": "Paris", "isoCountry": "FR"},
        "packages": [{"weight": 0.5, "height": 2, "width": 10, "length": 10}],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    # El código viene en `reference`; sin él, el envío quedaría huérfano en Genei.
    assert body["summary"]["shipment_code"] == "GEN123"
    assert body["state"]["shipment_code"] == "GEN123"
    assert body["state"]["payment_url"] == "https://pay/x"
    # La creación no trae estado → se fija a 7 (pendiente de pago), no «desconocido».
    assert body["summary"]["state_code"] == 7
    assert body["state"]["state_bucket"] == "created"
    # El payload llevó el nº de pedido como externalShippingCode.
    create_call = next(c for c in fake.calls if c[0] == "create")
    assert create_call[1]["externalShippingCode"] == "MAN-0001"
    # Los DOS campos que faltaban y hacían fallar la creación en vivo:
    assert create_call[1]["origin"]["name"] == "SAT Bomedia"
    assert create_call[1]["origin"]["isoCountry"] == "ES"
    assert create_call[1]["origin"]["phone"] == "+34609144424"   # con prefijo
    assert create_call[1]["paymentMethodShipping"] == 4          # pago con saldo
    assert create_call[1]["shippingFromWarehouse"] == 0
    assert "notificationUrl" not in create_call[1]      # PR-1 sin webhook
    # PR-2: se guarda el id de transacción para poder pagar por API.
    assert body["state"]["transaction_id"] == "555"
    # El origen se resolvió desde la dirección registrada de Genei (no duplicado).
    assert ("address", "1304422") in fake.calls
    with session_factory() as s:
        order = s.get(Order, oid)
        assert order.carrier_id is not None


def test_create_shipment_requires_origin_configured(client, session_factory):
    # Sin dirección de origen (remitente) en Ajustes → no se crea (400 claro).
    with session_factory() as s:
        cfg = GeneiConfig(preferred_couriers={"ES": ["GLS"]}, default_package=DefaultPackage())
        s.add(Carrier(
            name="Genei", code="genei", has_api=True, adapter_class=GENEI_ADAPTER,
            api_base_url="https://apiv2.genei.es",
            api_credentials_encrypted=GeneiClient.encode_credentials("sat@b.es", "pw"),
            default_address_id=None, config_json=cfg.to_json(),
        ))
        s.commit()
        oid = _seed_order(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments", headers=auth_headers(client), json={
        "agency_id": "2", "destination": {"name": "X", "address": "a", "postalCode": "1",
                                          "town": "t", "isoCountry": "ES"}, "packages": [],
    })
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "origin_not_configured"


def test_create_shipment_requires_packed(client, session_factory):
    # Regla de negocio: no se puede crear el envío si el pedido no está embalado.
    with session_factory() as s:
        _seed_carrier(s)
        company = Company(name="La Maison", vat="FR123", country="ES",
                          address_line="C/ Mayor 1", city="Madrid", postal_code="28001")
        s.add(company)
        s.flush()
        o = Order(order_number="MAN-0002", preparation_status="in_queue",  # aún NO embalado
                  payment_status="paid", transport_status="not_shipped",
                  external_source="manual", company_id=company.id, shipping_name="Alexandre")
        o.packing_json = json.dumps({"shipping_address": {
            "address_line": "12 Rue", "city": "Paris", "postal_code": "75001", "country": "FR",
        }})
        s.add(o)
        s.commit()
        oid = o.id
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments", headers=auth_headers(client), json={
        "agency_id": "2", "destination": {"name": "X", "address": "a", "postalCode": "1",
                                          "town": "t", "isoCountry": "ES"}, "packages": [],
    })
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_packed"


def test_create_shipment_surfaces_genei_error(client, session_factory, fake):
    # Un error de Genei al crear se propaga como 502 con su mensaje (no se traga).
    from app.erp.integrations.genei.client import GeneiError  # noqa: PLC0415

    def boom(payload):
        raise GeneiError('POST /shipments → Genei rechazó: "origin" is mandatory', status=200)
    fake.create_shipment = boom
    with session_factory() as s:
        _seed_carrier(s)
        oid = _seed_order(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments", headers=auth_headers(client), json={
        "agency_id": "2", "destination": {"name": "X", "address": "a", "postalCode": "1",
                                          "town": "t", "isoCountry": "ES"}, "packages": [],
    })
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "genei_error"
    assert "origin" in r.json()["detail"]["detail"]


def test_create_shipment_rejects_incomplete_destination(client, session_factory):
    with session_factory() as s:
        _seed_carrier(s)
        oid = _seed_order(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments", headers=auth_headers(client), json={
        "agency_id": "2", "destination": {"name": "X"}, "packages": [],
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "destination_incomplete"


def test_create_shipment_conflicts_when_exists(client, session_factory):
    with session_factory() as s:
        _seed_carrier(s)
        oid = _seed_order(s)
        order = s.get(Order, oid)
        order.packing_json = json.dumps({"genei": {"shipment_code": "OLD"}})
        s.commit()
    r = client.post(f"/api/erp/orders/{oid}/genei/shipments", headers=auth_headers(client), json={
        "agency_id": "2", "destination": {"name": "X", "address": "a", "postalCode": "1",
                                          "town": "t", "isoCountry": "ES"}, "packages": [],
    })
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "shipment_exists"


# --- pago interno (PR-2) ----------------------------------------------------


def _seed_with_shipment(s, *, transaction_id="555", code="GEN123"):
    _seed_carrier(s)
    oid = _seed_order(s)
    order = s.get(Order, oid)
    block = {"shipment_code": code, "state_bucket": "created", "state_code": 7}
    if transaction_id is not None:
        block["transaction_id"] = transaction_id
    order.packing_json = json.dumps({"genei": block})
    s.commit()
    return oid


def test_pay_pays_by_api_and_refreshes(client, session_factory, fake):
    with session_factory() as s:
        oid = _seed_with_shipment(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/pay", headers=auth_headers(client))
    assert r.status_code == 200, r.text
    # Pagó la transacción y refrescó el estado leyendo el envío (7 → …).
    pay_call = next(c for c in fake.calls if c[0] == "pay")
    assert pay_call[1] == "555"
    assert any(c[0] == "get" and c[1] == "GEN123" for c in fake.calls)
    body = r.json()
    assert body["state"]["state_bucket"] == "in_transit"   # fake.shipment: estado 5
    assert body["state"]["tracking"] == "TRK-9"
    assert body["state"].get("paid_at")


def test_pay_without_shipment_is_409(client, session_factory):
    with session_factory() as s:
        _seed_carrier(s)
        oid = _seed_order(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/pay", headers=auth_headers(client))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_shipment"


def test_pay_without_transaction_is_409(client, session_factory):
    with session_factory() as s:
        oid = _seed_with_shipment(s, transaction_id=None)
    r = client.post(f"/api/erp/orders/{oid}/genei/pay", headers=auth_headers(client))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_transaction"


def test_pay_surfaces_rejection(client, session_factory, fake):
    # Sin saldo / rechazo → 502 con el mensaje de Genei (no falla en silencio).
    from app.erp.integrations.genei.client import GeneiError  # noqa: PLC0415

    def boom(transaction_id):
        raise GeneiError("GET /payments/pay → Genei rechazó: Saldo insuficiente", status=200)
    fake.pay_transaction = boom
    with session_factory() as s:
        oid = _seed_with_shipment(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/pay", headers=auth_headers(client))
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "genei_error"
    assert "Saldo" in r.json()["detail"]["detail"]


def test_label_stores_shipment_file_and_transitions(client, session_factory, fake):
    with session_factory() as s:
        _seed_carrier(s)
        oid = _seed_order(s)
        order = s.get(Order, oid)
        order.packing_json = json.dumps({"genei": {"shipment_code": "GEN123"}})
        s.commit()
    r = client.post(f"/api/erp/orders/{oid}/genei/label", headers=auth_headers(client))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["file"]["kind"] == "etiqueta"
    assert body["transition_applied"] is True       # packed → label_created
    with session_factory() as s:
        files = s.scalars(select(ShipmentFile).where(ShipmentFile.order_id == oid)).all()
        assert len(files) == 1 and files[0].source == "genei_api"
        assert s.get(Order, oid).transport_status.value == "label_created"


def test_refresh_updates_tracking(client, session_factory):
    with session_factory() as s:
        _seed_carrier(s)
        oid = _seed_order(s)
        order = s.get(Order, oid)
        order.packing_json = json.dumps({"genei": {"shipment_code": "GEN123"}})
        s.commit()
    r = client.post(f"/api/erp/orders/{oid}/genei/refresh", headers=auth_headers(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["tracking"] == "TRK-9"
    assert body["summary"]["state_bucket"] == "in_transit"
    with session_factory() as s:
        assert s.get(Order, oid).tracking_number == "TRK-9"


def test_delete_shipment_clears_state(client, session_factory, fake):
    with session_factory() as s:
        _seed_carrier(s)
        oid = _seed_order(s)
        order = s.get(Order, oid)
        order.packing_json = json.dumps({"genei": {"shipment_code": "GEN123"}})
        s.commit()
    r = client.request("DELETE", f"/api/erp/orders/{oid}/genei/shipment",
                       headers=auth_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert any(c == ("delete", "GEN123") for c in fake.calls)
    with session_factory() as s:
        assert json.loads(s.get(Order, oid).packing_json or "{}").get("genei") is None


def test_not_configured_returns_400(client, session_factory):
    # Sin carrier Genei con credenciales → 400 claro.
    with session_factory() as s:
        oid = _seed_order(s)
    r = client.post(f"/api/erp/orders/{oid}/genei/prices", headers=auth_headers(client), json={
        "destination": {"isoCountry": "ES", "postalCode": "28001", "town": "Madrid"},
        "packages": [],
    })
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "genei_not_configured"
