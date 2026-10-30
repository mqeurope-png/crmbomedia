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
    # Stepper: creado hecho, aprobado es el paso ACTUAL; el pago va aparte,
    # como distintivo (31/10/2026).
    states = {s["key"]: s["state"] for s in wf["steps"]}
    assert states["creado"] == "done" and "pagado" not in states
    assert states["aprobado"] == "now" and states["factura"] == "pending"
    assert wf["pago"]["pagado"] is True


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


def test_workflow_factura_por_enviar_y_por_cobrar(session_factory) -> None:
    """Facturado: primero se envía la factura al cliente («Factura por
    enviar»); enviada y sin cobro en FACTUSOL, toca registrarlo (y siendo
    pagado en el CRM es además un aviso contable)."""
    with session_factory() as s:
        _order(s, oid="o3", number="BOPRIN-1", payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260063",
               factusol_invoice_serie=5)
    wf = _wf(session_factory, "o3")
    assert wf["queue"] == "factura_por_enviar" and wf["next_action"] == "enviar_factura"
    assert wf["next_action_label"] == ACTION_LABELS["enviar_factura"]
    states = {st["key"]: st["state"] for st in wf["steps"]}
    assert states["factura"] == "done" and states["factura_enviada"] == "now"
    assert states["cobro"] == "pending"

    _mark_invoice_emailed(session_factory, "o3", ["cliente@ejemplo.test"])
    wf = _wf(session_factory, "o3")
    assert wf["queue"] == "por_cobrar" and wf["next_action"] == "registrar_cobro"
    assert "cobro_no_registrado" in [a["code"] for a in wf["alerts"]]
    states = {st["key"]: st["state"] for st in wf["steps"]}
    assert states["factura_enviada"] == "done" and states["cobro"] == "now"


def test_workflow_enviada_y_cobrada_es_listo(session_factory) -> None:
    """Facturado, factura enviada y cobrado → listo para completar (el envío
    del paquete se sigue en la Cola SAT, no bloquea); completado → listo."""
    with session_factory() as s:
        _order(s, oid="o4", number="BOPRIN-2", payment_status="paid",
               preparation_status="packed", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260064",
               factusol_cobro_status="cobrada")
    # Cobrada pero sin enviar la factura: falta eso (el paso 6).
    assert _wf(session_factory, "o4")["queue"] == "factura_por_enviar"
    _mark_invoice_emailed(session_factory, "o4", ["cliente@ejemplo.test"])
    wf = _wf(session_factory, "o4")
    assert wf["queue"] == "listo" and wf["next_action"] == "marcar_completado"

    # El sub-estado del transporte (etiqueta subida, en tránsito) NO cambia la
    # acción: sigue siendo «marcar completado» — el envío al taller es opcional.
    for estado in ("label_created", "in_transit"):
        with session_factory() as s:
            o = s.get(Order, "o4")
            o.transport_status = estado
            s.commit()
        wf = _wf(session_factory, "o4")
        assert wf["queue"] == "listo" and wf["next_action"] == "marcar_completado"

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
    # Todos los pasos hechos, también «Factura enviada» (ya no es opcional).
    assert all(v == "done" for k, v in estados.items() if k != "albaran")
    assert not any(st.get("optional") for st in wf["steps"])


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
    """Facturado con una línea sin CODART: sigue su ciclo (factura por enviar)."""
    with session_factory() as s:
        _order(s, oid="o8", number="BOPRIN-4", payment_status="paid",
               preparation_status="packed", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260065",
               factusol_cobro_status="cobrada",
               lines=[{"sku": "RARO-1", "codart": None}])
    wf = _wf(session_factory, "o8")
    assert wf["queue"] == "factura_por_enviar"
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
    assert len(ficha["workflow"]["steps"]) == 6   # «Pagado» es distintivo, no paso
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


