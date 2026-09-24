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
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import ErpException, ExceptionStatus, Order, OrderLine, OrderSource
from app.erp.workflow import ACTION_LABELS, order_workflow
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
    """Facturado y cobrado → listo para completar (el SAT es OPCIONAL, ya no
    bloquea); completado → listo. El envío al taller no cambia la acción."""
    with session_factory() as s:
        _order(s, oid="o4", number="BOPRIN-2", payment_status="paid",
               preparation_status="packed", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260064",
               factusol_cobro_status="cobrada")
    wf = _wf(session_factory, "o4")
    # SAT opcional: sin haber enviado nada, ya se puede marcar completado.
    assert wf["queue"] == "por_enviar" and wf["next_action"] == "marcar_completado"

    # El sub-estado del transporte (etiqueta subida, en tránsito) NO cambia la
    # acción: sigue siendo «marcar completado» — el envío al taller es opcional.
    for estado in ("label_created", "in_transit"):
        with session_factory() as s:
            o = s.get(Order, "o4")
            o.transport_status = estado
            s.commit()
        wf = _wf(session_factory, "o4")
        assert wf["queue"] == "por_enviar" and wf["next_action"] == "marcar_completado"

    with session_factory() as s:
        o = s.get(Order, "o4")
        o.completed_at = datetime.now(UTC)
        s.commit()
    wf = _wf(session_factory, "o4")
    assert wf["queue"] == "listo" and wf["next_action"] == "ninguna"
    estados = {st["key"]: st["state"] for st in wf["steps"]}
    # La línea de vida ya NO tiene «Enviado» (el SAT es opcional; vive en «Envío
    # y seguimiento»). El albarán de un web no aplica (lo crea WooCommerce).
    assert "enviado" not in estados
    assert estados["albaran"] == "skipped"
    # Los OBLIGATORIOS están hechos; el hito OPCIONAL «Factura enviada» sigue
    # pendiente (no se mandó por email) y no bloquea ni cuenta como obligatorio.
    obligatorios = {k: v for k, v in estados.items()
                    if k not in ("albaran", "factura_enviada")}
    assert all(v == "done" for v in obligatorios.values())
    assert estados["factura_enviada"] == "pending"
    fe = next(st for st in wf["steps"] if st["key"] == "factura_enviada")
    assert fe["optional"] is True


# --- incidencias -------------------------------------------------------------


def test_workflow_lineas_sin_mapear_no_cuentan_para_el_flujo(session_factory) -> None:
    """Una línea sin CODART se emite como texto libre: NO es incidencia, no
    bloquea, no hay «Mapear líneas», y la acción es la de su estado real."""
    with session_factory() as s:
        _order(s, oid="o5", number="PRO-004352", company_id="fr", payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               factusol_albaran_number="2-100418",
               lines=[{"sku": "99cy", "codart": "99cy"},
                      {"sku": "RARO-1", "codart": None}])
    wf = _wf(session_factory, "o5")
    assert wf["queue"] == "por_facturar" and wf["blocked"] is False
    assert wf["next_action"] == "emitir_factura"
    assert wf["next_action_label"] == "Emitir factura"
    codes = [a["code"] for a in wf["alerts"]]
    assert "lineas_sin_mapear" not in codes
    assert not any(a["action"] == "mapear_lineas" for a in wf["alerts"])
    assert "mapear_lineas" not in ACTION_LABELS
    # Y en el resto de estados, lo mismo: la acción es la del estado.
    with session_factory() as s:
        _order(s, oid="o5b", number="PRO-004353", payment_status="paid",
               preparation_status="pending_review",
               lines=[{"sku": "RARO-2", "codart": None}])
    wf = _wf(session_factory, "o5b")
    assert wf["queue"] == "por_revisar" and wf["next_action"] == "aprobar"


def test_workflow_las_demas_alertas_siguen(session_factory) -> None:
    """Quitar el mapeo no toca las otras alertas: empresa sin vincular sigue
    saliendo (y el pedido con líneas sin mapear entra por ESA razón, no por el
    mapeo). Desde que «Incidencia» es solo lo reportado a mano, este aviso —que
    lo detecta la app— manda el pedido a «Por revisar»."""
    with session_factory() as s:
        _order(s, oid="o5c", number="MANUAL-9", company_id="sinlink",
               external_source=OrderSource.MANUAL, payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               lines=[{"sku": "RARO-3", "codart": None}])
    wf = _wf(session_factory, "o5c")
    assert wf["queue"] == "por_revisar" and wf["next_action"] == "vincular_empresa"
    assert [a["code"] for a in wf["alerts"] if a["review"]] == ["empresa_sin_vincular"]
    # Y NO es bloqueante: no es una incidencia de nadie.
    assert [a for a in wf["alerts"] if a["blocking"]] == []


