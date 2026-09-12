"""ERP · «Completar seleccionados» en la bandeja — bulk del «Marcar completado».

Mismo patrón que «Quitar de la bandeja» (selección + endpoint bulk) y la
MISMA lógica que el completado individual de #390 (`mark_order_completed`):
`completed_at` + `completed_by_user_id`, solo BoHub (nunca WooCommerce),
reversible, idempotente. No exige factura ni envío: avisa por fila. Si un
pedido falla, se informa y el resto se completa igualmente.
"""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.api import orders as orders_api
from app.erp.models import IntegrationEvent, InvoiceStatus, Order, TransportStatus
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_completado import _order, _store


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


def _bulk(http, ids: list[str], role: str = "pedidos"):
    return http.post("/api/erp/orders/bulk-complete", json={"order_ids": ids},
                     headers=auth_headers(http, role))


def _complete(http, oid: str):
    return http.post(f"/api/erp/orders/{oid}/complete", headers=auth_headers(http, "pedidos"))


def _bandeja(http, **params) -> list[dict]:
    r = http.get("/api/erp/orders", params=params, headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_completar_seleccionados_marca_todos(session_factory, http) -> None:
    """Varios pedidos seleccionados → todos completados de una vez, con quién
    y cuándo, y visibles como completados en la bandeja y el seguimiento.
    Solo con permiso de edición ERP."""
    with session_factory() as s:
        ids = [
            _order(s, f"BOPRIN-8100{i}", invoiced=True, shipped=True) for i in range(1, 4)
        ]
        s.commit()

    assert _bulk(http, ids, role="user").status_code in (401, 403)

    r = _bulk(http, ids)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["completed"] == 3 and body["already_completed"] == 0
    assert body["failed"] == [] and body["sin_facturar"] == 0
    assert {it["id"] for it in body["items"]} == set(ids)
    for it in body["items"]:
        assert it["completed"] is True and it["completed_at"] and it["completed_by_user_id"]
        assert it["completed_by_name"]
        assert it["already_completed"] is False and it["completion_avisos"] == []

    with session_factory() as s:
        rows = s.scalars(select(Order).where(Order.id.in_(ids))).all()
        assert all(o.completed_at is not None for o in rows)
        assert len({o.completed_by_user_id for o in rows}) == 1   # el mismo operador
    assert {it["order_number"] for it in _bandeja(http, completed="true")} == {
        "BOPRIN-81001", "BOPRIN-81002", "BOPRIN-81003",
    }
    seg = http.get("/api/erp/seguimiento", params={"estado": "completado"},
                   headers=auth_headers(http, "user")).json()["items"]
    assert len(seg) == 3 and all(row["completado"] for row in seg)


def test_completar_seleccionados_idempotente(session_factory, http) -> None:
    """Incluir pedidos ya completados (o el mismo id dos veces) no rompe ni
    duplica: se cuentan aparte y conservan su fecha y quién."""
    with session_factory() as s:
        a = _order(s, "BOPRIN-82001", invoiced=True)
        b = _order(s, "BOPRIN-82002", invoiced=True)
        s.commit()
    first = _complete(http, a).json()
    stamp, who = first["completed_at"], first["completed_by_user_id"]

    r = _bulk(http, [a, b, a])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["completed"] == 1 and body["already_completed"] == 1
    assert body["failed"] == [] and len(body["items"]) == 2
    by_id = {it["id"]: it for it in body["items"]}
    assert by_id[a]["already_completed"] is True
    assert by_id[a]["completed_at"] == stamp and by_id[a]["completed_by_user_id"] == who
    assert by_id[b]["already_completed"] is False and by_id[b]["completed"] is True

    again = _bulk(http, [a, b]).json()
    assert again["completed"] == 0 and again["already_completed"] == 2
    assert again["failed"] == []
    with session_factory() as s:
        assert s.get(Order, a).completed_at.isoformat().startswith(stamp[:19])


def test_completar_seleccionados_avisa_sin_factura_pero_permite(
    session_factory, http,
) -> None:
    """Igual que el individual: no exige factura ni envío. Avisa por fila
    («aún sin facturar», «el envío no consta como enviado») y el resumen
    cuenta cuántos van sin factura, pero NO bloquea. Los 4 estados no cambian."""
    with session_factory() as s:
        nada = _order(s, "BOPRIN-83001")                       # ni factura ni envío
        solo_fact = _order(s, "BOPRIN-83002", invoiced=True)   # factura, sin envío
        todo = _order(s, "BOPRIN-83003", invoiced=True, shipped=True)
        s.commit()

    r = _bulk(http, [nada, solo_fact, todo])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["completed"] == 3 and body["failed"] == []
    assert body["sin_facturar"] == 1
    avisos = {it["order_number"]: it["completion_avisos"] for it in body["items"]}
    assert avisos["BOPRIN-83001"] == [
        "aún sin facturar", "el envío no consta como enviado en BoHub",
    ]
    assert avisos["BOPRIN-83002"] == ["el envío no consta como enviado en BoHub"]
    assert avisos["BOPRIN-83003"] == []
    with session_factory() as s:
        o = s.get(Order, nada)
        assert o.completed_at is not None
        assert o.transport_status == TransportStatus.NOT_SHIPPED
        assert o.invoice_status == InvoiceStatus.NOT_INVOICED


def test_completar_seleccionados_no_toca_woocommerce(session_factory, http) -> None:
    """Solo BoHub: ni se instancia el cliente Woo ni cambia `woo_status`, y
    no se genera ningún evento de integración. Reversible uno a uno."""
    with session_factory() as s:
        store = _store(s)
        a = _order(s, "BOPRIN-84001", store=store, woo_status="processing", invoiced=True)
        b = _order(s, "BOPRIN-84002", store=store, woo_status="completed", invoiced=True)
        s.commit()
        events_before = s.scalar(select(func.count(IntegrationEvent.id))) or 0

    with patch("app.integrations.woocommerce.client.WooHTTPClient") as client_cls, \
         patch("app.integrations.woocommerce.jobs.WooHTTPClient") as jobs_cls:
        assert _bulk(http, [a, b]).status_code == 200
    assert client_cls.call_count == 0 and jobs_cls.call_count == 0
    assert client_cls.return_value.update_order.call_count == 0
    with session_factory() as s:
        assert s.get(Order, a).woo_status == "processing"
        assert s.get(Order, b).woo_status == "completed"
        assert s.get(Order, a).completed_at is not None
        assert (s.scalar(select(func.count(IntegrationEvent.id))) or 0) == events_before

    # Reversible con el «Desmarcar» de siempre.
    r = http.post(f"/api/erp/orders/{a}/uncomplete", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200 and r.json()["completed"] is False


def test_completar_seleccionados_parcial_informa_fallos(session_factory, http) -> None:
    """Si un pedido falla (no existe, o error al guardar), se informa en
    `failed` y el resto se completa igualmente: nunca se aborta todo por uno."""
    with session_factory() as s:
        a = _order(s, "BOPRIN-85001", invoiced=True)
        b = _order(s, "BOPRIN-85002", invoiced=True)
        c = _order(s, "BOPRIN-85003", invoiced=True)
        s.commit()

    real = orders_api.mark_order_completed

    def flaky(session, order, actor):
        if order.order_number == "BOPRIN-85002":
            raise RuntimeError("fallo simulado al guardar")
        return real(session, order, actor)

    with patch.object(orders_api, "mark_order_completed", side_effect=flaky):
        r = _bulk(http, [a, "no-existe", b, c])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["completed"] == 2 and body["already_completed"] == 0
    assert {f["order_id"]: f["error"] for f in body["failed"]} == {
        "no-existe": "El pedido no existe.",
        b: "fallo simulado al guardar",
    }
    assert {it["id"] for it in body["items"]} == {a, c}
    with session_factory() as s:
        assert s.get(Order, a).completed_at is not None
        assert s.get(Order, c).completed_at is not None
        assert s.get(Order, b).completed_at is None        # el fallido queda como estaba

    # Sin el fallo, el que quedó pendiente se completa en el siguiente intento.
    again = _bulk(http, [b]).json()
    assert again["ok"] is True and again["completed"] == 1