def test_factura_enviada_es_el_paso_6_antes_del_cobro(session_factory) -> None:
    """«Factura enviada» es el paso 6 de 7, va ANTES del cobro y es obligatorio
    (ya no es un hito opcional al final); el SAT no es un paso."""
    with session_factory() as s:
        _order(s, oid="fe", number="ARTISJ-FE", payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260070")
    wf = _wf(session_factory, "fe")
    keys = [st["key"] for st in wf["steps"]]
    assert keys == ["creado", "aprobado", "albaran", "factura",
                    "factura_enviada", "cobro"]
    assert "enviado" not in keys
    steps = {st["key"]: st for st in wf["steps"]}
    assert steps["factura_enviada"]["state"] == "now"
    assert not steps["factura_enviada"].get("optional")
    assert steps["cobro"]["state"] == "pending"
    # Enviada: hecho con la fecha, y el actual pasa a ser el cobro.
    _mark_invoice_emailed(session_factory, "fe", ["cliente@ejemplo.test"])
    wf = _wf(session_factory, "fe")
    steps = {st["key"]: st for st in wf["steps"]}
    assert steps["factura_enviada"]["state"] == "done" and steps["factura_enviada"]["detail"]
    assert steps["cobro"]["state"] == "now"


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


def _facturado(s: Session, oid: str, numero: str) -> None:
    _order(s, oid=oid, number=f"ARTISJ-{oid.upper()}", payment_status="paid",
           approved_at=datetime.now(UTC), invoice_status="invoiced_by_erp",
           factusol_invoice_number=numero)


def test_factura_enviada_en_la_hoja_solo_si_es_una_fecha(session_factory, http) -> None:
    """Un texto en la columna «Factura enviada» de la hoja («sí, por email»)
    no da la factura por enviada: solo una fecha."""
    from app.erp.models import SeguimientoOverride
    from app.erp.workflow import latest_invoice_sent_map

    with session_factory() as s:
        _facturado(s, "h1", "260301")
        _facturado(s, "h2", "260302")
        s.add_all([
            SeguimientoOverride(order_id="h1", column_key="factura_enviada",
                                value="sí, por email"),
            SeguimientoOverride(order_id="h2", column_key="factura_enviada",
                                value="2026-09-10"),
        ])
        s.commit()
        assert latest_invoice_sent_map(s, ["h1", "h2"]) == {"h2": "2026-09-10"}
    assert _wf(session_factory, "h1")["queue"] == "factura_por_enviar"
    assert _wf(session_factory, "h2")["queue"] == "por_cobrar"
    h = auth_headers(http, "pedidos")
    assert http.get("/api/erp/orders/h1", headers=h).json()["invoice_sent"] is None
    sent = http.get("/api/erp/orders/h2", headers=h).json()["invoice_sent"]
    assert sent["via"] == "hoja" and sent["at"] == "2026-09-10"


def test_deshacer_en_el_mismo_segundo_que_la_marca_no_se_pierde(session_factory, http) -> None:
    """Marca «ya enviada» y su deshacer en el mismo segundo, con el deshacer
    ordenado antes (ids al azar): sigue deshecha, porque el deshacer dice qué
    marca anula. Un deshacer antiguo (sin `anula`) quita lo marcado antes."""
    import json

    from app.erp.workflow import latest_invoice_sent_map
    from app.models.crm import AuditLog

    t = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)
    with session_factory() as s:
        _facturado(s, "u1", "260310")
        _facturado(s, "u2", "260311")
        _facturado(s, "u3", "260312")
        s.add_all([
            # u1: mismo segundo, el deshacer («aa…») va antes que la marca («zz…»).
            AuditLog(id="zz-marca-u1", action="erp.invoice_marked_sent", target_type="order",
                     target_id="u1", created_at=t,
                     metadata_json=json.dumps({"fecha": "2026-09-01", "medio": "papel"})),
            AuditLog(id="aa-deshacer-u1", action="erp.invoice_marked_unsent",
                     target_type="order", target_id="u1", created_at=t,
                     metadata_json=json.dumps({"anula": "zz-marca-u1"})),
            # u2: deshacer antiguo, sin `anula`, un segundo después: sigue valiendo.
            AuditLog(id="m-u2", action="erp.invoice_marked_sent", target_type="order",
                     target_id="u2", created_at=t,
                     metadata_json=json.dumps({"fecha": "2026-09-01"})),
            AuditLog(id="d-u2", action="erp.invoice_marked_unsent", target_type="order",
                     target_id="u2", created_at=t.replace(second=1), metadata_json="{}"),
            # u3: se anula una marca, pero hay otra que sigue en pie.
            AuditLog(id="m1-u3", action="erp.invoice_marked_sent", target_type="order",
                     target_id="u3", created_at=t,
                     metadata_json=json.dumps({"fecha": "2026-08-30"})),
            AuditLog(id="m2-u3", action="erp.invoice_marked_sent", target_type="order",
                     target_id="u3", created_at=t.replace(second=2),
                     metadata_json=json.dumps({"fecha": "2026-09-02"})),
            AuditLog(id="d-u3", action="erp.invoice_marked_unsent", target_type="order",
                     target_id="u3", created_at=t.replace(second=2),
                     metadata_json=json.dumps({"anula": "m2-u3"})),
        ])
        s.commit()
        assert latest_invoice_sent_map(s, ["u1", "u2", "u3"]) == {"u3": "2026-08-30"}
    assert _wf(session_factory, "u1")["queue"] == "factura_por_enviar"
    h = auth_headers(http, "pedidos")
    assert http.get("/api/erp/orders/u1", headers=h).json()["invoice_sent"] is None
    assert http.get("/api/erp/orders/u2", headers=h).json()["invoice_sent"] is None
    assert http.get("/api/erp/orders/u3", headers=h).json()["invoice_sent"]["at"] == "2026-08-30"


