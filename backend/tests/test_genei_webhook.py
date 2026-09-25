"""Webhook de estados de Genei (PR-2): seguridad (secreto cifrado, comparación
constante), idempotencia y mapeo de estados Genei → transport_status del pedido
(fuente común para ficha, Cola SAT y hoja de Seguimiento)."""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.integrations.genei.client import GeneiClient
from app.erp.models import Order, OrderStatusHistory
from app.erp.models.carriers import Carrier
from app.main import app
from tests._test_helpers import seed_test_users

SECRET = "topsecret-webhook-token"


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
        seed.commit()
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


def _carrier(s: Session, *, secret: str | None = SECRET) -> None:
    s.add(Carrier(
        name="Genei", code="genei", has_api=True, api_base_url="https://apiv2.genei.es",
        api_credentials_encrypted=GeneiClient.encode_credentials(
            "sat@b.es", "pw", webhook_secret=secret),
    ))


def _order(s: Session, *, number="BOP-1", transport="label_created", code="GEN9") -> str:
    o = Order(
        order_number=number, external_source="manual",
        preparation_status="packed", transport_status=transport, tracking_number="TRK-1",
    )
    o.packing_json = json.dumps({"genei": {"shipment_code": code}})
    s.add(o)
    s.commit()
    return o.id


def _payload(*, code="GEN9", ext="BOP-1", estado=5, tracking="TRK-9", desc="") -> dict:
    data = {"codigo_envio": code, "codigo_envio_externo": ext, "estado": estado,
            "codigo_seguimiento": tracking, "nombre_agencia": "GLS"}
    if desc:
        data["desc_incidencia"] = desc
    return {"status": 1, "message": "", "data": data}


def _transport(s: Session, oid: str) -> str:
    return s.get(Order, oid).transport_status.value


# --- seguridad --------------------------------------------------------------


def test_webhook_rejects_bad_token(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        _order(s)
        s.commit()
    r = client.post("/api/webhooks/genei?token=wrong", json=_payload())
    assert r.status_code == 401


def test_webhook_rejects_when_no_secret_configured(client, session_factory):
    with session_factory() as s:
        _carrier(s, secret=None)  # credenciales sin secreto de webhook
        _order(s)
        s.commit()
    r = client.post("/api/webhooks/genei?token=whatever", json=_payload())
    assert r.status_code == 401


# --- mapeo de estados → transporte ------------------------------------------


def test_webhook_recogido_no_mueve_de_pestana(client, session_factory):
    """Genei 5 («recogida efectuada / en tránsito») se guarda y se enseña, pero
    NO pasa el pedido a «Enviados»: eso lo hace una persona con «📤 Marcar
    recogido». Solo una incidencia mueve el pedido solo."""
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, transport="label_created")
        s.commit()
    r = client.post(f"/api/webhooks/genei?token={SECRET}", json=_payload(estado=5))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] is True and body["order_id"] == oid
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.transport_status.value == "label_created"
        assert o.tracking_number == "TRK-9"          # el webhook actualiza el tracking
        assert json.loads(o.packing_json)["genei"]["state_code"] == 5


def test_webhook_entregado_sin_marcar_recogido_no_mueve(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, transport="label_created")
        s.commit()
    r = client.post(f"/api/webhooks/genei?token={SECRET}", json=_payload(estado=3))
    assert r.status_code == 200
    with session_factory() as s:
        assert _transport(s, oid) == "label_created"


def test_webhook_incidencia_mueve_aunque_no_se_haya_marcado_recogido(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, transport="label_created")
        s.commit()
    r = client.post(f"/api/webhooks/genei?token={SECRET}",
                    json=_payload(estado=9, desc="Recogida fallida"))
    assert r.status_code == 200
    with session_factory() as s:
        assert _transport(s, oid) == "incident"


def test_webhook_entregado_moves_to_delivered(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, transport="in_transit")
        s.commit()
    r = client.post(f"/api/webhooks/genei?token={SECRET}", json=_payload(estado=3))
    assert r.status_code == 200
    with session_factory() as s:
        assert _transport(s, oid) == "delivered"


def test_webhook_incidencia_de_envio_moves_to_incident(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, transport="in_transit")
        s.commit()
    r = client.post(f"/api/webhooks/genei?token={SECRET}",
                    json=_payload(estado=10, desc="Paquete roto en tránsito"))
    assert r.status_code == 200
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.transport_status.value == "incident"
        # La descripción de la incidencia de TRANSPORTE queda en el bloque genei.
        block = json.loads(o.packing_json)["genei"]
        assert block["desc_incidencia"] == "Paquete roto en tránsito"


def test_webhook_is_idempotent(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, transport="in_transit")
        s.commit()
    for _ in range(2):
        r = client.post(f"/api/webhooks/genei?token={SECRET}", json=_payload(estado=3))
        assert r.status_code == 200
    with session_factory() as s:
        assert _transport(s, oid) == "delivered"
        # Un solo arco `delivered` en el historial: el segundo webhook no aplica nada.
        n = s.scalar(
            select(OrderStatusHistory.id).where(
                OrderStatusHistory.order_id == oid,
                OrderStatusHistory.to_status == "delivered",
            ).limit(2)
        )
        assert n is not None
        count = len(s.scalars(
            select(OrderStatusHistory).where(
                OrderStatusHistory.order_id == oid,
                OrderStatusHistory.to_status == "delivered",
            )
        ).all())
        assert count == 1


def test_webhook_unknown_order_is_acked(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        _order(s)
        s.commit()
    r = client.post(f"/api/webhooks/genei?token={SECRET}",
                    json=_payload(ext="NOPE-999", code="ZZZ"))
    assert r.status_code == 200
    assert r.json()["matched"] is False


def test_webhook_matches_by_shipment_code_when_no_external(client, session_factory):
    with session_factory() as s:
        _carrier(s)
        oid = _order(s, number="BOP-7", transport="in_transit", code="ABC123")
        s.commit()
    # Sin codigo_envio_externo: se localiza por el codigo_envio del bloque genei.
    payload = _payload(code="ABC123", estado=3)
    payload["data"].pop("codigo_envio_externo")
    r = client.post(f"/api/webhooks/genei?token={SECRET}", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True and body["order_id"] == oid
