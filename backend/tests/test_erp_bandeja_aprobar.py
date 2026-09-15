"""ERP · Lote 2 D — «Aprobar seleccionados» en la bandeja (bulk de `/approve`).

La Cola PEDIDOS deja de ser una pantalla aparte: se aprueba desde la cola
«Por revisar» de la bandeja, uno a uno (`/approve`, el de siempre) o en
bloque (`/bulk-approve`). El bulk sigue el patrón de `bulk-complete`: cada
pedido se confirma por separado, los bloqueados (excepciones abiertas) y los
inexistentes van a `failed` con su motivo, los que ya no estaban pendientes
se cuentan aparte y NUNCA se aborta todo por uno.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import ErpException, ExceptionType, Order
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users

SEED_COMPANY_ID = "seed-company-aprobar"


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
        # El alta manual exige empresa vinculada a FACTUSOL (Tarea B).
        seed.add(Company(id=SEED_COMPANY_ID, name="Cliente Aprobar SL",
                         factusol_company_id="55555"))
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


def _create(client, number: str) -> str:
    """Pedido manual → nace `pending_review` (cola «Por revisar»)."""
    r = client.post("/api/erp/orders", json={
        "order_number": number,
        "company_id": SEED_COMPANY_ID,
        "lines": [{"product_sku": "SKU-ROT", "product_codart": "ROT01",
                   "description": "Rotativo", "quantity": 1, "unit_price": 195}],
    }, headers=auth_headers(client, "pedidos"))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _bulk(client, ids: list[str], role: str = "pedidos"):
    return client.post("/api/erp/orders/bulk-approve", json={"order_ids": ids},
                       headers=auth_headers(client, role))


def _bandeja(client, **params) -> list[dict]:
    r = client.get("/api/erp/orders", params=params, headers=auth_headers(client, "user"))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_bulk_approve_aprueba_todos_y_salen_de_por_revisar(client, session_factory) -> None:
    ids = [_create(client, f"MAN-AP{i}") for i in range(1, 4)]
    assert {o["id"] for o in _bandeja(client, queue="por_revisar")} == set(ids)

    r = _bulk(client, ids)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["approved"] == 3 and body["already_approved"] == 0
    assert body["failed"] == []
    assert {it["id"] for it in body["items"]} == set(ids)
    for it in body["items"]:
        assert it["preparation_status"] == "in_queue"
        assert it["approved_at"]
        # El bloque `workflow` viene actualizado: ya no toca aprobar.
        assert it["workflow"]["queue"] != "por_revisar"
        assert it["workflow"]["next_action"] != "aprobar"

    with session_factory() as s:
        rows = s.scalars(select(Order).where(Order.id.in_(ids))).all()
        assert all(o.approved_at is not None and o.approved_by_user_id for o in rows)
        assert len({o.approved_by_user_id for o in rows}) == 1
    # Fuera de «Por revisar» (y de la cola de aprobación, que sigue existiendo).
    assert _bandeja(client, queue="por_revisar") == []
    cola = client.get("/api/erp/orders/pending-approval",
                      headers=auth_headers(client, "user")).json()["items"]
    assert cola == []


def test_bulk_approve_bloqueado_va_a_failed_con_el_motivo(client, session_factory) -> None:
    """Una excepción operativa abierta bloquea ESE pedido (como el 409 de
    `/approve`); el resto se aprueba igualmente y `ok` es False."""
    limpio = _create(client, "MAN-AP-OK")
    bloqueado = _create(client, "MAN-AP-BLK")
    with session_factory() as s:
        s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id=bloqueado))
        s.commit()

    r = _bulk(client, [limpio, bloqueado])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["approved"] == 1 and body["already_approved"] == 0
    assert [f["order_id"] for f in body["failed"]] == [bloqueado]
    (fallo,) = body["failed"]
    assert fallo["code"] == "blocked"
    assert "excepción(es) sin resolver" in fallo["error"]
    assert {it["id"] for it in body["items"]} == {limpio}
    with session_factory() as s:
        assert s.get(Order, limpio).preparation_status == "in_queue"
        assert s.get(Order, bloqueado).preparation_status == "pending_review"
        assert s.get(Order, bloqueado).approved_at is None


def test_bulk_approve_inexistente_y_ya_aprobado(client) -> None:
    """Un id que no existe se informa en `failed`; el ya aprobado (o el mismo
    id repetido) no rompe ni se re-aprueba: cuenta como `already_approved`."""
    a = _create(client, "MAN-AP-A")
    b = _create(client, "MAN-AP-B")
    first = client.post(f"/api/erp/orders/{a}/approve", headers=auth_headers(client, "pedidos"))
    assert first.status_code == 200, first.text
    stamp = first.json()["approved_at"]

    r = _bulk(client, [a, "no-existe", b, b])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["approved"] == 1 and body["already_approved"] == 1
    assert body["failed"] == [{"order_id": "no-existe", "error": "El pedido no existe."}]
    assert [it["id"] for it in body["items"]] == [b]
    # El ya aprobado conserva su sello.
    ficha = client.get(f"/api/erp/orders/{a}", headers=auth_headers(client, "user")).json()
    assert ficha["approved_at"] == stamp

    again = _bulk(client, [a, b]).json()
    assert again["ok"] is True
    assert again["approved"] == 0 and again["already_approved"] == 2


def test_bulk_approve_permisos_y_payload(client) -> None:
    """Mismo permiso que `/approve` (admin / pedidos); el cuerpo exige ids."""
    oid = _create(client, "MAN-AP-PERM")
    for role in ("sat", "manager", "user"):
        assert _bulk(client, [oid], role=role).status_code == 403, role
    r = client.post("/api/erp/orders/bulk-approve", json={"order_ids": []},
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 422
    assert _bulk(client, [oid], role="admin").status_code == 200