def test_workflow_empresa_sin_vincular_va_a_por_revisar(session_factory) -> None:
    """Lo detecta la app, no una persona: «Por revisar», no «Incidencia». Sigue
    destacado y con su acción (hay que vincular antes de facturar), pero no
    entra en la lista de incidencias del equipo."""
    with session_factory() as s:
        _order(s, oid="o6", number="MANUAL-1", company_id="sinlink",
               external_source=OrderSource.MANUAL, payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC))
    wf = _wf(session_factory, "o6")
    assert wf["queue"] == "por_revisar" and wf["next_action"] == "vincular_empresa"
    assert "Sin FACTUSOL SL" in wf["alerts"][0]["text"]
    assert wf["blocked"] is False


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
    # Lo REPORTADO A MANO (el botón «Reportar problema» de la Cola SAT) es lo
    # único que hace «Incidencia»: es la lista de problemas del equipo.
    assert wf["queue"] == "incidencias"
    assert "excepcion_abierta" in [a["code"] for a in wf["alerts"]]
    assert wf["blocked"] is True


def test_resolver_la_excepcion_saca_el_pedido_de_incidencias(session_factory) -> None:
    """Resuelta la incidencia manual, el pedido vuelve a su cola de siempre."""
    with session_factory() as s:
        _order(s, oid="o7b", number="BOPRIN-3B", payment_status="paid",
               preparation_status="pending_review")
        s.add(ErpException(
            order_id="o7b", type="sat_issue", subtype=None,
            status=ExceptionStatus.OPEN,
        ))
        s.commit()
    assert _wf(session_factory, "o7b")["queue"] == "incidencias"
    with session_factory() as s:
        exc = s.scalars(select(ErpException).where(
            ErpException.order_id == "o7b")).one()
        exc.status = ExceptionStatus.RESOLVED
        s.commit()
    wf = _wf(session_factory, "o7b")
    assert wf["queue"] != "incidencias"
    assert "excepcion_abierta" not in [a["code"] for a in wf["alerts"]]


def test_workflow_lineas_sin_mapear_tampoco_si_ya_facturado(session_factory) -> None:
    """Facturado con una línea sin CODART: sigue su ciclo (por enviar)."""
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
    # A-3 (empresa sin vincular) es un aviso de la app: cae en «Por revisar»
    # junto a A-1, no en «Incidencias» —que ahora es solo lo reportado a mano—.
    assert body["queue_counts"]["por_revisar"] == 2
    assert body["queue_counts"]["por_facturar"] == 1
    assert body["queue_counts"]["incidencias"] == 0
    by_number = {i["order_number"]: i for i in body["items"]}
    assert by_number["A-1"]["workflow"]["next_action"] == "aprobar"
    assert by_number["A-2"]["workflow"]["next_action"] == "emitir_factura"
    assert by_number["A-3"]["workflow"]["next_action"] == "vincular_empresa"

    r = http.get("/api/erp/orders?queue=por_revisar",
                 headers=auth_headers(http, "pedidos"))
    body = r.json()
    assert sorted(i["order_number"] for i in body["items"]) == ["A-1", "A-3"]
    assert body["queue"] == "por_revisar"
    assert body["queue_counts"]["por_facturar"] == 1    # contadores completos


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
        # Fase VIES: NIF-IVA de la UE aún sin validar (VIES apagado en tests).
        "vies": {"vat": "FR16339753527", "status": "pendiente"},
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


# --- «Factura enviada»: hito opcional + filtro de bandeja -------------------


def _mark_invoice_emailed(session_factory, oid: str, to: list[str]) -> None:
    """Registra el evento `erp.invoice_emailed` del pedido (el dato ya existe:
    lo escribe el envío de factura por email)."""
    from app.core.audit import record_event  # noqa: PLC0415

    with session_factory() as s:
        record_event(
            s, action="erp.invoice_emailed", target_type="order",
            target_id=oid, actor=None, message="Factura enviada al cliente",
            metadata={"to": to},
        )
        s.commit()