def test_workflow_no_requiere_envio_es_listo(session_factory) -> None:
    """Un pedido facturado, con la factura enviada y cobrado, marcado «No
    requiere envío», queda «Listo» (con el completado opcional a mano)."""
    with session_factory() as s:
        _order(s, oid="ns", number="ARTISJ-NS", payment_status="paid",
               preparation_status="in_queue", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260201",
               factusol_cobro_status="cobrada")
    _mark_invoice_emailed(session_factory, "ns", ["cliente@ejemplo.test"])
    assert _wf(session_factory, "ns")["queue"] == "listo"
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
    _mark_invoice_emailed(session_factory, "onc", ["cliente@ejemplo.test"])
    assert _wf(session_factory, "onc")["queue"] == "por_cobrar"
    with session_factory() as s:
        s.get(Order, "onc").packing_json = json.dumps(
            {PAYMENT_KEY: {"paid": False, "no_charge": True}},
        )
        s.commit()
    wf = _wf(session_factory, "onc")
    assert wf["queue"] == "listo" and wf["next_action"] == "marcar_completado"


# --- remates de bandeja (lote de pantallas) -------------------------------------


def test_workflow_entregado_facturado_y_cobrado_es_listo(session_factory) -> None:
    """Tras #501 un pedido entregado sin completar sigue a la vista: facturado +
    cobrado + ENTREGADO no está «Por enviar», está «Listo» (como un «No aplica»
    facturado y cobrado), con el completado a mano. Casos reales FLUXLA-5787 y
    ARTISJ-9489."""
    with session_factory() as s:
        _order(s, oid="ent", number="FLUXLA-5787", payment_status="paid",
               preparation_status="packed", approved_at=datetime.now(UTC),
               invoice_status="invoiced_by_erp", factusol_invoice_number="260310",
               factusol_cobro_status="cobrada", transport_status="delivered")
    _mark_invoice_emailed(session_factory, "ent", ["cliente@ejemplo.test"])
    wf = _wf(session_factory, "ent")
    assert wf["queue"] == "listo" and wf["next_action"] == "marcar_completado"
    # Seguimiento (pantalla y hoja) usa la misma cola como Situación.
    with session_factory() as s:
        from app.erp.api.seguimiento import _rows

        fila = next(f for f in _rows(s) if f["id"] == "ent")
        assert fila["situacion"] == "listo"


def test_bandeja_por_revisar_incluye_los_no_pagados_detras(http, session_factory) -> None:
    """La bandeja por defecto ya no filtra «pagados»: un pedido pendiente de
    pago que espera aprobación cuenta en «Por revisar» y sale DETRÁS de los
    pagados (aunque sea más reciente)."""
    with session_factory() as s:
        _order(s, oid="p1", number="BOPRIN-100", payment_status="paid",
               preparation_status="pending_review",
               placed_at=datetime(2026, 9, 1, tzinfo=UTC))
        _order(s, oid="p2", number="BOPRIN-101", payment_status="pending",
               preparation_status="pending_review",
               placed_at=datetime(2026, 9, 30, tzinfo=UTC))
    h = auth_headers(http, "pedidos")
    r = http.get("/api/erp/orders", headers=h,
                 params={"completed": "false", "queue": "por_revisar",
                         "paid_first": "true"}).json()
    assert [i["order_number"] for i in r["items"]] == ["BOPRIN-100", "BOPRIN-101"]
    assert r["queue_counts"]["por_revisar"] == 2
    # Sin pedirlo (actividad de una empresa…), el orden por fecha de siempre:
    # el recorte no deja fuera a los más recientes aunque no estén pagados.
    r = http.get("/api/erp/orders", headers=h,
                 params={"company_id": "es", "limit": 1}).json()
    assert [i["order_number"] for i in r["items"]] == ["BOPRIN-101"]
