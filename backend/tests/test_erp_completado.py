"""ERP · «Marcar completado» (solo BoHub, reversible).

Estado FINAL del pedido — facturado y enviado, aunque el envío se tramite
fuera de BoHub. Manual (Bart manda): no exige transporte «enviado» ni factura
(avisa si no está facturado), se guarda con quién y cuándo, se ve y se filtra
en la bandeja y en el seguimiento, se desmarca, es idempotente y NUNCA toca
WooCommerce.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import (
    IntegrationEvent,
    InvoiceStatus,
    Order,
    OrderSource,
    TransportStatus,
)
from app.main import app
from app.models.crm import Company, ExternalSystem
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
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
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator:
    from fastapi.testclient import TestClient

    def override():
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _store(s: Session, slug: str = "boprint") -> IntegrationAccount:
    from app.core.crypto import encrypt

    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=slug,
        enabled=True, mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=f"https://{slug}.example",
        consumer_key_encrypted=encrypt("ck"), consumer_secret_encrypted=encrypt("cs"),
        credential_status="configured",
    )
    s.add(a)
    s.flush()
    return a


def _order(
    s: Session, number: str, *, cliente: str = "Cliente SL",
    woo_status: str | None = "processing", store: IntegrationAccount | None = None,
    invoiced: bool = False, shipped: bool = False,
    source: OrderSource = OrderSource.WOOCOMMERCE,
) -> str:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    o = Order(
        order_number=number, external_source=source, external_id=number.split("-")[-1],
        store_id=store.id if store else None, company_id=comp.id,
        woo_status=woo_status, placed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    if invoiced:
        o.invoice_status = InvoiceStatus.GENERATED
        o.factusol_invoice_number = f"5-{number.split('-')[-1]}"
    if shipped:
        o.transport_status = TransportStatus.DELIVERED
    s.add(o)
    s.flush()
    return o.id


def _complete(http, oid: str, role: str = "pedidos"):
    return http.post(f"/api/erp/orders/{oid}/complete", headers=auth_headers(http, role))


def _uncomplete(http, oid: str, role: str = "pedidos"):
    return http.post(f"/api/erp/orders/{oid}/uncomplete", headers=auth_headers(http, role))


def _bandeja(http, **params) -> list[dict]:
    r = http.get("/api/erp/orders", params=params, headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _seguimiento(http, **params) -> list[dict]:
    r = http.get("/api/erp/seguimiento", params=params, headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _numeros(items: list[dict]) -> set[str]:
    return {it["order_number"] for it in items}


# --- 1) se guarda como estado del pedido ------------------------------------


def test_marcar_completado_guarda_estado(session_factory, http) -> None:
    with session_factory() as s:
        oid = _order(s, "BOPRIN-71001", invoiced=True, shipped=True)
        s.commit()

    r = _complete(http, oid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["completed"] is True
    assert body["completed_at"] and body["completed_by_user_id"]
    assert body["completed_by_name"]
    assert body["already_completed"] is False
    assert body["completion_avisos"] == []  # facturado y entregado: sin avisos

    # Persistente (quién y cuándo) y visible en la ficha.
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.completed_at is not None and o.completed_by_user_id is not None
    detail = http.get(f"/api/erp/orders/{oid}", headers=auth_headers(http, "user")).json()
    assert detail["completed"] is True and detail["completed_by_name"]

    # Se ve en la bandeja (badge) y en el seguimiento (estado «completado»).
    (item,) = _bandeja(http)
    assert item["completed"] is True and item["completed_by_name"]
    (row,) = _seguimiento(http, estado="completado")
    assert row["order_number"] == "BOPRIN-71001"
    assert row["completado"] is True and row["completado_en"] and row["completado_por_nombre"]
    # Es el estado FINAL: ya no está «en curso».
    assert row["en_curso"] is False
    assert _seguimiento(http) == []


# --- 2) no exige envío ni factura; avisa si no está facturado -----------------


def test_completado_no_exige_envio_ni_factura_pero_avisa_sin_factura(
    session_factory, http,
) -> None:
    with session_factory() as s:
        nada = _order(s, "BOPRIN-72001")                       # ni factura ni envío
        solo_fact = _order(s, "BOPRIN-72002", invoiced=True)   # factura, sin envío
        todo = _order(s, "BOPRIN-72003", invoiced=True, shipped=True)
        s.commit()

    r = _complete(http, nada)
    assert r.status_code == 200, r.text  # NO bloquea
    assert r.json()["completed"] is True
    assert r.json()["completion_avisos"] == [
        "aún sin facturar", "el envío no consta como enviado en BoHub",
    ]

    r = _complete(http, solo_fact)
    assert r.status_code == 200 and r.json()["completed"] is True
    assert r.json()["completion_avisos"] == ["el envío no consta como enviado en BoHub"]

    r = _complete(http, todo)
    assert r.status_code == 200 and r.json()["completion_avisos"] == []

    # Los 4 estados del pedido no cambian: completar no es una transición.
    with session_factory() as s:
        o = s.get(Order, nada)
        assert o.transport_status == TransportStatus.NOT_SHIPPED
        assert o.invoice_status == InvoiceStatus.NOT_INVOICED
        assert o.completed_at is not None


# --- 3) reversible e idempotente ----------------------------------------------


def test_desmarcar_completado(session_factory, http) -> None:
    with session_factory() as s:
        oid = _order(s, "BOPRIN-73001", invoiced=True)
        s.commit()

    first = _complete(http, oid).json()
    stamp = first["completed_at"]
    # Marcar lo ya marcado: no rompe y conserva fecha y quién.
    again = _complete(http, oid).json()
    assert again["already_completed"] is True
    assert again["completed_at"] == stamp
    assert again["completed_by_user_id"] == first["completed_by_user_id"]

    # Desmarcar: vuelve a no-completado (campos limpios) y a la bandeja/seguimiento.
    r = _uncomplete(http, oid)
    assert r.status_code == 200, r.text
    assert r.json()["completed"] is False
    assert r.json()["already_uncompleted"] is False
    assert r.json()["completed_at"] is None and r.json()["completed_by_user_id"] is None
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.completed_at is None and o.completed_by_user_id is None
    assert _numeros(_seguimiento(http)) == {"BOPRIN-73001"}  # otra vez «en curso»
    assert _seguimiento(http, estado="completado") == []

    # Desmarcar lo no marcado: no rompe.
    r = _uncomplete(http, oid)
    assert r.status_code == 200 and r.json()["already_uncompleted"] is True
    # Y se puede volver a marcar.
    assert _complete(http, oid).json()["completed"] is True


# --- 4) filtro por completado en la bandeja -----------------------------------


def test_filtro_completado_en_bandeja(session_factory, http) -> None:
    with session_factory() as s:
        done = _order(s, "BOPRIN-74001", invoiced=True, shipped=True)
        _order(s, "BOPRIN-74002")
        s.commit()
    assert _complete(http, done).status_code == 200

    assert _numeros(_bandeja(http)) == {"BOPRIN-74001", "BOPRIN-74002"}  # todos
    assert _numeros(_bandeja(http, completed="true")) == {"BOPRIN-74001"}
    assert _numeros(_bandeja(http, completed="false")) == {"BOPRIN-74002"}
    # Combina con los filtros que ya había (pago/preparación).
    assert _numeros(_bandeja(http, completed="true", payment="pending")) == {"BOPRIN-74001"}
    assert _bandeja(http, completed="true", payment="paid") == []
    # Seguimiento: «completado» como estado; «en curso» ya no lo incluye.
    assert _numeros(_seguimiento(http, estado="completado")) == {"BOPRIN-74001"}
    assert _numeros(_seguimiento(http)) == {"BOPRIN-74002"}
    assert _numeros(_seguimiento(http, en_curso="false")) == {"BOPRIN-74001", "BOPRIN-74002"}


# --- 5) NO toca WooCommerce --------------------------------------------------


def test_completado_no_toca_woocommerce(session_factory, http) -> None:
    with session_factory() as s:
        store = _store(s)
        oid = _order(s, "BOPRIN-75001", store=store, woo_status="processing", invoiced=True)
        s.commit()
        events_before = s.scalar(select(func.count(IntegrationEvent.id))) or 0

    with patch("app.integrations.woocommerce.client.WooHTTPClient") as client_cls, \
         patch("app.integrations.woocommerce.jobs.WooHTTPClient") as jobs_cls:
        assert _complete(http, oid).status_code == 200
        assert _uncomplete(http, oid).status_code == 200
        assert _complete(http, oid).status_code == 200
    # Ni se instancia el cliente ni se llama a update_order: solo BoHub.
    assert client_cls.call_count == 0 and jobs_cls.call_count == 0
    assert client_cls.return_value.update_order.call_count == 0

    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.woo_status == "processing"          # el estado de la tienda no cambia
        assert o.completed_at is not None
        assert (s.scalar(select(func.count(IntegrationEvent.id))) or 0) == events_before


def test_completado_requiere_permiso_edicion(session_factory, http) -> None:
    with session_factory() as s:
        oid = _order(s, "BOPRIN-76001")
        s.commit()
    assert _complete(http, oid, role="user").status_code in (401, 403)
    assert _uncomplete(http, oid, role="user").status_code in (401, 403)
    assert _complete(http, "no-existe").status_code == 404