def test_hito_factura_enviada_opcional(session_factory) -> None:
    """El hito «Factura enviada» refleja pendiente/enviada, es OPCIONAL, nunca
    es el paso actual y no cuenta como obligatorio; el SAT ya no es un paso."""
    with session_factory() as s:
        _order(s, oid="fe", number="ARTISJ-FE", payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260070",
               factusol_cobro_status="cobrada")
    wf = _wf(session_factory, "fe")
    steps = {st["key"]: st for st in wf["steps"]}
    # El SAT/«Enviado» ya no es un paso de la línea de vida.
    assert "enviado" not in steps
    fe = steps["factura_enviada"]
    assert fe["state"] == "pending" and fe["optional"] is True
    # No cuenta para el «Paso N de N» (los obligatorios son 6, sin factura_enviada).
    assert sum(1 for st in wf["steps"] if not st.get("optional")) == 6
    # Marcar enviada: hito done con la fecha; sigue opcional.
    _mark_invoice_emailed(session_factory, "fe", ["cliente@x.com"])
    wf = _wf(session_factory, "fe")
    fe = {st["key"]: st for st in wf["steps"]}["factura_enviada"]
    assert fe["state"] == "done" and fe["optional"] is True and fe["detail"]


def test_bandeja_filtro_factura_enviada(session_factory, http) -> None:
    """El filtro «Factura enviada» (enviada / no_enviada) devuelve el conjunto
    correcto; sin factura no entra en ninguno; el resumen lleva la fecha."""
    with session_factory() as s:
        # A facturado + enviado · B facturado sin enviar · C sin factura.
        for oid, num, inv in (("A", "ARTISJ-A", "260071"), ("B", "ARTISJ-B", "260072")):
            _order(s, oid=oid, number=num, payment_status="paid",
                   approved_at=datetime.now(UTC), invoice_status="invoiced_by_erp",
                   factusol_invoice_number=inv, factusol_cobro_status="cobrada")
        _order(s, oid="C", number="ARTISJ-C", payment_status="paid",
               approved_at=datetime.now(UTC))
    _mark_invoice_emailed(session_factory, "A", ["c@x.com"])

    h = auth_headers(http, "pedidos")
    enviada = http.get("/api/erp/orders?invoice_email=enviada", headers=h).json()
    assert {i["order_number"] for i in enviada["items"]} == {"ARTISJ-A"}
    assert enviada["items"][0]["invoice_emailed_at"]  # el resumen lleva la fecha
    no_env = http.get("/api/erp/orders?invoice_email=no_enviada", headers=h).json()
    assert {i["order_number"] for i in no_env["items"]} == {"ARTISJ-B"}
    # Sin filtro: todos; C (sin factura) no sale en ninguno de los dos.
    todos = http.get("/api/erp/orders", headers=h).json()
    assert {"ARTISJ-A", "ARTISJ-B", "ARTISJ-C"} <= {i["order_number"] for i in todos["items"]}


def test_workflow_no_requiere_envio_fuera_de_por_enviar(session_factory) -> None:
    """Un pedido facturado + cobrado marcado «No requiere envío» NO cae en la
    cola «Por enviar»: queda «Listo» (con el completado opcional a mano)."""
    with session_factory() as s:
        _order(s, oid="ns", number="ARTISJ-NS", payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260201",
               factusol_cobro_status="cobrada")
    # Sin marcar: cae en «Por enviar».
    assert _wf(session_factory, "ns")["queue"] == "por_enviar"
    with session_factory() as s:
        s.get(Order, "ns").shipping_not_required = True
        s.commit()
    wf = _wf(session_factory, "ns")
    assert wf["queue"] == "listo" and wf["next_action"] == "marcar_completado"


def test_workflow_sin_cobro_fuera_de_facturar_y_cobrar(session_factory) -> None:
    """C1: un pedido «sin cobro» (cortesía), aunque esté aprobado y con factura,
    no entra en «Por facturar» ni «Por cobrar»: solo queda enviarlo y
    completarlo (como una muestra, pero conservando empresa y FACTUSOL)."""
    import json

    from app.erp.factusol_albaran import PAYMENT_KEY

    with session_factory() as s:
        # Aprobado + con factura pendiente de cobro: sin la marca sería «por_cobrar».
        _order(s, oid="onc", number="MANUAL-000050", payment_status="pending",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260099")
    assert _wf(session_factory, "onc")["queue"] == "por_cobrar"
    with session_factory() as s:
        s.get(Order, "onc").packing_json = json.dumps(
            {PAYMENT_KEY: {"paid": False, "no_charge": True}},
        )
        s.commit()
    wf = _wf(session_factory, "onc")
    assert wf["queue"] == "por_enviar" and wf["next_action"] == "marcar_completado"
