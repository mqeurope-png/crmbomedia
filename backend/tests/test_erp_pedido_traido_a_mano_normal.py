"""ERP · Pedidos web TRAÍDOS A MANO: una vez dentro, es un pedido normal.

Bart trae a mano desde ERP · Pedidos un pedido `on-hold` de la tienda (una
transferencia aún sin cobrar). Traerlo ES la autorización: no hay estado nuevo
de «sin pagar» ni bloqueos inventados. El pago se queda honesto («pendiente»
hasta que la tienda lo cobre) y aguas abajo:

- la preparación no espera el cobro (`payment_not_required`): aprobar, empezar
  la preparación, embalar y crear el envío pasan con el pago pendiente; el
  MISMO pedido sin la marca de traído sigue parado en la puerta de pago;
- emitir la factura no mira el pago (ya era así; queda asegurado);
- se ve en Seguimiento y en la hoja de Drive aunque la tienda lo tenga
  `on-hold`; anulado (en BoHub o en la tienda) se oculta como siempre;
- el Cuadre «Pedido esperando aprobación» lo cuenta en vez de saltárselo;
- las limpiezas de carritos (`purge.py`, `cleanup.py`) no lo tocan;
- «Pedidos web pagados que no llegan» lo REFRESCA por el camino del webhook
  cuando la tienda lo cobra sin avisar (pago → pagado, sin duplicarlo) y sigue
  saltándose los pedidos normales que ya están.

Figurante: el pedido 5873 de fluxlasers.es (anexo de Bart, 28/10/2026), con los
datos personales sustituidos. Nada sale a una tienda ni a FACTUSOL reales.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra todos los modelos
from app.core.crypto import encrypt
from app.db.base import Base
from app.db.session import get_session
from app.erp import seguimiento as core
from app.erp.api.seguimiento import _rows, drive_live_rows
from app.erp.cuadre import checks_mysql as cm
from app.erp.cuadre.config import normalizar_config
from app.erp.cuadre.contexto import Contexto
from app.erp.models import (
    InvoiceStatus,
    Order,
    OrderLine,
    OrderSource,
    PaymentStatus,
    PreparationStatus,
    ShipmentPackage,
    TransportStatus,
)
from app.erp.sample_orders import is_billable, is_sample_order, payment_not_required
from app.integrations.factusol.service import emit_invoice
from app.integrations.woocommerce import jobs as woo_jobs
from app.integrations.woocommerce.cleanup import cleanup_non_processing_orders
from app.integrations.woocommerce.missing import import_missing_paid_orders
from app.integrations.woocommerce.purge import purge_unprocessed_web_orders
from app.main import app
from app.models.crm import ExternalSystem, SyncLog, User, UserRole
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_woo_pagados_que_faltan import _tiendas
from tests.test_factusol_service import FakeFactusol, _pcl_row

#: Cuándo lo trajo Bart (el 5873 entró `on-hold` el 23/10 a las 09:18).
TRAIDO_EL = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
#: «Ahora» del repaso de pagados que faltan: la tienda lo cobró ayer.
AHORA = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with f() as seed:
        seed_test_users(seed)
        seed.commit()
    # La importación de los que faltan abre su propia sesión: la misma BD.
    with patch.object(woo_jobs, "_session_factory", return_value=f):
        yield f
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --- figurantes ----------------------------------------------------------------------


def _store(s: Session, slug: str = "fluxlasers") -> IntegrationAccount:
    """fluxlasers.es; en FACTUSOL su prefijo es `FLE` (no el derivado `FLU`)."""
    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=f"{slug}.es",
        enabled=True, mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=f"https://{slug}.example",
        consumer_key_encrypted=encrypt("ck"), consumer_secret_encrypted=encrypt("cs"),
        credential_status="configured",
        metadata_json=json.dumps({"factusol_ref_prefix": "FLE"}),
    )
    s.add(a)
    s.commit()
    return a


def _woo_5873(*, status: str = "on-hold", woo_id: int = 5873) -> dict[str, Any]:
    """El 5873 tal como lo da la REST de la tienda: transferencia (`bacs`),
    compra sin cuenta, CIF en `meta_data`, portes con su IVA, una línea sin
    SKU y provincia como código («T»). Cobrado (`processing`) lleva
    `date_paid`; en espera, no."""
    pagado = status in ("processing", "completed")
    persona = {"first_name": "Marta", "last_name": "Vidal Soler",
               "company": "Escola de Ponent", "address_1": "Carrer Major 1",
               "city": "Tarragona", "state": "T", "postcode": "43001",
               "country": "ES", "phone": "977000000"}
    return {
        "id": woo_id, "number": str(woo_id), "status": status,
        "currency": "EUR", "prices_include_tax": False, "customer_id": 0,
        "date_created": "2026-10-23T09:18:59", "date_created_gmt": "2026-10-23T07:18:59",
        "date_paid": "2026-10-27T09:02:11" if pagado else None,
        "date_paid_gmt": "2026-10-27T08:02:11" if pagado else None,
        "date_modified_gmt": "2026-10-27T08:02:11",
        "discount_total": "0.00", "shipping_total": "6.00",
        "total": "44.77", "total_tax": "7.77",
        "payment_method": "bacs", "payment_method_title": "Transferencia bancaria directa",
        "transaction_id": "",
        "billing": {**persona, "email": "secretaria@escoladeponent.example"},
        "shipping": dict(persona),
        "line_items": [
            {"id": 3866, "name": "Ador & BB2 AF Plastic Probe Cubierta", "product_id": 4972,
             "quantity": 1, "sku": "PMP-12003", "subtotal": "5.00", "total": "5.00",
             "total_tax": "1.05"},
            {"id": 3867, "name": "Ador & BB2 AF Plastic Probe", "product_id": 4971,
             "quantity": 1, "sku": "PMP-12002", "subtotal": "5.00", "total": "5.00",
             "total_tax": "1.05"},
            {"id": 3868, "name": "Pack 50 Láminas A4 Cartón Canal 3 mm", "product_id": 4326,
             "quantity": 1, "sku": "", "subtotal": "21.00", "total": "21.00",
             "total_tax": "4.41"},
        ],
        "shipping_lines": [
            {"id": 3869, "method_title": "Envío 48/72h (Una vez procesado el pedido)",
             "method_id": "flexible_shipping_single", "total": "6.00", "total_tax": "1.26"},
        ],
        "meta_data": [
            {"key": "_billing_nif", "value": "Q4300000J"},
            {"key": "_shipping_nif", "value": "Q4300000J"},
            {"key": "is_vat_exempt", "value": "no"},
        ],
    }


def _user(s: Session, role: UserRole) -> User:
    return s.scalar(select(User).where(User.role == role))


def _pedido(
    s: Session, store: IntegrationAccount, *, traido: bool = True, woo_id: str = "5873",
    woo_status: str = "on-hold", **kw: Any,
) -> Order:
    """El 5873 ya dentro de BoHub como lo deja «Traer» (bloque A): web de la
    tienda, `on-hold`, pago «pendiente», sin aprobar y con la marca de quién y
    cuándo lo trajo. `traido=False` = el MISMO pedido sin la marca."""
    o = Order(
        external_source=OrderSource.WOOCOMMERCE, external_id=woo_id, store_id=store.id,
        order_number=f"{store.account_id.upper()[:6]}-{woo_id}", woo_status=woo_status,
        total_amount=44.77, currency="EUR", payment_method="bacs",
        payment_method_title="Transferencia bancaria directa",
        payment_status=PaymentStatus.PENDING,
        preparation_status=PreparationStatus.PENDING_REVIEW,
        placed_at=datetime(2026, 10, 23, 9, 18, 59, tzinfo=UTC),
        manual_web_import_at=TRAIDO_EL if traido else None,
        manual_web_import_by_user_id=_user(s, UserRole.PEDIDOS).id if traido else None,
        **kw,
    )
    s.add(o)
    s.flush()
    for pos, (sku, desc, total, tax) in enumerate((
        ("PMP-12003", "Ador & BB2 AF Plastic Probe Cubierta", 5, 21),
        ("PMP-12002", "Ador & BB2 AF Plastic Probe", 5, 21),
        ("woo-pid-4326", "Pack 50 Láminas A4 Cartón Canal 3 mm", 21, 21),
    )):
        s.add(OrderLine(order_id=o.id, position=pos, product_sku=sku, description=desc,
                        quantity=1, unit_price=total, tax_rate=tax, line_total=total))
    s.add(OrderLine(order_id=o.id, position=3, product_sku="shipping",
                    description="Envío 48/72h (Una vez procesado el pedido)", quantity=1,
                    unit_price=6, tax_rate=21, line_total=6, is_shipping=True,
                    line_kind="shipping"))
    s.commit()
    return o


def _transicion(http: TestClient, oid: str, rol: str, domain: str, to: str):
    return http.post(f"/api/erp/orders/{oid}/transitions",
                     json={"domain": domain, "to_status": to},
                     headers=auth_headers(http, rol))


def _bulto(factory, oid: str) -> None:
    """Embalar exige un bulto medido (Fase D)."""
    with factory() as s:
        s.add(ShipmentPackage(order_id=oid, position=1, weight_kg=2,
                              height_cm=10, width_cm=20, depth_cm=30))
        s.commit()


# --- preparar / embalar / enviar sin esperar el cobro ------------------------------------


def test_traido_sin_cobrar_se_aprueba_prepara_embala_y_envia(factory, http):
    with factory() as s:
        oid = _pedido(s, _store(s)).id

    r = http.post(f"/api/erp/orders/{oid}/approve", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    # «Empezar preparación»: la puerta de pago no le aplica (traerlo autorizó).
    r = _transicion(http, oid, "sat", "preparation", "preparing")
    assert r.status_code == 200, r.text
    _bulto(factory, oid)
    r = _transicion(http, oid, "sat", "preparation", "packed")
    assert r.status_code == 200, r.text
    r = _transicion(http, oid, "pedidos", "transport", "label_created")
    assert r.status_code == 200, r.text

    with factory() as s:
        o = s.get(Order, oid)
        assert o.preparation_status == PreparationStatus.PACKED
        assert o.transport_status == TransportStatus.LABEL_CREATED
        assert o.approved_at is not None
        # El pago sigue siendo el real: nadie lo ha dado por cobrado.
        assert o.payment_status == PaymentStatus.PENDING
        # Pasa la puerta, pero NO es una muestra: se factura y se cobra.
        assert payment_not_required(o)
        assert not is_sample_order(o) and is_billable(o)


def test_el_mismo_pedido_sin_traer_tambien_se_prepara(factory, http):
    """30/10/2026: el cobro ya no condiciona el envío; un pedido web sin
    pagar y SIN traer a mano también se prepara (decisión expresa de Bart)."""
    with factory() as s:
        oid = _pedido(s, _store(s), traido=False).id
        assert not payment_not_required(s.get(Order, oid))

    r = http.post(f"/api/erp/orders/{oid}/approve", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    r = _transicion(http, oid, "sat", "preparation", "preparing")
    assert r.status_code == 200, r.text
    with factory() as s:
        assert s.get(Order, oid).preparation_status == PreparationStatus.PREPARING


def test_emitir_la_factura_no_espera_el_cobro(factory, http):
    """Como siempre: emitir no mira el pago. El botón encola y el servicio
    copia el pedido de cliente de FesteWeb (`FLE-005873`) con el pago aún
    pendiente; sin marcarla cobrada (eso solo con el pedido `paid`)."""
    with factory() as s:
        oid = _pedido(s, _store(s)).id
    with patch("app.integrations.factusol.jobs.enqueue_emit_invoice",
               return_value="job-5873") as enq:
        r = http.post(f"/api/erp/orders/{oid}/emit-factusol-invoice",
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 202, r.text
    enq.assert_called_once()

    lpc = [{"ARTLPC": "PMP-12003", "DESLPC": "Probe Cubierta", "CANLPC": 1, "TOTLPC": 5,
            "CODLPC": 3101},
           {"ARTLPC": "", "DESLPC": "Pack 50 Láminas", "CANLPC": 1, "TOTLPC": 21,
            "CODLPC": 3101}]
    cliente = FakeFactusol(
        pcl_row=_pcl_row(CODPCL=3101, REFPCL="FLE-005873", TOTPCL=44.77),
        lpc_rows=lpc, f_fac_last=526100,
    )
    with factory() as s:
        resultado = emit_invoice(s, oid, cliente)
    assert cliente.pcl_filters == ["REFPCL='FLE-005873'"]
    assert resultado["codfac"] == "526101"
    assert not any(t == "F_FAC" and "ESTFAC" in u for t, u in cliente.updates)
    with factory() as s:
        o = s.get(Order, oid)
        assert o.invoice_status == InvoiceStatus.INVOICED_BY_ERP
        assert o.payment_status == PaymentStatus.PENDING


# --- Seguimiento, hoja de Drive y Cuadre -------------------------------------------------


def test_se_ve_en_seguimiento_y_en_drive_aunque_la_tienda_lo_tenga_en_espera(factory):
    with factory() as s:
        st = _store(s)
        _pedido(s, st, woo_id="5873")                                   # traído, on-hold
        _pedido(s, st, woo_id="5874", woo_status="pending")             # traído, pending
        _pedido(s, st, woo_id="5875", traido=False)                     # carrito on-hold
        _pedido(s, st, woo_id="5876", cancelled_at=AHORA)               # traído y anulado
        _pedido(s, st, woo_id="5877", woo_status="cancelled")           # cancelado en la web
        _pedido(s, st, woo_id="5878", woo_status="failed")              # pago fallido
        filas = {r["order_number"]: r for r in _rows(s)}
        vista = {r["order_number"] for r in core.filter_rows(list(filas.values()))}
        drive = {r["order_number"] for r in drive_live_rows(s)}

    assert vista == drive == {"FLUXLA-5873", "FLUXLA-5874"}
    traido = filas["FLUXLA-5873"]
    assert traido["oculto_por_estado"] is False and traido["estado_woo_motivo"] is None
    assert traido["forzado"] is False                       # no hace falta forzarlo
    assert traido["pendiente_escribir"] is True             # va a la hoja de Drive
    assert traido["woo_status"] == "on-hold"                # el estado real, a la vista
    # Lo demás, como siempre.
    assert filas["FLUXLA-5875"]["estado_woo_motivo"] == "on_hold"
    assert filas["FLUXLA-5876"]["estado_woo_motivo"] == "anulado"
    assert filas["FLUXLA-5877"]["estado_woo_motivo"] == "cancelled"
    assert filas["FLUXLA-5878"]["estado_woo_motivo"] == "failed"


def test_el_cuadre_cuenta_el_traido_sin_aprobar(factory):
    with factory() as s:
        st = _store(s)
        traido = _pedido(s, st, woo_id="5873")
        _pedido(s, st, woo_id="5875", traido=False)               # carrito: no es trabajo
        _pedido(s, st, woo_id="5876", cancelled_at=TRAIDO_EL)     # anulado: tampoco
        ctx = Contexto(s, ahora=TRAIDO_EL + timedelta(days=10),
                       config=normalizar_config({"checks": {}}))
        hallazgos = list(cm.pedido_sin_aprobar(ctx))
    assert [h.entidad_id for h in hallazgos] == [traido.id]
    assert "Esperando aprobación desde el" in hallazgos[0].detalle


def test_el_cuadre_cuenta_la_espera_del_traido_desde_que_se_trajo(factory):
    """Un `on-hold` que esperó semanas la transferencia en la tienda no sale
    «atrasado» nada más traerlo: solo se podía aprobar desde que entró en
    BoHub (`manual_web_import_at`), no desde la fecha de la web."""
    config = normalizar_config({"checks": {}})
    with factory() as s:
        traido = _pedido(s, _store(s), woo_id="5873")
        traido.placed_at = TRAIDO_EL - timedelta(days=27)      # on-hold desde el 26/09
        s.commit()
        al_traerlo = list(cm.pedido_sin_aprobar(
            Contexto(s, ahora=TRAIDO_EL + timedelta(days=1), config=config)))
        pasada_la_semana = list(cm.pedido_sin_aprobar(
            Contexto(s, ahora=TRAIDO_EL + timedelta(days=10), config=config)))
    assert al_traerlo == []
    assert [h.entidad_id for h in pasada_la_semana] == [traido.id]
    assert pasada_la_semana[0].detalle == "Esperando aprobación desde el 2026-10-23 (10 días)."


# --- limpiezas de carritos ---------------------------------------------------------------


def test_las_limpiezas_de_carritos_no_tocan_un_traido(factory):
    with factory() as s:
        st = _store(s)
        traido_id = _pedido(s, st, woo_id="5873").id
        _pedido(s, st, woo_id="5875", traido=False)                     # carrito de verdad

        borrado = purge_unprocessed_web_orders(s, dry_run=True)
        assert [c["order_number"] for c in borrado["candidates"]] == ["FLUXLA-5875"]
        assert borrado["protected"] == []

        excluido = cleanup_non_processing_orders(s, dry_run=False)
        assert [c["order_number"] for c in excluido["candidates"]] == ["FLUXLA-5875"]
        assert excluido["excluded"] == 1

        borrado = purge_unprocessed_web_orders(s, dry_run=False)
        assert borrado["deleted"] == 1

    with factory() as s:
        quedan = list(s.scalars(select(Order)))
        assert [o.id for o in quedan] == [traido_id]
        assert quedan[0].seguimiento_excluded_at is None


# --- «Pedidos web pagados que no llegan» -------------------------------------------------


def _repaso(factory, pedidos: dict[str, list[dict]], llamadas: list[dict], *,
            dry_run: bool = False) -> dict:
    with factory() as s:
        return import_missing_paid_orders(
            s, dry_run=dry_run, days=90, now=AHORA,
            client_factory=_tiendas(pedidos, llamadas),
        )


def test_pagados_que_faltan_refresca_el_traido_que_la_tienda_cobro_sin_webhook(factory):
    """El plugin de TSM cobra el 5873 sin disparar el webhook: el repaso lo
    pasa por `import_woo_order` (el camino del webhook) y queda pagado y en la
    Cola SAT, sin crear otro. Uno normal que ya está se sigue saltando."""
    with factory() as s:
        st = _store(s)
        traido_id = _pedido(s, st, woo_id="5873").id
        # Uno normal (entró solo) que BoHub aún ve pendiente: no se toca.
        normal_id = _pedido(s, st, woo_id="5790", traido=False, woo_status="processing").id
    tienda = {"fluxlasers": [_woo_5873(status="processing"),
                             _woo_5873(status="processing", woo_id=5790)]}

    # Vista previa: se anuncia, no se escribe nada y no cuenta como «que falta».
    llamadas: list[dict] = []
    previa = _repaso(factory, tienda, llamadas, dry_run=True)
    assert (previa["faltan"], previa["importados"], previa["refrescados"]) == (0, 0, 0)
    assert [(i["numero"], i["resultado"]) for i in previa["traidos"]] == [
        ("5873", "a_refrescar")]
    assert not [c for c in llamadas if "get_order" in c]
    with factory() as s:
        assert s.get(Order, traido_id).payment_status == PaymentStatus.PENDING

    # Al aplicar: el pedido FRESCO de la tienda, por el importador.
    llamadas = []
    r = _repaso(factory, tienda, llamadas)
    assert (r["faltan"], r["importados"], r["refrescados"]) == (0, 0, 1)
    item = r["traidos"][0]
    assert item["resultado"] == "refrescado" and item["pagado"] is True
    assert (item["order_id"], item["order_number"]) == (traido_id, "FLUXLA-5873")
    assert [c["get_order"] for c in llamadas if "get_order" in c] == [5873]
    with factory() as s:
        assert s.scalar(select(func.count(Order.id))) == 2            # ninguno nuevo
        o = s.get(Order, traido_id)
        assert o.payment_status == PaymentStatus.PAID
        assert o.woo_status == "processing"
        assert o.preparation_status == PreparationStatus.IN_QUEUE     # como el automático
        assert o.manual_web_import_at is not None                     # sigue siendo traído
        normal = s.get(Order, normal_id)
        assert normal.payment_status == PaymentStatus.PENDING          # no se refresca
        log = s.scalar(select(SyncLog).where(SyncLog.operation == "import_missing"))
        assert log.status == "success" and log.records_processed == 1
        assert "traídos a mano con el cobro al día 1" in log.message
        assert json.loads(log.metadata_json)["numeros_refrescados"] == ["FLUXLA-5873"]

    # Otra pasada: ya está pagado → nada que refrescar ni que pedir.
    llamadas = []
    r2 = _repaso(factory, tienda, llamadas)
    assert (r2["faltan"], r2["refrescados"], r2["traidos"]) == (0, 0, [])
    assert not [c for c in llamadas if "get_order" in c]


def test_pagados_que_faltan_no_toca_el_traido_que_sigue_en_espera(factory):
    """Mientras la tienda no lo cobra, ni se lista (el listado es de pagados)."""
    with factory() as s:
        traido_id = _pedido(s, _store(s)).id
    llamadas: list[dict] = []
    r = _repaso(factory, {"fluxlasers": [_woo_5873(status="on-hold")]}, llamadas)
    assert (r["faltan"], r["refrescados"], r["traidos"]) == (0, 0, [])
    with factory() as s:
        assert s.get(Order, traido_id).payment_status == PaymentStatus.PENDING
