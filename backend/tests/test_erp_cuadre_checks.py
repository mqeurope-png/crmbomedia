"""ERP · Cuadre — las 12 comprobaciones: un caso que dispara y uno limpio cada una.

FACTUSOL es un doble en memoria (nunca la API real): `FakeFactusol` sirve las
tablas y cuenta las lecturas (una por tabla y pasada, del ejercicio en curso).
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.erp.cuadre import checks_factusol as cf
from app.erp.cuadre import checks_mysql as cm
from app.erp.cuadre.contexto import Contexto
from app.erp.cuadre.registry import Hallazgo
from app.erp.models import (
    InvoiceStatus,
    Order,
    OrderSource,
    OrderStatusHistory,
    PreparationStatus,
    SeguimientoSnapshot,
    StatusDomain,
    TransportStatus,
)
from app.erp.seguimiento import ID_INDEX, SEGUIMIENTO_COLUMNS_V2
from app.models.crm import AuditLog

AHORA = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)
HACE_MUCHO = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
USER = "user-bart"


@pytest.fixture()
def s() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    Base.metadata.drop_all(engine)


class FakeFactusol:
    """Tablas de FACTUSOL en memoria. Cuenta cuántas veces se lee cada una."""

    def __init__(self, **tablas: list[dict[str, Any]]) -> None:
        self.tablas = tablas
        self.lecturas: Counter[str] = Counter()
        self.ejercicios: set[str | None] = set()

    def load_table(self, tabla: str, *, filtro: str = "1=1", ejercicio: str | None = None):
        assert filtro == "1=1"
        self.lecturas[tabla] += 1
        self.ejercicios.add(ejercicio)
        return [dict(r) for r in self.tablas.get(tabla, [])]


def _ctx(s: Session, client: Any = None, **checks: dict[str, Any]) -> Contexto:
    from app.erp.cuadre.config import normalizar_config

    return Contexto(s, ahora=AHORA, config=normalizar_config({"checks": checks}),
                    client=client, ejercicio="2026" if client is not None else None)


def _pedido(s: Session, numero: str, **kw: Any) -> Order:
    kw.setdefault("created_at", HACE_MUCHO)
    kw.setdefault("placed_at", HACE_MUCHO)
    o = Order(order_number=numero, external_source=kw.pop("source", OrderSource.MANUAL), **kw)
    s.add(o)
    s.flush()
    return o


def _historial(s: Session, o: Order, domain: StatusDomain, to: str, cuando: datetime) -> None:
    s.add(OrderStatusHistory(order_id=o.id, domain=domain, from_status=None, to_status=to,
                             changed_at=cuando, changed_by_user_id=USER))
    s.flush()


def _ids(hallazgos: list[Hallazgo]) -> set[str]:
    return {h.entidad_id for h in hallazgos}


def _correr(func, ctx: Contexto) -> list[Hallazgo]:
    return list(func(ctx))


# --- 4 · «No requiere envío» con datos de envío ----------------------------------------


def test_sin_envio_con_tracking_dispara_y_sin_datos_no(s):
    malo = _pedido(s, "BOP-1", shipping_not_required=True, tracking_number="1Z999")
    con_courier = _pedido(s, "BOP-2", shipping_not_required=True,
                          packing_json=json.dumps({"envio": {"courier": "UPS"}}))
    con_genei = _pedido(s, "BOP-3", shipping_not_required=True,
                        packing_json=json.dumps({"genei": {"shipment_code": "GE1"}}))
    _pedido(s, "BOP-4", shipping_not_required=True)                       # limpio
    _pedido(s, "BOP-5", tracking_number="1Z1")                            # requiere envío
    s.commit()
    res = _correr(cm.sin_envio_con_datos, _ctx(s))
    assert _ids(res) == {malo.id, con_courier.id, con_genei.id}
    assert "tracking 1Z999" in next(h for h in res if h.entidad_id == malo.id).detalle


# --- 5 · Enviado con tracking sin aviso al cliente -------------------------------------


def _genei(codigo: str, aviso: str) -> str:
    return json.dumps({"genei": {"shipment_code": codigo, "customer_email": {"status": aviso}}})


def test_enviado_sin_aviso_dispara_y_con_aviso_no(s):
    sin = _pedido(s, "BOP-10", transport_status=TransportStatus.IN_TRANSIT, tracking_number="T1")
    _historial(s, sin, StatusDomain.TRANSPORT, "in_transit", AHORA - timedelta(days=3))
    con = _pedido(s, "BOP-11", transport_status=TransportStatus.DELIVERED, tracking_number="T2")
    _historial(s, con, StatusDomain.TRANSPORT, "in_transit", AHORA - timedelta(days=4))
    s.add(AuditLog(action="erp.shipment_emailed", target_type="order", target_id=con.id))
    en_pedido = _pedido(s, "BOP-12", transport_status=TransportStatus.IN_TRANSIT,
                        tracking_number="T3", packing_json=json.dumps(
                            {"envio": {"customer_email": {"status": "sent"}}}))
    _historial(s, en_pedido, StatusDomain.TRANSPORT, "in_transit", AHORA - timedelta(days=2))
    viejo = _pedido(s, "BOP-13", transport_status=TransportStatus.IN_TRANSIT, tracking_number="T4")
    _historial(s, viejo, StatusDomain.TRANSPORT, "in_transit", AHORA - timedelta(days=90))
    # Aviso automático APAGADO al crear el envío: decisión de Configuración.
    apagado = _pedido(s, "BOP-14", transport_status=TransportStatus.IN_TRANSIT,
                      tracking_number="T5", packing_json=_genei("G5", "disabled"))
    _historial(s, apagado, StatusDomain.TRANSPORT, "in_transit", AHORA - timedelta(days=2))
    # Envío de Genei de antes del aviso de BoHub (sin bloque): ya lo avisó Genei.
    genei_viejo = _pedido(s, "BOP-15", transport_status=TransportStatus.IN_TRANSIT,
                          tracking_number="T6", packing_json=json.dumps(
                              {"genei": {"shipment_code": "G6"}}))
    _historial(s, genei_viejo, StatusDomain.TRANSPORT, "in_transit", AHORA - timedelta(days=2))
    # Aviso con error: el cliente no lo ha recibido.
    con_error = _pedido(s, "BOP-16", transport_status=TransportStatus.IN_TRANSIT,
                        tracking_number="T7", packing_json=_genei("G7", "error"))
    _historial(s, con_error, StatusDomain.TRANSPORT, "in_transit", AHORA - timedelta(days=2))
    s.commit()
    assert _ids(_correr(cm.enviado_sin_aviso, _ctx(s))) == {sin.id, con_error.id}
    # La ventana es configurable: con 120 días entra también el viejo.
    ancha = _ctx(s, enviado_sin_aviso={"dias": 120})
    assert _ids(_correr(cm.enviado_sin_aviso, ancha)) == {sin.id, con_error.id, viejo.id}


# --- 6 · Enviado sin entrega ni incidencia -------------------------------------------


def test_envio_estancado_respeta_el_umbral(s):
    lento = _pedido(s, "BOP-20", transport_status=TransportStatus.IN_TRANSIT)
    _historial(s, lento, StatusDomain.TRANSPORT, "in_transit", AHORA - timedelta(days=15))
    reciente = _pedido(s, "BOP-21", transport_status=TransportStatus.IN_TRANSIT)
    _historial(s, reciente, StatusDomain.TRANSPORT, "in_transit", AHORA - timedelta(days=5))
    entregado = _pedido(s, "BOP-22", transport_status=TransportStatus.DELIVERED)
    _historial(s, entregado, StatusDomain.TRANSPORT, "in_transit", AHORA - timedelta(days=30))
    s.commit()
    assert _ids(_correr(cm.envio_sin_entregar, _ctx(s))) == {lento.id}
    assert _correr(cm.envio_sin_entregar, _ctx(s, envio_sin_entregar={"dias": 20})) == []


# --- 7 · Entregado, facturado y cobrado sin completar -------------------------------------


def test_entregado_cobrado_sin_completar(s):
    kw = dict(transport_status=TransportStatus.DELIVERED,
              invoice_status=InvoiceStatus.INVOICED_BY_ERP,
              factusol_invoice_number="5-260001", factusol_cobro_status="cobrada")
    pendiente = _pedido(s, "BOP-30", **kw)
    _historial(s, pendiente, StatusDomain.TRANSPORT, "delivered", AHORA - timedelta(days=20))
    completado = _pedido(s, "BOP-31", completed_at=AHORA - timedelta(days=1), **kw)
    _historial(s, completado, StatusDomain.TRANSPORT, "delivered", AHORA - timedelta(days=20))
    sin_cobrar = _pedido(s, "BOP-32", **{**kw, "factusol_cobro_status": "pendiente"})
    _historial(s, sin_cobrar, StatusDomain.TRANSPORT, "delivered", AHORA - timedelta(days=20))
    s.commit()
    assert _ids(_correr(cm.entregado_sin_completar, _ctx(s))) == {pendiente.id}


# --- 8 · Hoja de Drive -----------------------------------------------------------


def _foto(s: Session, row_id: str, numero: str) -> None:
    fila = [""] * len(SEGUIMIENTO_COLUMNS_V2)
    fila[SEGUIMIENTO_COLUMNS_V2.index("Nº pedido")] = numero
    fila[ID_INDEX] = row_id
    s.add(SeguimientoSnapshot(row_id=row_id, kind="order", values_json=json.dumps(fila)))


def _vivo(s: Session, numero: str) -> Order:
    o = _pedido(s, numero, approved_at=HACE_MUCHO,
                preparation_status=PreparationStatus.IN_QUEUE)
    o.updated_at = HACE_MUCHO
    return o


def test_hoja_drive_falta_fila_y_fila_huerfana(s, monkeypatch):
    en_hoja = _vivo(s, "BOP-40")
    falta = _vivo(s, "BOP-41")
    _foto(s, en_hoja.id, "BOP-40")
    _foto(s, "id-que-ya-no-existe", "BOP-0001")
    s.commit()
    # `updated_at` se re-estampa al guardar: se fija a mano para el margen.
    s.query(Order).update({Order.updated_at: HACE_MUCHO})
    s.commit()
    res = _correr(cm.hoja_drive, _ctx(s))
    assert _ids(res) == {falta.id, "id-que-ya-no-existe"}
    huerfana = next(h for h in res if h.entidad_id == "id-que-ya-no-existe")
    assert huerfana.entidad_tipo == "fila_hoja" and huerfana.etiqueta == "BOP-0001"


def test_hoja_drive_limpia_y_sin_espejo_no_dice_nada(s):
    o = _vivo(s, "BOP-42")
    s.commit()
    assert _correr(cm.hoja_drive, _ctx(s)) == []          # el espejo aún no escribió
    _foto(s, o.id, "BOP-42")
    s.commit()
    assert _correr(cm.hoja_drive, _ctx(s)) == []


def test_hoja_drive_respeta_el_margen_de_la_sincronizacion(s):
    en_hoja = _vivo(s, "BOP-43")
    _foto(s, en_hoja.id, "BOP-43")
    _vivo(s, "BOP-44")
    s.commit()
    s.query(Order).update({Order.updated_at: AHORA - timedelta(minutes=10)})
    s.commit()
    assert _correr(cm.hoja_drive, _ctx(s)) == []          # tocado hace 10 min


# --- 10 · Factura emitida sin enviar ----------------------------------------------


def test_factura_sin_enviar(s):
    kw = dict(invoice_status=InvoiceStatus.INVOICED_BY_ERP, factusol_invoice_serie=5)
    sin = _pedido(s, "BOP-50", factusol_invoice_number="260050", **kw)
    _historial(s, sin, StatusDomain.INVOICE, "invoiced_by_erp", AHORA - timedelta(days=10))
    enviada = _pedido(s, "BOP-51", factusol_invoice_number="260051", **kw)
    _historial(s, enviada, StatusDomain.INVOICE, "invoiced_by_erp", AHORA - timedelta(days=10))
    s.add(AuditLog(action="erp.invoice_emailed", target_type="order", target_id=enviada.id))
    reciente = _pedido(s, "BOP-52", factusol_invoice_number="260052", **kw)
    _historial(s, reciente, StatusDomain.INVOICE, "invoiced_by_erp", AHORA - timedelta(days=2))
    s.commit()
    res = _correr(cm.factura_sin_enviar, _ctx(s))
    assert _ids(res) == {sin.id}
    assert "5-260050" in res[0].detalle


# --- 11 · Pedido esperando aprobación ---------------------------------------------


def test_pedido_sin_aprobar(s):
    viejo = _pedido(s, "BOP-60", preparation_status=PreparationStatus.PENDING_REVIEW)
    _pedido(s, "BOP-61", preparation_status=PreparationStatus.PENDING_REVIEW,
            placed_at=AHORA - timedelta(days=2))                           # reciente
    _pedido(s, "BOP-62", preparation_status=PreparationStatus.PENDING_REVIEW,
            approved_at=HACE_MUCHO)                                         # aprobado
    _pedido(s, "BOPRIN-63", source=OrderSource.WOOCOMMERCE, woo_status="pending",
            preparation_status=PreparationStatus.PENDING_REVIEW)            # carrito sin pagar
    _pedido(s, "BOP-64", preparation_status=PreparationStatus.PENDING_REVIEW,
            cancelled_at=HACE_MUCHO)                                        # anulado
    s.commit()
    assert _ids(_correr(cm.pedido_sin_aprobar, _ctx(s))) == {viejo.id}


# --- FACTUSOL ----------------------------------------------------------------------


def _fac(serie: int, codigo: int, *, net: float, total: float | None = None,
         estfac: str = "0", fecha: str = "2026-08-01", **extra: Any) -> dict[str, Any]:
    return {"TIPFAC": str(serie), "CODFAC": codigo, "NET1FAC": net, "BAS1FAC": net,
            "TOTFAC": total if total is not None else round(net * 1.21, 2),
            "ESTFAC": estfac, "FECFAC": f"{fecha}T00:00:00", "CNOFAC": "Cliente SL",
            **extra}


def _vincular(s: Session, *facturas: tuple[int, int]) -> None:
    """Un pedido de BoHub por factura: el Cuadre solo mira facturas vinculadas."""
    for serie, codigo in facturas:
        _pedido(s, f"PED-{serie}-{codigo}", invoice_status=InvoiceStatus.INVOICED_BY_ERP,
                factusol_invoice_number=str(codigo), factusol_invoice_serie=serie)
    s.commit()


def _lin(serie: int, codigo: int, total: float, desc: str = "Impresora") -> dict[str, Any]:
    return {"TIPLFA": str(serie), "CODLFA": codigo, "TOTLFA": total, "DESLFA": desc,
            "ARTLFA": "ART"}


# --- 1 · Factura con líneas que no son suyas --------------------------------------


def test_factura_contaminada_dispara_y_las_limpias_no(s):
    _vincular(s, *[(5, c) for c in range(1, 8)], (2, 2))
    client = FakeFactusol(
        F_FAC=[
            _fac(5, 1, net=100),                                     # limpia
            _fac(5, 2, net=100),                                     # contaminada (#382)
            _fac(5, 3, net=100, IDTO1FAC=10, BAS1FAC=90),           # descuento en cabecera
            _fac(5, 4, net=100),                                     # línea DESCUENTO
            _fac(5, 5, net=104),                                     # recargo PayPal 4 %
            _fac(5, 6, net=104, IDTO1FAC=0),                         # PayPal con DESCUENTO
            _fac(5, 7, net=100),                                     # contaminada + DESCUENTO
            _fac(2, 2, net=300),                                     # homónima de otra serie
        ],
        F_LFA=[
            _lin(5, 1, 60), _lin(5, 1, 40),
            _lin(5, 2, 100), _lin(5, 2, 300, "Línea del pedido de la otra serie"),
            _lin(5, 3, 90),
            _lin(5, 4, 100), _lin(5, 4, -10, "DESCUENTO 10%"),
            _lin(5, 5, 50), _lin(5, 5, 50),
            _lin(5, 6, 110), _lin(5, 6, -10, "DESCUENTO"),
            _lin(5, 7, 100), _lin(5, 7, 250, "Ajena"), _lin(5, 7, -10, "DESCUENTO"),
            _lin(2, 2, 300),
        ],
    )
    res = _correr(cf.factura_lineas_ajenas, _ctx(s, client))
    assert _ids(res) == {"5-000002", "5-000007"}
    h = next(r for r in res if r.entidad_id == "5-000002")
    assert h.huella_datos == {"suma": 400.0, "base": 100.0, "lineas": 2}
    assert client.lecturas == {"F_FAC": 1, "F_LFA": 1}
    assert client.ejercicios == {"2026"}


def test_lineas_ajenas_solo_mira_facturas_de_pedidos_de_bohub(s):
    """Una factura solo de FACTUSOL (sin pedido en BoHub) no es un descuadre,
    aunque sus líneas no cuadren."""
    _vincular(s, (5, 2))
    client = FakeFactusol(
        F_FAC=[_fac(5, 2, net=100), _fac(3, 9, net=100)],
        F_LFA=[_lin(5, 2, 100), _lin(5, 2, 300, "Ajena"), _lin(3, 9, 400)],
    )
    assert _ids(_correr(cf.factura_lineas_ajenas, _ctx(s, client))) == {"5-000002"}


def test_lineas_cuadran_con_descuento_global_y_paypal():
    fac = {"NET1FAC": 104, "BAS1FAC": 104}
    assert cf.lineas_cuadran([{"TOTLFA": 100}], fac)                    # × 1,04
    fac = {"NET1FAC": 100, "IDTO1FAC": 10, "BAS1FAC": 90}
    assert cf.lineas_cuadran([{"TOTLFA": 90}], fac)                     # − DESCUENTO
    assert cf.lineas_cuadran([{"TOTLFA": 100}], fac)                    # líneas sin dto
    assert not cf.lineas_cuadran([{"TOTLFA": 100}, {"TOTLFA": 80}], fac)


# --- 2 · Cobro descuadrado --------------------------------------------------------


def _facturado(s: Session, numero: str, codigo: int, cobro: str | None) -> Order:
    return _pedido(s, numero, invoice_status=InvoiceStatus.INVOICED_BY_ERP,
                   factusol_invoice_number=str(codigo), factusol_invoice_serie=5,
                   factusol_cobro_status=cobro)


def test_cobro_descuadrado_en_los_dos_sentidos(s):
    bohub_cobrada = _facturado(s, "BOP-70", 70, "cobrada")
    bohub_pendiente = _facturado(s, "BOP-71", 71, "pendiente")
    _facturado(s, "BOP-72", 72, "cobrada")                       # cuadra
    _facturado(s, "BOP-73", 73, None)                            # sin comprobar
    _facturado(s, "BOP-74", 74, None)                            # cobrada sin líneas
    s.commit()
    client = FakeFactusol(
        F_FAC=[_fac(5, 70, net=100, estfac="0"), _fac(5, 71, net=100, estfac="2"),
               _fac(5, 72, net=100, estfac="2"), _fac(5, 73, net=100, estfac="0"),
               _fac(5, 74, net=100, estfac="2"),                 # cobrada sin líneas
               _fac(3, 75, net=100, estfac="2")],                # ídem, sin pedido: no
        F_LCO=[{"TFALCO": "5", "CFALCO": 71, "IMPLCO": 121},
               {"TFALCO": "5", "CFALCO": 72, "IMPLCO": 121}],
    )
    res = _correr(cf.cobro_descuadrado, _ctx(s, client))
    assert _ids(res) == {bohub_cobrada.id, bohub_pendiente.id, "5-000074"}
    sin_lineas = next(h for h in res if h.entidad_id == "5-000074")
    assert "sin ninguna línea de cobro" in sin_lineas.detalle


def test_cobro_con_f_lco_vacia_no_juzga(s):
    """F_LCO vacía = lectura rota: la comprobación no corre (no da por
    resuelto, ni por nuevo, lo que no ha podido leer)."""
    from app.erp.cuadre.contexto import FactusolNoDisponible

    client = FakeFactusol(F_FAC=[_fac(5, 74, net=100, estfac="2")], F_LCO=[])
    with pytest.raises(FactusolNoDisponible, match="F_LCO"):
        _correr(cf.cobro_descuadrado, _ctx(s, client))
    with pytest.raises(FactusolNoDisponible):
        _correr(cf.factura_sin_cobro, _ctx(s, client))


def test_una_lectura_fallida_se_recuerda_en_la_pasada(s):
    """Si DELSOL falla, no se le vuelve a pedir la misma tabla desde cada
    comprobación de la pasada."""
    from app.erp.cuadre.contexto import FactusolNoDisponible

    class Caido(FakeFactusol):
        def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
            self.lecturas[tabla] += 1
            raise TimeoutError("DELSOL no responde")

    client = Caido()
    ctx = _ctx(s, client)
    for func in (cf.factura_lineas_ajenas, cf.factura_sin_cobro, cf.factura_sin_vincular):
        with pytest.raises(FactusolNoDisponible):
            _correr(func, ctx)
    assert client.lecturas["F_FAC"] == 1


# --- 3 · Factura emitida sin cobro --------------------------------------------------


def test_factura_sin_cobro_con_importe_pendiente(s):
    o = _facturado(s, "BOP-80", 80, "pendiente")
    s.commit()
    client = FakeFactusol(
        F_FAC=[_fac(5, 80, net=100, total=121, fecha="2026-08-01"),      # parcial, vieja
               _fac(5, 81, net=100, total=121, fecha="2026-08-01"),      # cobrada entera
               _fac(5, 82, net=100, total=121, fecha="2026-09-30"),      # reciente
               _fac(5, 83, net=100, total=121, estfac="2"),              # cobrada sin líneas
               _fac(5, 84, net=100, total=121, fecha="2026-03-01"),      # sin pedido en BoHub
               _fac(3, 85, net=100, total=121, fecha="2026-02-01")],     # serie 3, sin pedido
        F_LCO=[{"TFALCO": "5", "CFALCO": 80, "IMPLCO": 21},
               {"TFALCO": "5", "CFALCO": 81, "IMPLCO": 121}],
    )
    _vincular(s, (5, 81), (5, 82), (5, 83))
    res = _correr(cf.factura_sin_cobro, _ctx(s, client))
    # Solo la del pedido con saldo; la 83 es de la comprobación 2 y la 84 y la 85
    # son facturas solo de FACTUSOL (sin pedido en BoHub): no son un descuadre.
    assert _ids(res) == {"5-000080"}
    assert res[0].datos["importe"] == 100.0
    assert res[0].enlace == f"/erp/orders/{o.id}"
    assert res[0].huella_datos == {"pendiente": 100.0, "total": 121.0}


# --- 9 · Factura sin vincular / vínculo roto -------------------------------------------


def test_factura_sin_vincular_y_vinculo_roto(s):
    sin_vincular = _pedido(s, "BOPRIN-99866")
    roto = _facturado(s, "BOP-91", 526080, "pendiente")         # hueco / anulada
    _facturado(s, "BOP-92", 526081, "pendiente")                 # existe: limpio
    _facturado(s, "BOP-93", 525900, "pendiente")                 # del ejercicio anterior
    _pedido(s, "BOPRIN-99867")                                   # sin factura en FACTUSOL
    s.commit()
    client = FakeFactusol(F_FAC=[
        _fac(5, 526079, net=100, REFFAC="BOP-099866"),
        _fac(5, 526081, net=100),
        _fac(5, 526085, net=100),
    ])
    res = _correr(cf.factura_sin_vincular, _ctx(s, client))
    assert _ids(res) == {sin_vincular.id, roto.id}
    assert "5-526079" in next(h for h in res if h.entidad_id == sin_vincular.id).detalle
    assert "5-526080" in next(h for h in res if h.entidad_id == roto.id).detalle


# --- 12 · Proforma aceptada sin convertir ------------------------------------------


def test_proforma_aceptada_sin_convertir(s):
    _pedido(s, "PRO-574", source=OrderSource.FACTUSOL_PROFORMA, external_id="574")
    s.commit()
    client = FakeFactusol(F_PRE=[
        {"TIPPRE": "1", "CODPRE": 573, "ESTPRE": 1, "FECPRE": "2026-07-01", "TOTPRE": 500},
        {"TIPPRE": "1", "CODPRE": 574, "ESTPRE": 1, "FECPRE": "2026-07-01", "TOTPRE": 500},
        {"TIPPRE": "1", "CODPRE": 575, "ESTPRE": 0, "FECPRE": "2026-07-01", "TOTPRE": 500},
        {"TIPPRE": "1", "CODPRE": 576, "ESTPRE": 1, "FECPRE": "2026-09-30", "TOTPRE": 500},
    ])
    res = _correr(cf.proforma_sin_convertir, _ctx(s, client))
    assert _ids(res) == {"1-000573"}
    assert res[0].datos["importe"] == 500.0


# --- 1 · bandas de portes con neto propio (casos reales del 03/10/2026) -------------


def _fac_portes(codigo: int, *, lineas: float, neto_portes: float, cliente: str):
    """Factura de la serie 2 como las leídas en producción: las líneas en la
    banda 1 y, en la banda 3, los portes (IPOR3 = 19) con un neto propio que no
    es de ninguna línea (el 4 % de PayPal sobre líneas + portes)."""
    return {
        "TIPFAC": "2", "CODFAC": codigo, "CNOFAC": cliente, "ESTFAC": "2",
        "FECFAC": "2026-09-30T00:00:00",
        "NET1FAC": lineas, "BAS1FAC": lineas,
        "NET3FAC": neto_portes, "IPOR3FAC": 19.00,
        "BAS3FAC": round(neto_portes + 19.00, 2),
        "TOTFAC": round(lineas + neto_portes + 19.00, 2),
    }


def test_banda_de_portes_con_neto_propio_no_es_contaminacion(s):
    """2-526098 (PROTAVIS GMBH, 4 líneas = 215,00; NET3 = 9,36) y 2-526103
    (innovescence, 1 línea = 25,00; NET3 = 1,76) salían con «diferencia» igual
    al neto de la banda de portes: no son contaminación."""
    _vincular(s, (2, 526098), (2, 526103))
    client = FakeFactusol(
        F_FAC=[
            _fac_portes(526098, lineas=215.00, neto_portes=9.36, cliente="PROTAVIS GMBH"),
            _fac_portes(526103, lineas=25.00, neto_portes=1.76, cliente="innovescence"),
        ],
        F_LFA=[
            _lin(2, 526098, 100.00), _lin(2, 526098, 60.00), _lin(2, 526098, 35.00),
            _lin(2, 526098, 20.00),
            _lin(2, 526103, 25.00),
        ],
    )
    assert _correr(cf.factura_lineas_ajenas, _ctx(s, client)) == []


def test_contaminacion_382_sigue_saltando_aunque_haya_banda_de_portes(s):
    """Las líneas suman DE MÁS (renglones del pedido homónimo de otra serie):
    quitar el neto de la banda de portes solo baja la base, así que sigue
    saltando."""
    _vincular(s, (2, 526110), (2, 526111))
    contaminada = _fac_portes(526110, lineas=215.00, neto_portes=9.36, cliente="Cliente SL")
    client = FakeFactusol(
        F_FAC=[contaminada],
        F_LFA=[_lin(2, 526110, 215.00), _lin(2, 526110, 480.00, "Renglón de otra serie")],
    )
    res = _correr(cf.factura_lineas_ajenas, _ctx(s, client))
    assert _ids(res) == {"2-526110"}
    assert res[0].huella_datos["suma"] == 695.00
    # Y una que se queda CORTA en algo que no es el neto de una banda de portes
    # (faltan líneas) también salta.
    corta = _fac_portes(526111, lineas=215.00, neto_portes=9.36, cliente="Cliente SL")
    client = FakeFactusol(F_FAC=[corta], F_LFA=[_lin(2, 526111, 150.00)])
    assert _ids(_correr(cf.factura_lineas_ajenas, _ctx(s, client))) == {"2-526111"}
