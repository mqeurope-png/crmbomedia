"""Comprobaciones del Cuadre que solo leen la BD de BoHub (fuente `mysql`).

Corren al momento con «Comprobar ahora» y en el job nocturno. SOLO LECTURA:
ninguna corrige nada; cada descuadre enlaza a la pantalla donde ya existe la
acción para arreglarlo.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.erp.cuadre.contexto import Contexto
from app.erp.cuadre.registry import (
    ENTIDAD_CUENTA,
    ENTIDAD_FILA_HOJA,
    ENTIDAD_PEDIDO,
    FUENTE_MYSQL,
    Hallazgo,
    comprobacion,
)

#: Enlaces a las pantallas donde ya existe la acción de arreglo.
SAT_ENVIADOS = "/erp/sat?tab=enviados"
SAT_SIN_ENVIO = "/erp/sat?tab=sin_envio"
POR_REVISAR = "/erp/orders?queue=por_revisar"
SEGUIMIENTO = "/erp/seguimiento"

#: Margen antes de dar por descuadrada la hoja: el espejo de Drive pasa cada
#: pocos minutos; un pedido tocado hace menos de esto puede no haber llegado.
MARGEN_HOJA_MINUTOS = 60


def _v(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _s(value: Any) -> str:
    return str(value if value is not None else "").strip()


def enlace_pedido(order_id: str) -> str:
    return f"/erp/orders/{order_id}"


def _etiqueta(order: Any) -> str:
    return order.order_number or order.id


def en_flujo(order: Any) -> bool:
    """Pedido vivo para las comprobaciones de trabajo pendiente: ni anulado, ni
    quitado de las listas a mano, ni gestionado fuera de BoHub."""
    return (
        order.cancelled_at is None
        and order.seguimiento_excluded_at is None
        and order.externally_processed_at is None
    )


def fecha_real(order: Any, domain: str, estados: set[str]) -> datetime | None:
    """Fecha del HECHO real (no la estampada en la importación): la misma regla
    que la hoja de seguimiento."""
    from app.erp.seguimiento import _real_event_date  # noqa: PLC0415

    return _real_event_date(order, domain, estados)


def _fecha(value: datetime | None) -> str:
    return value.date().isoformat() if value else "—"


# --- 4 · «No requiere envío» con datos de envío --------------------------------------


@comprobacion(
    id="sin_envio_con_datos", orden=4,
    titulo="«No requiere envío» con datos de envío",
    descripcion="Pedido marcado «No requiere envío» que sí tiene tracking, envío de "
                "Genei o courier apuntado.",
    severidad="media", fuente=FUENTE_MYSQL, grupo="envios",
)
def sin_envio_con_datos(ctx: Contexto) -> Iterator[Hallazgo]:
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415
    from app.erp.shipping_courier import external_state  # noqa: PLC0415

    for o in ctx.pedidos():
        if not o.shipping_not_required or o.cancelled_at is not None:
            continue
        tracking = _s(o.tracking_number)
        codigo = _s(genei_state_of(o).get("shipment_code"))
        courier = _s(external_state(o).get("courier"))
        if not (tracking or codigo or courier):
            continue
        partes = [
            f"tracking {tracking}" if tracking else "",
            f"envío Genei {codigo}" if codigo else "",
            f"courier {courier}" if courier else "",
        ]
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id, etiqueta=_etiqueta(o),
            detalle="Marcado «No requiere envío» pero tiene "
                    + ", ".join(p for p in partes if p) + ".",
            pista_de_arreglo="Si sí se envió, quítale «No requiere envío» en la Cola SAT "
                             "(pestaña «Sin envío» → «Requiere envío»); si no, borra el "
                             "tracking en la ficha del pedido.",
            enlace=enlace_pedido(o.id), arreglo_enlace=SAT_SIN_ENVIO,
            arreglo_boton="Ir a «Sin envío»",
            huella_datos={"tracking": tracking, "genei": codigo, "courier": courier},
        )


# --- 5 · Enviado con tracking sin aviso al cliente -------------------------------------


def _avisos_enviados(ctx: Contexto) -> set[str]:
    """Pedidos con el aviso de envío mandado (evento `erp.shipment_emailed`)."""
    from app.erp.shipment_email import AUDIT_ACTION  # noqa: PLC0415
    from app.models.crm import AuditLog  # noqa: PLC0415

    def leer() -> set[str]:
        return {
            tid for tid in ctx.session.scalars(
                select(AuditLog.target_id).where(
                    AuditLog.action == AUDIT_ACTION, AuditLog.target_type == "order",
                ).distinct()
            ) if tid
        }

    return ctx.cached("avisos_enviados", leer)


def _aviso_no_toca(order: Any) -> bool:
    """El pedido no espera aviso de BoHub: ya se mandó (`sent`), se creó con el
    aviso automático APAGADO (`disabled`: decisión de Configuración), o es un
    envío de Genei de antes del aviso de BoHub (sin bloque: ya lo avisó Genei).
    Lo que queda —pendiente, con error, o un envío de otro courier sin aviso—
    es un cliente sin avisar."""
    from app.erp.shipment_email import _state  # noqa: PLC0415
    from app.erp.shipping_courier import is_genei_shipment  # noqa: PLC0415

    block = _state(order).get("customer_email")
    if isinstance(block, dict) and block:
        return block.get("status") in ("sent", "disabled") or bool(block.get("sent_at"))
    return is_genei_shipment(order)


def _tracking(order: Any) -> str:
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415

    return _s(order.tracking_number) or _s(genei_state_of(order).get("tracking"))


@comprobacion(
    id="enviado_sin_aviso", orden=5,
    titulo="Enviado con tracking sin aviso al cliente",
    descripcion="Pedido en tránsito o entregado, con tracking, al que no se le ha "
                "mandado el aviso de envío.",
    severidad="media", fuente=FUENTE_MYSQL, grupo="envios",
    dias_defecto=30, dias_texto="Solo envíos de los últimos N días",
)
def enviado_sin_aviso(ctx: Contexto) -> Iterator[Hallazgo]:
    ventana = ctx.dias("enviado_sin_aviso", 30)
    avisados = _avisos_enviados(ctx)
    for o in ctx.pedidos():
        if not en_flujo(o) or o.completed_at is not None or o.shipping_not_required:
            continue
        if _v(o.transport_status) not in ("in_transit", "delivered"):
            continue
        tracking = _tracking(o)
        if not tracking or o.id in avisados or _aviso_no_toca(o):
            continue
        salida = fecha_real(o, "transport", {"in_transit", "delivered"})
        dias = ctx.dias_desde(salida)
        if dias is None or dias > ventana:
            continue
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id, etiqueta=_etiqueta(o),
            detalle=f"Salió el {_fecha(salida)} con tracking {tracking} y el cliente no "
                    "ha recibido el aviso de envío.",
            pista_de_arreglo="Mándalo con «Enviar aviso» en la Cola SAT, pestaña «Enviados».",
            enlace=enlace_pedido(o.id), arreglo_enlace=SAT_ENVIADOS,
            arreglo_boton="Ir a «Enviados»",
            huella_datos={"tracking": tracking},
            datos={"dias": dias},
        )


# --- 6 · Enviado hace N días sin entrega ni incidencia --------------------------------


@comprobacion(
    id="envio_sin_entregar", orden=6,
    titulo="Enviado sin entrega ni incidencia",
    descripcion="Pedido en tránsito desde hace más de N días sin pasar a entregado "
                "ni a incidencia.",
    severidad="media", fuente=FUENTE_MYSQL, grupo="envios",
    dias_defecto=10, dias_texto="Avisar pasados N días en tránsito",
)
def envio_sin_entregar(ctx: Contexto) -> Iterator[Hallazgo]:
    umbral = ctx.dias("envio_sin_entregar", 10)
    for o in ctx.pedidos():
        if not en_flujo(o) or o.completed_at is not None:
            continue
        if _v(o.transport_status) != "in_transit":
            continue
        salida = fecha_real(o, "transport", {"in_transit"})
        dias = ctx.dias_desde(salida)
        if dias is None or dias <= umbral:
            continue
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id, etiqueta=_etiqueta(o),
            detalle=f"En tránsito desde el {_fecha(salida)} ({dias} días) sin entrega "
                    "ni incidencia.",
            pista_de_arreglo="Pulsa «Actualizar estado» en la Cola SAT, pestaña «Enviados» "
                             "(Genei), o márcalo entregado / con incidencia en la ficha.",
            enlace=enlace_pedido(o.id), arreglo_enlace=SAT_ENVIADOS,
            arreglo_boton="Ir a «Enviados»",
            huella_datos={"transporte": "in_transit", "salida": _fecha(salida)},
            datos={"dias": dias},
        )


# --- 7 · Entregado, facturado y cobrado sin completar ----------------------------------


@comprobacion(
    id="entregado_sin_completar", orden=7,
    titulo="Entregado, facturado y cobrado sin completar",
    descripcion="Pedido entregado hace más de N días, con la factura cobrada, sin "
                "«Marcar completado».",
    severidad="media", fuente=FUENTE_MYSQL, grupo="envios",
    dias_defecto=14, dias_texto="Avisar pasados N días desde la entrega",
)
def entregado_sin_completar(ctx: Contexto) -> Iterator[Hallazgo]:
    from app.erp.workflow import is_cobrada, is_invoiced  # noqa: PLC0415

    umbral = ctx.dias("entregado_sin_completar", 14)
    for o in ctx.pedidos():
        if not en_flujo(o) or o.completed_at is not None:
            continue
        if _v(o.transport_status) != "delivered" or not is_invoiced(o) or not is_cobrada(o):
            continue
        entrega = fecha_real(o, "transport", {"delivered"})
        dias = ctx.dias_desde(entrega)
        if dias is None or dias <= umbral:
            continue
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id, etiqueta=_etiqueta(o),
            detalle=f"Entregado el {_fecha(entrega)} ({dias} días), facturado y cobrado, "
                    "y sigue sin completar.",
            pista_de_arreglo="Ciérralo con «Marcar completado» en la ficha del pedido.",
            enlace=enlace_pedido(o.id), arreglo_enlace=enlace_pedido(o.id),
            arreglo_boton="Abrir el pedido",
            huella_datos={"transporte": "delivered", "cobro": "cobrada"},
            datos={"dias": dias},
        )


# --- 8 · Hoja de Drive descuadrada -----------------------------------------------------


def _numero_de_fila(values_json: str | None) -> str:
    import json  # noqa: PLC0415

    from app.erp.seguimiento import SEGUIMIENTO_COLUMNS_V2  # noqa: PLC0415

    try:
        fila = json.loads(values_json or "[]")
    except (TypeError, ValueError):
        return ""
    idx = SEGUIMIENTO_COLUMNS_V2.index("Nº pedido")
    return _s(fila[idx]) if isinstance(fila, list) and len(fila) > idx else ""


def _tocado_hace_poco(ctx: Contexto, order: Any) -> bool:
    """El pedido cambió hace menos de `MARGEN_HOJA_MINUTOS`: la sincronización
    de la hoja puede no haberlo recogido todavía."""
    tocado = getattr(order, "updated_at", None)
    if tocado is None:
        return False
    if tocado.tzinfo is None:
        tocado = tocado.replace(tzinfo=UTC)
    return (ctx.ahora - tocado).total_seconds() < MARGEN_HOJA_MINUTOS * 60


@comprobacion(
    id="hoja_drive", orden=8,
    titulo="Hoja de Drive descuadrada",
    descripcion="Pedido vivo de BoHub sin fila en «Seguimiento (app)», o fila de BoHub "
                "en la hoja cuyo pedido ya no existe.",
    severidad="media", fuente=FUENTE_MYSQL, grupo="documentos",
)
def hoja_drive(ctx: Contexto) -> Iterator[Hallazgo]:
    from app.erp import seguimiento as core  # noqa: PLC0415
    from app.erp.api.seguimiento import _rows  # noqa: PLC0415
    from app.erp.models import Order, SeguimientoSnapshot  # noqa: PLC0415
    from app.erp.models.seguimiento_mirror import KIND_ORDER  # noqa: PLC0415
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    if series_config(ctx.session).get("drive_legacy_insert"):
        return                                # modo antiguo: no hay espejo
    foto = {
        s.row_id: s for s in ctx.session.scalars(
            select(SeguimientoSnapshot).where(SeguimientoSnapshot.kind == KIND_ORDER)
        )
    }
    if not foto:
        return                                # el espejo aún no ha escrito la hoja
    # Las mismas filas que escribe el espejo (`drive_live_rows` +
    # `drive_completados_rows`), construyendo el seguimiento una sola vez.
    filas = _rows(ctx.session)
    esperadas = {
        str(r["id"]): r for r in (
            *core.filter_rows(filas, en_curso=True, sort="fecha", direction="desc"),
            *core.filter_rows(filas, estado="completado", sort="fecha", direction="desc"),
        ) if r.get("id")
    }
    pedidos = {o.id: o for o in ctx.pedidos()}
    for rid, fila in esperadas.items():
        if rid in foto:
            continue
        if _tocado_hace_poco(ctx, pedidos.get(rid)):
            continue                          # aún puede estar de camino
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PEDIDO, entidad_id=rid,
            etiqueta=_s(fila.get("order_number")) or rid,
            detalle="Pedido vivo de BoHub que no tiene fila en «Seguimiento (app)».",
            pista_de_arreglo="Pulsa «Actualizar hoja de Drive» en Seguimiento (o espera a "
                             "la sincronización automática); si sigue faltando, avisa.",
            enlace=enlace_pedido(rid), arreglo_enlace=SEGUIMIENTO,
            arreglo_boton="Ir a Seguimiento",
            huella_datos={"falta_en_hoja": True},
        )
    existentes = set(ctx.session.scalars(
        select(Order.id).where(Order.id.in_(sorted(foto)))
    )) if foto else set()
    for rid, snap in foto.items():
        if rid in existentes:
            continue
        numero = _numero_de_fila(snap.values_json)
        yield Hallazgo(
            entidad_tipo=ENTIDAD_FILA_HOJA, entidad_id=rid,
            etiqueta=numero or rid,
            detalle="Fila de BoHub en «Seguimiento (app)» cuyo pedido ya no existe en BoHub"
                    + (f" (Nº pedido {numero})." if numero else "."),
            pista_de_arreglo="Revisa la fila en la hoja (columna «id» oculta) y bórrala a "
                             "mano si sobra; la próxima sincronización ya no la escribirá.",
            enlace=SEGUIMIENTO, arreglo_enlace=SEGUIMIENTO, arreglo_boton="Ir a Seguimiento",
            huella_datos={"huerfana": True},
        )


# --- 10 · Factura emitida sin enviar al cliente --------------------------------------


@comprobacion(
    id="factura_sin_enviar", orden=10,
    titulo="Factura emitida sin enviar al cliente",
    descripcion="Pedido con factura vinculada hace más de N días que no se ha mandado "
                "al cliente.",
    severidad="media", fuente=FUENTE_MYSQL, grupo="documentos",
    dias_defecto=7, dias_texto="Avisar pasados N días desde la factura",
)
def factura_sin_enviar(ctx: Contexto) -> Iterator[Hallazgo]:
    from app.erp.linked_invoice import get_linked_invoice  # noqa: PLC0415
    from app.erp.models import SeguimientoOverride  # noqa: PLC0415
    from app.erp.seguimiento import _fecha_factura  # noqa: PLC0415
    from app.erp.workflow import latest_invoice_emailed_map  # noqa: PLC0415

    umbral = ctx.dias("factura_sin_enviar", 7)
    candidatos = []
    for o in ctx.pedidos():
        if not en_flujo(o) or o.completed_at is not None:
            continue
        if _v(o.invoice_status) not in ("generated", "invoiced_by_erp"):
            continue
        factura = get_linked_invoice(o)
        if factura is None:
            continue
        fecha = _fecha_factura(o)
        dias = ctx.dias_desde(fecha)
        if dias is None or dias <= umbral:
            continue
        candidatos.append((o, factura, fecha, dias))
    if not candidatos:
        return
    ids = [o.id for o, *_ in candidatos]
    enviados = latest_invoice_emailed_map(ctx.session, ids)
    a_mano = {
        oid for oid, valor in ctx.session.execute(
            select(SeguimientoOverride.order_id, SeguimientoOverride.value).where(
                SeguimientoOverride.order_id.in_(ids),
                SeguimientoOverride.column_key == "factura_enviada",
            )
        ) if _s(valor)
    }
    for o, factura, fecha, dias in candidatos:
        if o.id in enviados or o.id in a_mano:
            continue
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id, etiqueta=_etiqueta(o),
            detalle=f"Factura {factura.numero} del {_fecha(fecha)} ({dias} días) sin "
                    "enviar al cliente.",
            pista_de_arreglo="Mándala con «Enviar factura al cliente» en la ficha del pedido.",
            enlace=enlace_pedido(o.id), arreglo_enlace=enlace_pedido(o.id),
            arreglo_boton="Abrir el pedido",
            huella_datos={"factura": factura.numero},
            datos={"dias": dias, "factura": factura.numero},
        )


# --- 11 · Pedido esperando aprobación --------------------------------------------------


@comprobacion(
    id="pedido_sin_aprobar", orden=11,
    titulo="Pedido esperando aprobación",
    descripcion="Pedido que lleva más de N días esperando a que alguien lo apruebe.",
    severidad="baja", fuente=FUENTE_MYSQL, grupo="documentos",
    dias_defecto=7, dias_texto="Avisar pasados N días sin aprobar",
)
def pedido_sin_aprobar(ctx: Contexto) -> Iterator[Hallazgo]:
    from app.erp.sample_orders import is_sample_order  # noqa: PLC0415
    from app.erp.seguimiento import woo_status_visibility  # noqa: PLC0415
    from app.erp.workflow import is_approved  # noqa: PLC0415

    umbral = ctx.dias("pedido_sin_aprobar", 7)
    for o in ctx.pedidos():
        if not en_flujo(o) or o.completed_at is not None or is_sample_order(o):
            continue
        if is_approved(o):
            continue
        oculto, _motivo, _reemb = woo_status_visibility(o)
        if oculto and o.seguimiento_forced_at is None:
            continue                          # carrito web sin pagar: no es trabajo
        alta = o.placed_at or o.created_at
        dias = ctx.dias_desde(alta)
        if dias is None or dias <= umbral:
            continue
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id, etiqueta=_etiqueta(o),
            detalle=f"Esperando aprobación desde el {_fecha(alta)} ({dias} días).",
            pista_de_arreglo="Apruébalo (o anúlalo) desde la bandeja, cola «Por revisar».",
            enlace=enlace_pedido(o.id), arreglo_enlace=POR_REVISAR,
            arreglo_boton="Ir a «Por revisar»",
            huella_datos={"pendiente_aprobacion": True},
            datos={"dias": dias},
        )


# --- 13 · Sincronización colgada ---------------------------------------------------

#: Sistemas con una sincronización PERIÓDICA de cada cuenta (el «sin ninguna
#: correcta en 24 h» solo tiene sentido en ellos): AgileCRM cada hora y Brevo
#: (cuentas en modo live) cada 12 h.
SYNC_PERIODICA: dict[str, str] = {"agilecrm": "sync_contacts", "brevo": "sync_contacts"}
SIN_SYNC_OK_HORAS = 24


@comprobacion(
    id="sincronizacion_colgada", orden=13,
    titulo="Sincronización colgada",
    descripcion="Cuenta de integración con una sincronización en curso desde hace más de N "
                "horas, o habilitada sin ninguna sincronización correcta en las últimas 24 h.",
    severidad="media", fuente=FUENTE_MYSQL, grupo="integraciones",
    dias_defecto=3, dias_texto="Avisar si una sincronización lleva más de N horas en curso",
)
def sincronizacion_colgada(ctx: Contexto) -> Iterator[Hallazgo]:
    from datetime import timedelta  # noqa: PLC0415

    from sqlalchemy import func  # noqa: PLC0415

    from app.models.crm import SyncLog, SyncStatus  # noqa: PLC0415
    from app.models.integration_settings import (  # noqa: PLC0415
        IntegrationAccount,
        IntegrationMode,
    )
    from app.workers.huerfanas import INFLIGHT_STATUSES, inicio_sql  # noqa: PLC0415

    horas = ctx.dias("sincronizacion_colgada", 3)
    colgada_antes = ctx.ahora - timedelta(hours=horas)
    sin_ok_desde = ctx.ahora - timedelta(hours=SIN_SYNC_OK_HORAS)
    ok = (SyncStatus.SUCCESS.value, SyncStatus.PARTIAL_SUCCESS.value)
    cuentas = ctx.session.scalars(
        select(IntegrationAccount).where(IntegrationAccount.enabled.is_(True))
    )
    for cuenta in cuentas:
        system = _v(cuenta.system)
        filtro = (SyncLog.system == cuenta.system, SyncLog.account_id == cuenta.account_id)
        colgada = ctx.session.scalars(
            select(SyncLog).where(
                *filtro, SyncLog.status.in_(INFLIGHT_STATUSES), inicio_sql() < colgada_antes,
            ).order_by(inicio_sql()).limit(1)
        ).first()
        sin_ok = False
        ultimo_ok = None
        operacion = SYNC_PERIODICA.get(system)
        periodica = operacion is not None and (
            system != "brevo" or _v(cuenta.mode) == IntegrationMode.LIVE.value
        )
        alta = cuenta.created_at
        if alta is not None and alta.tzinfo is None:
            alta = alta.replace(tzinfo=UTC)
        if periodica and (alta is None or alta < sin_ok_desde):
            ultimo_ok = ctx.session.scalar(
                select(func.max(SyncLog.finished_at)).where(
                    *filtro, SyncLog.operation == operacion, SyncLog.status.in_(ok),
                )
            )
            if ultimo_ok is not None and ultimo_ok.tzinfo is None:
                ultimo_ok = ultimo_ok.replace(tzinfo=UTC)
            sin_ok = ultimo_ok is None or ultimo_ok < sin_ok_desde
        if colgada is None and not sin_ok:
            continue
        partes = []
        if colgada is not None:
            inicio = colgada.started_at or colgada.created_at
            partes.append(
                f"«{colgada.operation}» en {colgada.status} desde el {_fecha(inicio)} "
                f"(más de {horas} h)"
            )
        if sin_ok:
            partes.append(
                f"ninguna «{operacion}» correcta en las últimas {SIN_SYNC_OK_HORAS} h"
                + (f" (la última, el {_fecha(ultimo_ok)})" if ultimo_ok else " (ni una)")
            )
        enlace = f"/admin/integrations/{system}/{cuenta.account_id}/sync-history"
        yield Hallazgo(
            entidad_tipo=ENTIDAD_CUENTA, entidad_id=f"{system}:{cuenta.account_id}",
            etiqueta=f"{system} · {cuenta.display_name or cuenta.account_id}",
            detalle="Sincronización colgada: " + "; ".join(partes) + ".",
            pista_de_arreglo="Una ejecución que lleva tanto «en curso» es de un worker que murió: "
                             "deja de bloquear sola y se cierra al reiniciar el worker. Mira el "
                             "error en el historial y lanza «Sincronizar ahora» en Integraciones.",
            enlace=enlace, arreglo_enlace=enlace, arreglo_boton="Ver historial",
            huella_datos={"colgada": colgada.id if colgada is not None else None,
                          "sin_sync_ok": sin_ok},
        )


# --- 15 / 16 · Envío Genei tramitado que BoHub no sigue -----------------------------

SAT_PENDIENTE_RECOGIDA = "/erp/sat?tab=pendiente_recogida"
#: Genei con el envío tramitado (estado 1+): hay paquete (o lo habrá en horas).
_GENEI_TRAMITADO = ("ready", "in_transit", "delivered", "incident")


def _genei(order: Any) -> dict[str, Any]:
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415

    return genei_state_of(order)


def _cuando(valor: Any) -> datetime | None:
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(str(valor))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@comprobacion(
    id="envio_tramitado_sin_enviar", orden=15,
    titulo="Envío tramitado que BoHub no da por enviado",
    descripcion="Envío de Genei ya tramitado (con etiqueta y tracking) y el pedido sigue "
                "«sin enviar» en BoHub: hay un paquete que la app no está siguiendo.",
    severidad="alta", fuente=FUENTE_MYSQL, grupo="envios",
)
def envio_tramitado_sin_enviar(ctx: Contexto) -> Iterator[Hallazgo]:
    for o in ctx.pedidos():
        if o.cancelled_at is not None or _v(o.transport_status) != "not_shipped":
            continue
        g = _genei(o)
        if not g.get("shipment_code") or g.get("state_bucket") not in _GENEI_TRAMITADO:
            continue
        aviso = g.get("customer_email") if isinstance(g.get("customer_email"), dict) else {}
        avisado = aviso.get("status") == "sent" or bool(aviso.get("sent_at"))
        tracking = _s(g.get("tracking")) or _s(o.tracking_number)
        partes = [f"Genei: «{g.get('state_label') or g.get('state_bucket')}»"]
        if g.get("courier"):
            partes.append(str(g["courier"]))
        if tracking:
            partes.append(f"tracking {tracking}")
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id, etiqueta=_etiqueta(o),
            detalle=(", ".join(partes) + " — y BoHub lo tiene «sin enviar»"
                     + (" aunque el cliente ya recibió el aviso de envío." if avisado else ".")),
            pista_de_arreglo="«Actualizar estado» en el envío Genei de la ficha lo pasa a "
                             "«etiqueta creada». Si no se mueve, el pedido no está embalado.",
            enlace=enlace_pedido(o.id), arreglo_enlace=enlace_pedido(o.id),
            arreglo_boton="Abrir el pedido",
            huella_datos={"estado_genei": g.get("state_code"), "avisado": avisado},
            datos={"shipment_code": g.get("shipment_code"), "tracking": tracking or None,
                   "avisado": avisado},
        )


def _con_etiqueta(ctx: Contexto) -> set[str]:
    """Pedidos con una etiqueta adjunta vigente (una lectura por pasada)."""
    from app.erp.models.shipping import KIND_ETIQUETA, ShipmentFile  # noqa: PLC0415

    return ctx.cached("con_etiqueta", lambda: set(ctx.session.scalars(
        select(ShipmentFile.order_id).where(
            ShipmentFile.kind == KIND_ETIQUETA, ShipmentFile.replaced_at.is_(None),
        ).distinct()
    )))


@comprobacion(
    id="envio_tramitado_sin_etiqueta", orden=16,
    titulo="Envío tramitado sin etiqueta adjunta",
    descripcion="Envío de Genei tramitado, esperando al transportista, sin la etiqueta "
                "adjunta pasadas N horas (la descarga automática no lo consiguió).",
    severidad="media", fuente=FUENTE_MYSQL, grupo="envios",
    dias_defecto=2, dias_texto="Avisar pasadas N horas desde que se tramitó",
)
def envio_tramitado_sin_etiqueta(ctx: Contexto) -> Iterator[Hallazgo]:
    from datetime import timedelta  # noqa: PLC0415

    horas = ctx.dias("envio_tramitado_sin_etiqueta", 2)
    limite = ctx.ahora - timedelta(hours=horas)
    con_etiqueta = _con_etiqueta(ctx)
    for o in ctx.pedidos():
        if o.cancelled_at is not None or o.id in con_etiqueta:
            continue
        if _v(o.transport_status) not in ("not_shipped", "label_created"):
            continue
        g = _genei(o)
        if not g.get("shipment_code") or g.get("state_bucket") != "ready":
            continue
        auto = g.get("label_auto") if isinstance(g.get("label_auto"), dict) else {}
        # Desde cuándo está tramitado: el pago en BoHub; si se pagó en la web de
        # Genei, cuando BoHub lo vio tramitado (empezó a pedir la etiqueta); y
        # si no, la creación del envío.
        desde = (_cuando(g.get("paid_at")) or _cuando(auto.get("scheduled_at"))
                 or _cuando(g.get("created_at")))
        if desde is None or desde > limite:
            continue
        estado = auto.get("status")
        if estado == "agotada":
            motivo = (f"la descarga automática se rindió tras {auto.get('attempts')} intentos"
                      + (f" ({auto['last_error']})" if auto.get("last_error") else ""))
        elif estado == "esperando":
            motivo = f"la descarga automática sigue reintentando (intento {auto.get('attempts')})"
        else:
            motivo = "nadie la ha traído de Genei"
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id, etiqueta=_etiqueta(o),
            detalle=f"Tramitado en Genei (desde el {_fecha(desde)}) y sin etiqueta adjunta: "
                    f"{motivo}.",
            pista_de_arreglo="«Traer etiqueta de Genei» en la Cola SAT (o «Imprimir etiqueta» "
                             "en la ficha) la deja adjunta.",
            enlace=enlace_pedido(o.id), arreglo_enlace=SAT_PENDIENTE_RECOGIDA,
            arreglo_boton="Ir a «Pendiente de recogida»",
            huella_datos={"shipment_code": g.get("shipment_code"), "estado": estado},
            datos={"desde": desde.isoformat()},
        )
