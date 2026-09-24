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
        self.created = {"shipmentCode": "GEN123", "paymentUrl": "https://pay/x", "status": 7}
        self.shipment = {"shipmentCode": "GEN123", "estado": 5,
                         "codigo_seguimiento": "TRK-9", "nombre_agencia": "GLS"}

    def agency_prices(self, **kw):
        self.calls.append(("prices", kw))
        return self.prices

    def create_shipment(self, payload):
        self.calls.append(("create", payload))
        return self.created

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
    assert body["summary"]["shipment_code"] == "GEN123"
    assert body["state"]["shipment_code"] == "GEN123"
    assert body["state"]["payment_url"] == "https://pay/x"
    # El payload llevó el nº de pedido como externalShippingCode.
    create_call = next(c for c in fake.calls if c[0] == "create")
    assert create_call[1]["externalShippingCode"] == "MAN-0001"
    assert create_call[1]["originAddressId"] == "1304422"
    assert "notificationUrl" not in create_call[1]      # PR-1 sin webhook
    with session_factory() as s:
        order = s.get(Order, oid)
        assert order.carrier_id is not None


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
