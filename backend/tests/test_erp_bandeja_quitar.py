"""ERP · Pedidos (bandeja) — «Quitar» desde la bandeja, unificado con #388.

Un solo flag (`seguimiento_excluded_at`, F6-fix7) saca el pedido de TODAS las
listas de trabajo: bandeja de Pedidos, Cola PEDIDOS y seguimiento/Drive. Se
ve en «Ver ocultados» con motivo y quién; «Reincluir» lo devuelve a todas.
Cualquier estado; con factura/cobro/albarán avisa pero permite. Idempotente.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import (
    InvoiceStatus,
    Order,
    OrderSource,
    PaymentStatus,
    PreparationStatus,
    ShipmentPackage,
)
from app.main import app
from app.models.crm import Company
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


def _order(
    s: Session, number: str, *, cliente: str = "Cliente SL",
    woo_status: str | None = None, invoiced: bool = False, paid: bool = False,
    bulto: bool = False, preparation: PreparationStatus | None = None,
    source: OrderSource = OrderSource.WOOCOMMERCE,
) -> str:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    o = Order(
        order_number=number, external_source=source, external_id=number.split("-")[-1],
        company_id=comp.id, woo_status=woo_status,
        placed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    if invoiced:
        o.invoice_status = InvoiceStatus.GENERATED
        o.factusol_invoice_number = f"5-{number.split('-')[-1]}"
    if paid:
        o.payment_status = PaymentStatus.PAID
    if preparation is not None:
        o.preparation_status = preparation
    s.add(o)
    s.flush()
    if bulto:
        s.add(ShipmentPackage(order_id=o.id, weight_kg=1, height_cm=10,
                              width_cm=10, depth_cm=10))
    s.flush()
    return o.id


def _bandeja(http, **params) -> list[dict]:
    r = http.get("/api/erp/orders", params=params, headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _numeros(items: list[dict]) -> set[str]:
    return {it["order_number"] for it in items}


def _cola(http) -> set[str]:
    r = http.get("/api/erp/orders/pending-approval", headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    return _numeros(r.json()["items"])


def _seguimiento(http, **params) -> set[str]:
    r = http.get("/api/erp/seguimiento", params=params, headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    return _numeros(r.json()["items"])


def _exclude(http, ids: list[str], **body) -> dict:
    r = http.post("/api/erp/seguimiento/exclude", json={"order_ids": ids, **body},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    return r.json()


def _include(http, ids: list[str]) -> dict:
    r = http.post("/api/erp/seguimiento/include", json={"order_ids": ids},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    return r.json()


def _preview(http, ids: list[str]) -> dict:
    r = http.post("/api/erp/seguimiento/exclude-preview", json={"order_ids": ids},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    return r.json()


# --- 1) quitar desde la bandeja: desaparece de la bandeja ----------------------


def test_quitar_desde_bandeja_oculta_de_bandeja(session_factory, http) -> None:
    with session_factory() as s:
        prueba = _order(s, "BOPRIN-91001", cliente="probando agile", woo_status="on-hold")
        _order(s, "BOPRIN-91002", cliente="Cliente Real SL", woo_status="processing")
        s.commit()

    # Antes: los dos en la bandeja, ninguno oculto.
    assert _numeros(_bandeja(http)) == {"BOPRIN-91001", "BOPRIN-91002"}
    assert _bandeja(http, show_excluded="true") == []

    res = _exclude(http, [prueba], reason_code="prueba", reason="pedido de prueba")
    assert res["excluded"] == 1

    # Después: fuera de la bandeja por defecto (también con «mostrar externalizados»).
    assert _numeros(_bandeja(http)) == {"BOPRIN-91002"}
    assert _numeros(_bandeja(http, show_external="true")) == {"BOPRIN-91002"}
    # …y SOLO él en «Ver ocultados», con motivo, fecha y quién.
    ocultos = _bandeja(http, show_excluded="true")
    assert _numeros(ocultos) == {"BOPRIN-91001"}
    (it,) = ocultos
    assert it["excluded"] is True
    assert it["seguimiento_excluded_reason"] == "prueba: pedido de prueba"
    assert it["seguimiento_excluded_at"]
    assert it["seguimiento_excluded_by_name"]
    # Los visibles no llevan la marca.
    assert all(x["excluded"] is False for x in _bandeja(http))
    # La ficha del pedido sigue accesible (no se borra nada).
    r = http.get(f"/api/erp/orders/{prueba}", headers=auth_headers(http, "user"))
    assert r.status_code == 200 and r.json()["excluded"] is True


# --- 2) un solo flag: bandeja + Cola PEDIDOS + seguimiento --------------------


def test_quitar_unifica_bandeja_y_seguimiento(session_factory, http) -> None:
    with session_factory() as s:
        oid = _order(s, "BOPRIN-92001", woo_status="processing")  # pending_review → Cola
        s.commit()

    # Visible en las tres listas de trabajo.
    assert "BOPRIN-92001" in _numeros(_bandeja(http))
    assert "BOPRIN-92001" in _cola(http)
    assert "BOPRIN-92001" in _seguimiento(http)

    # Quitar (desde donde sea: es el mismo endpoint y el mismo flag).
    assert _exclude(http, [oid], reason_code="duplicado")["excluded"] == 1
    assert "BOPRIN-92001" not in _numeros(_bandeja(http))
    assert "BOPRIN-92001" not in _cola(http)
    assert "BOPRIN-92001" not in _seguimiento(http)
    assert "BOPRIN-92001" not in _seguimiento(http, en_curso="false")
    # Y aparece en las vistas de revisión de ambas pantallas, con el mismo motivo.
    (band,) = _bandeja(http, show_excluded="true")
    assert band["seguimiento_excluded_reason"] == "duplicado"
    assert _seguimiento(http, ver_excluidos="true") == {"BOPRIN-92001"}
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.seguimiento_excluded_at is not None  # un único campo para todo

    # Reincluir lo devuelve a las tres.
    assert _include(http, [oid])["included"] == 1
    assert "BOPRIN-92001" in _numeros(_bandeja(http))
    assert "BOPRIN-92001" in _cola(http)
    assert "BOPRIN-92001" in _seguimiento(http)
    assert _bandeja(http, show_excluded="true") == []
    assert _seguimiento(http, ver_excluidos="true") == set()


# --- 3) cualquier estado; facturado/cobrado avisa pero permite ----------------


def test_quitar_cualquier_estado_y_facturado_avisa_pero_permite(session_factory, http) -> None:
    with session_factory() as s:
        ids = {
            "facturado": _order(s, "BOPRIN-93001", woo_status="completed",
                                invoiced=True, paid=True, bulto=True,
                                preparation=PreparationStatus.PACKED),
            "on-hold": _order(s, "BOPRIN-93002", woo_status="on-hold"),
            "cancelled": _order(s, "BOPRIN-93003", woo_status="cancelled"),
            "sin_estado": _order(s, "BOPRIN-93004", woo_status=None),
            "manual": _order(s, "MANUAL-000093", source=OrderSource.MANUAL),
        }
        s.commit()

    # El facturado/cobrado/embalado AVISA (no bloquea, no escribe).
    pre = _preview(http, [ids["facturado"], ids["on-hold"]])
    by_num = {it["order_number"]: it for it in pre["items"]}
    assert by_num["BOPRIN-93001"]["avisos"] == [
        "facturado", "cobrado/pagado", "albarán/envío", "en preparación (packed)",
    ]
    assert by_num["BOPRIN-93002"]["avisos"] == []
    assert _bandeja(http, show_excluded="true") == []  # la preview no cambia nada

    # Se quitan TODOS (cualquier estado, también facturado): la bandeja queda vacía.
    res = _exclude(http, list(ids.values()), reason_code="cancelado")
    assert res["excluded"] == 5 and res["already_excluded"] == 0
    assert list(res["avisos"]) == ["BOPRIN-93001"]
    assert _bandeja(http) == []
    assert _numeros(_bandeja(http, show_excluded="true")) == {
        "BOPRIN-93001", "BOPRIN-93002", "BOPRIN-93003", "BOPRIN-93004", "MANUAL-000093",
    }
    with session_factory() as s:
        o = s.get(Order, ids["facturado"])
        # Factura, cobro y preparación intactos: solo la exclusión cambió.
        assert o.factusol_invoice_number == "5-93001"
        assert o.invoice_status == InvoiceStatus.GENERATED
        assert o.payment_status == PaymentStatus.PAID
        assert o.preparation_status == PreparationStatus.PACKED
        assert o.seguimiento_excluded_reason == "cancelado"


# --- 4) reincluir deshace; idempotente ----------------------------------------


def test_reincluir_deshace(session_factory, http) -> None:
    with session_factory() as s:
        oid = _order(s, "BOPRIN-94001", woo_status="refunded", invoiced=True)
        s.commit()

    first = _exclude(http, [oid], reason_code="reembolsado", reason="devuelto")
    assert first["excluded"] == 1
    assert _bandeja(http) == []
    with session_factory() as s:
        o = s.get(Order, oid)
        stamp, reason = o.seguimiento_excluded_at, o.seguimiento_excluded_reason
        assert reason == "reembolsado: devuelto"

    # Quitar lo ya quitado: no rompe y NO pisa fecha ni motivo.
    again = _exclude(http, [oid], reason_code="prueba")
    assert again["excluded"] == 0 and again["already_excluded"] == 1
    with session_factory() as s:
        o = s.get(Order, oid)
        assert (o.seguimiento_excluded_at, o.seguimiento_excluded_reason) == (stamp, reason)

    # Reincluir: vuelve a la bandeja y al seguimiento; campos limpios.
    res = _include(http, [oid])
    assert res["included"] == 1 and res["already_included"] == 0
    assert _numeros(_bandeja(http)) == {"BOPRIN-94001"}
    assert _bandeja(http, show_excluded="true") == []
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.seguimiento_excluded_at is None
        assert o.seguimiento_excluded_by_user_id is None
        assert o.seguimiento_excluded_reason is None
        assert o.factusol_invoice_number == "5-94001"

    # Reincluir lo ya incluido: no rompe.
    res2 = _include(http, [oid])
    assert res2["included"] == 0 and res2["already_included"] == 1
    # Y se puede volver a quitar.
    assert _exclude(http, [oid])["excluded"] == 1
    assert _bandeja(http) == []


def test_bandeja_respeta_filtros_en_ver_ocultados(session_factory, http) -> None:
    """«Ver ocultados» combina con los filtros de estado de la bandeja."""
    with session_factory() as s:
        a = _order(s, "BOPRIN-95001", paid=True)
        b = _order(s, "BOPRIN-95002")
        s.commit()
    _exclude(http, [a, b])
    assert _numeros(_bandeja(http, show_excluded="true", payment="paid")) == {"BOPRIN-95001"}
    assert _numeros(_bandeja(http, show_excluded="true", payment="pending")) == {"BOPRIN-95002"}
    # Solo lectura puede ver, pero no quitar.
    r = http.post("/api/erp/seguimiento/exclude", json={"order_ids": [a]},
                  headers=auth_headers(http, "user"))
    assert r.status_code in (401, 403)


def test_cola_sat_tambien_oculta_quitados(session_factory, http) -> None:
    """«Fuera de mis listas de trabajo» incluye la cola del taller (SAT)."""
    with session_factory() as s:
        en_cola = _order(s, "BOPRIN-96001", preparation=PreparationStatus.IN_QUEUE)
        embalado = _order(s, "BOPRIN-96002", preparation=PreparationStatus.PACKED)
        s.commit()

    def sat_text() -> str:
        r = http.get("/api/erp/sat/queue", headers=auth_headers(http, "sat"))
        assert r.status_code == 200, r.text
        return r.text

    assert "BOPRIN-96001" in sat_text() and "BOPRIN-96002" in sat_text()
    _exclude(http, [en_cola, embalado], reason_code="prueba")
    assert "BOPRIN-96001" not in sat_text() and "BOPRIN-96002" not in sat_text()
    _include(http, [en_cola, embalado])
    assert "BOPRIN-96001" in sat_text() and "BOPRIN-96002" in sat_text()
