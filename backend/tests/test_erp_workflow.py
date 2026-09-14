"""ERP · rediseño de flujo (Fase 1) — el bloque `workflow` del pedido.

Es el único sitio que decide «lo que toca»: cola, siguiente acción y alertas.
Se comprueba con los casos típicos del día a día (los de la maqueta) que la
cola y la acción salen bien, que las incidencias mandan sobre el ciclo, y que
bandeja y ficha reciben EL MISMO bloque.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import ErpException, ExceptionStatus, Order, OrderLine, OrderSource
from app.erp.workflow import order_workflow
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
        seed.add_all([
            Company(id="es", name="Duplicoder SL", country="ES",
                    tax_id="B12345678", factusol_company_id="2458"),
            Company(id="fr", name="La Maison de la Plaque", country="FR",
                    tax_id="FR16339753527", vat="FR16339753527",
                    factusol_company_id="2760"),
            Company(id="sinlink", name="Sin FACTUSOL SL", country="ES"),
        ])
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _order(
    s: Session, *, oid: str, number: str, company_id: str | None = "es",
    lines: list[dict[str, Any]] | None = None, **fields: Any,
) -> Order:
    o = Order(
        id=oid, order_number=number, company_id=company_id,
        external_source=fields.pop("external_source", OrderSource.WOOCOMMERCE),
        total_amount=fields.pop("total_amount", 100.0), currency="EUR",
        **fields,
    )
    s.add(o)
    s.flush()
    for i, line in enumerate(lines or [{"sku": "99cy", "codart": "99cy"}]):
        s.add(OrderLine(
            order_id=oid, position=i, product_sku=line.get("sku", ""),
            product_codart=line.get("codart"), description="Tinta",
            quantity=1, unit_price=100, line_total=100,
        ))
    s.commit()
    return o


def _wf(session_factory, oid: str) -> dict[str, Any]:
    with session_factory() as s:
        return order_workflow(s, s.get(Order, oid))


# --- colas + siguiente acción ------------------------------------------------


def test_workflow_por_revisar(session_factory) -> None:
    """Pedido recién llegado y pagado: toca aprobarlo."""
    with session_factory() as s:
        _order(s, oid="o1", number="BOPRIN-99929", payment_status="paid",
               preparation_status="pending_review")
    wf = _wf(session_factory, "o1")
    assert wf["queue"] == "por_revisar" and wf["next_action"] == "aprobar"
    assert wf["next_action_label"] == "Aprobar"
    assert wf["blocked"] is False
    # Stepper: creado y pagado hechos, aprobado es el paso ACTUAL.
    states = {s["key"]: s["state"] for s in wf["steps"]}
    assert states["creado"] == "done" and states["pagado"] == "done"
    assert states["aprobado"] == "now" and states["factura"] == "pending"


def test_workflow_por_facturar(session_factory) -> None:
    """Aprobado y con albarán, sin factura: toca emitirla."""
    with session_factory() as s:
        _order(s, oid="o2", number="ARTISJ-9544", company_id="fr",
               payment_status="paid", preparation_status="in_queue",
               approved_at=datetime.now(UTC), factusol_albaran_number="2-100418")
    wf = _wf(session_factory, "o2")
    assert wf["queue"] == "por_facturar" and wf["next_action"] == "emitir_factura"
    states = {s["key"]: s["state"] for s in wf["steps"]}
    assert states["albaran"] == "done" and states["factura"] == "now"
    # Cliente francés con VAT: intracomunitario, y se avisa de que va sin IVA.
    assert wf["regime"] == "intracomunitario"
    codes = [a["code"] for a in wf["alerts"]]
    assert "cliente_intracomunitario" in codes
    # Es informativa: NO lo saca de su cola.
    assert wf["blocked"] is False


def test_workflow_por_cobrar(session_factory) -> None:
    """Facturado y pagado en el CRM pero sin cobro en FACTUSOL: toca
    registrarlo, y además es una incidencia contable (aviso)."""
    with session_factory() as s:
        _order(s, oid="o3", number="BOPRIN-1", payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260063",
               factusol_invoice_serie=5)
    wf = _wf(session_factory, "o3")
    assert wf["queue"] == "por_cobrar" and wf["next_action"] == "registrar_cobro"
    assert "cobro_no_registrado" in [a["code"] for a in wf["alerts"]]


def test_workflow_por_enviar_y_listo(session_factory) -> None:
    """Cobrado pero sin enviar → por enviar; enviado y sin completar →
    marcar completado; completado → listo."""
    with session_factory() as s:
        _order(s, oid="o4", number="BOPRIN-2", payment_status="paid",
               preparation_status="packed", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260064",
               factusol_cobro_status="cobrada")
    wf = _wf(session_factory, "o4")
    assert wf["queue"] == "por_enviar" and wf["next_action"] == "crear_envio"

    with session_factory() as s:
        o = s.get(Order, "o4")
        o.transport_status = "in_transit"
        s.commit()
    wf = _wf(session_factory, "o4")
    assert wf["queue"] == "por_enviar" and wf["next_action"] == "marcar_completado"

    with session_factory() as s:
        o = s.get(Order, "o4")
        o.completed_at = datetime.now(UTC)
        s.commit()
    wf = _wf(session_factory, "o4")
    assert wf["queue"] == "listo" and wf["next_action"] == "ninguna"
    # Pedido WEB sin albarán de BoHub: ese paso no aplica (lo crea
    # WooCommerce), nunca queda «pendiente» eternamente.
    estados = {s["key"]: s["state"] for s in wf["steps"]}
    assert estados["albaran"] == "skipped"
    assert all(v == "done" for k, v in estados.items() if k != "albaran")


# --- incidencias -------------------------------------------------------------


def test_workflow_incidencia_lineas_sin_mapear(session_factory) -> None:
    """Línea sin CODART: incidencia BLOQUEANTE — manda sobre el ciclo."""
    with session_factory() as s:
        _order(s, oid="o5", number="PRO-004352", payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               lines=[{"sku": "99cy", "codart": "99cy"},
                      {"sku": "RARO-1", "codart": None}])
    wf = _wf(session_factory, "o5")
    assert wf["queue"] == "incidencias"
    assert wf["next_action"] == "mapear_lineas" and wf["blocked"] is True
    alert = next(a for a in wf["alerts"] if a["code"] == "lineas_sin_mapear")
    assert "1 línea sin mapear" in alert["text"] and alert["blocking"] is True


def test_workflow_incidencia_empresa_sin_vincular(session_factory) -> None:
    with session_factory() as s:
        _order(s, oid="o6", number="MANUAL-1", company_id="sinlink",
               external_source=OrderSource.MANUAL, payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC))
    wf = _wf(session_factory, "o6")
    assert wf["queue"] == "incidencias" and wf["next_action"] == "vincular_empresa"
    assert "Sin FACTUSOL SL" in wf["alerts"][0]["text"]


def test_workflow_incidencia_excepcion_abierta(session_factory) -> None:
    with session_factory() as s:
        _order(s, oid="o7", number="BOPRIN-3", payment_status="paid",
               preparation_status="pending_review")
        s.add(ErpException(
            order_id="o7", type="sat", subtype="rotura",
            status=ExceptionStatus.OPEN,
        ))
        s.commit()
    wf = _wf(session_factory, "o7")
    assert wf["queue"] == "incidencias"
    assert "excepcion_abierta" in [a["code"] for a in wf["alerts"]]


def test_workflow_lineas_sin_mapear_no_molesta_si_ya_facturado(session_factory) -> None:
    """Si el pedido YA está facturado, el mapeo dejó de importar: no se
    convierte en incidencia (no se reabre trabajo hecho)."""
    with session_factory() as s:
        _order(s, oid="o8", number="BOPRIN-4", payment_status="paid",
               preparation_status="packed", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260065",
               factusol_cobro_status="cobrada",
               lines=[{"sku": "RARO-1", "codart": None}])
    wf = _wf(session_factory, "o8")
    assert wf["queue"] == "por_enviar"
    assert "lineas_sin_mapear" not in [a["code"] for a in wf["alerts"]]


# --- API: bandeja y ficha reciben lo mismo -----------------------------------


def test_bandeja_devuelve_colas_contadores_y_filtro(http, session_factory) -> None:
    """La bandeja trae el `workflow` de cada pedido, los contadores de TODAS
    las colas y sabe filtrar por cola sin perder los contadores."""
    with session_factory() as s:
        _order(s, oid="a1", number="A-1", payment_status="paid",
               preparation_status="pending_review")
        _order(s, oid="a2", number="A-2", payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC))
        _order(s, oid="a3", number="A-3", company_id="sinlink",
               payment_status="paid", preparation_status="in_queue",
               approved_at=datetime.now(UTC))

    r = http.get("/api/erp/orders", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queue_counts"]["por_revisar"] == 1
    assert body["queue_counts"]["por_facturar"] == 1
    assert body["queue_counts"]["incidencias"] == 1
    by_number = {i["order_number"]: i for i in body["items"]}
    assert by_number["A-1"]["workflow"]["next_action"] == "aprobar"
    assert by_number["A-2"]["workflow"]["next_action"] == "emitir_factura"

    r = http.get("/api/erp/orders?queue=incidencias",
                 headers=auth_headers(http, "pedidos"))
    body = r.json()
    assert [i["order_number"] for i in body["items"]] == ["A-3"]
    assert body["queue"] == "incidencias"
    assert body["queue_counts"]["por_revisar"] == 1     # contadores completos


def test_ficha_devuelve_el_mismo_workflow(http, session_factory) -> None:
    with session_factory() as s:
        _order(s, oid="f1", number="F-1", company_id="fr", payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               factusol_albaran_number="2-100418")

    lista = http.get("/api/erp/orders", headers=auth_headers(http, "pedidos")).json()
    en_bandeja = lista["items"][0]["workflow"]
    ficha = http.get("/api/erp/orders/f1", headers=auth_headers(http, "pedidos")).json()
    assert ficha["workflow"]["queue"] == en_bandeja["queue"]
    assert ficha["workflow"]["next_action"] == en_bandeja["next_action"]
    assert ficha["workflow"]["alerts"] == en_bandeja["alerts"]
    assert len(ficha["workflow"]["steps"]) == 7
    # Cliente del pedido: lo que pinta la cabecera (país + régimen) y el
    # bloque FACTUSOL de la ficha (nº de cliente vinculado).
    assert ficha["workflow"]["company"] == {
        "id": "fr", "name": "La Maison de la Plaque", "country": "FR",
        "factusol_id": "2760", "regime": "intracomunitario",
    }


def test_bandeja_conserva_los_filtros_y_acciones_de_siempre(http, session_factory) -> None:
    """El rediseño NO quita nada: los filtros de estado, «Ver ocultados», el
    filtro de completados y el de cobro siguen respondiendo igual."""
    with session_factory() as s:
        _order(s, oid="b1", number="B-1", payment_status="paid",
               preparation_status="pending_review")
        _order(s, oid="b2", number="B-2", payment_status="pending",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               completed_at=datetime.now(UTC))

    h = auth_headers(http, "pedidos")
    assert [i["order_number"] for i in http.get(
        "/api/erp/orders?payment=paid", headers=h).json()["items"]] == ["B-1"]
    assert [i["order_number"] for i in http.get(
        "/api/erp/orders?completed=true", headers=h).json()["items"]] == ["B-2"]
    assert [i["order_number"] for i in http.get(
        "/api/erp/orders?preparation=pending_review", headers=h).json()["items"]] == ["B-1"]
    # Y las acciones de la ficha siguen ahí (el serializer no perdió campos).
    item = http.get("/api/erp/orders", headers=h).json()["items"][0]
    for key in ("completed", "excluded", "factusol_cobro_status",
                "factusol_albaran_number", "invoice_status", "transport_status"):
        assert key in item
