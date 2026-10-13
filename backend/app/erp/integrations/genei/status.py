"""Códigos de estado del envío Genei → significado en BoHub.

Se usa en el «Actualizar estado» manual (PR-1, tras `GET /shipments/{code}`) y
en el webhook de estados (PR-2). El *bucket* agrupa los códigos en el lenguaje
del pedido de BoHub para pintar la ficha/Seguimiento y decidir la transición de
transporte.
"""
from __future__ import annotations

from dataclasses import dataclass

# Buckets (lenguaje BoHub).
CREATED = "created"          # 7: recién creado, pendiente de pagar
PROCESSING = "processing"    # 6: pagado, tramitándose
READY = "ready"             # 1 / 2: tramitado, etiqueta disponible, sin recoger
IN_TRANSIT = "in_transit"    # recogido / en reparto / en oficina…
DELIVERED = "delivered"      # entregado
INCIDENT = "incident"        # incidencia (fallida, devuelto, siniestro…)
CLOSED = "closed"           # cerrado / destruido / abandonado
# 11 «pendiente de abono» / 12 «abonado»: ANULACIÓN con devolución del importe.
# El envío ya NO va a salir (verificado en vivo el 13/10/2026: dos envíos de DHL
# cayeron 7→6→1→11 solos y se quedaron con etiqueta y tracking vivos pero sin
# recoger jamás). No es entregable: el pedido suelta el vínculo y vuelve a «sin
# enviar» para crear/vincular otro (ver `webhook.apply_shipment_state`).
CANCELLED = "cancelled"      # 11 / 12: anulado con devolución; el envío no saldrá
OTHER = "other"             # rectificativo (8): informativo, sin etiqueta
# Código que la tabla NO contempla: NO se traga en silencio. Se marca el envío
# como «estado desconocido» (rastro en la ficha + aviso en Cuadre), nunca como
# si nada hubiera pasado (fue justo lo que falló con el 11).
UNKNOWN_STATE = "unknown"


@dataclass(frozen=True)
class GeneiState:
    code: int
    label: str
    bucket: str


#: Tabla oficial (genei-api-doc.md, rev 2025-11-10).
GENEI_STATES: dict[int, GeneiState] = {
    7: GeneiState(7, "Recogida pendiente de pago", CREATED),
    6: GeneiState(6, "Pendiente de tramitar", PROCESSING),
    1: GeneiState(1, "Tramitado", READY),
    5: GeneiState(5, "Recogida efectuada / en tránsito", IN_TRANSIT),
    80: GeneiState(80, "En reparto", IN_TRANSIT),
    85: GeneiState(85, "Disponible en oficina", IN_TRANSIT),
    # 2 = tramitado, pero el paquete aún hay que DEPOSITARLO en la oficina: el
    # transportista no lo tiene. NO es «en tránsito» (antes lo era, y el pedido
    # salía como recogido sin haber salido): es como 1, esperando al
    # transportista (pestaña «Pendiente de recogida»; la etiqueta ya existe).
    2: GeneiState(2, "Pendiente de depositar en oficina de recogida", READY),
    13: GeneiState(13, "Concertado próximo reparto", IN_TRANSIT),
    86: GeneiState(86, "En el centro logístico", IN_TRANSIT),
    3: GeneiState(3, "Paquete entregado", DELIVERED),
    9: GeneiState(9, "Recogida fallida", INCIDENT),
    10: GeneiState(10, "En tránsito con incidencia", INCIDENT),
    14: GeneiState(14, "Envío devuelto", INCIDENT),
    15: GeneiState(15, "Gestionando siniestro", INCIDENT),
    78: GeneiState(78, "Incidencia gestionada", INCIDENT),
    # 8 rectificativo: Genei ha emitido un envío corregido que sustituye a este.
    # Informativo (no lo tratamos como etiqueta viva ni como anulado): se enseña
    # y ya. No aparece ninguno en el listado actual; se decide OTHER por ser un
    # estado de información, no un paso del transporte.
    8: GeneiState(8, "Envío rectificativo", OTHER),
    # 11/12: anulación con devolución (ver bucket CANCELLED).
    11: GeneiState(11, "Pendiente de abono", CANCELLED),
    12: GeneiState(12, "Envío abonado", CANCELLED),
    77: GeneiState(77, "Envío cerrado", CLOSED),
    79: GeneiState(79, "Destruido / abandonado", CLOSED),
}


def state_of(code: int | str | None) -> GeneiState:
    """`GeneiState` del código (int o str). Un código que la tabla NO contempla
    cae en el bucket `UNKNOWN_STATE` (no en OTHER): así no se trata como un
    estado conocido y deja rastro de «estado desconocido» para avisar."""
    try:
        num = int(str(code).strip())
    except (TypeError, ValueError):
        return GeneiState(-1, "Estado desconocido", UNKNOWN_STATE)
    return GENEI_STATES.get(num, GeneiState(num, f"Estado desconocido ({num})", UNKNOWN_STATE))


#: Bucket → estado de transporte de BoHub al que debe llevar (o None = no mover
#: el estado; solo informar). Lo aplica `tracking.transport_target` (webhook,
#: «Actualizar estado», pagar y el sondeo).
BUCKET_TO_TRANSPORT: dict[str, str | None] = {
    CREATED: None,
    PROCESSING: None,
    # Tramitado: el envío existe y tiene etiqueta en Genei → «etiqueta creada»
    # por sí solo, sin esperar a que alguien descargue el PDF (07/10/2026: un
    # envío tramitado, con tracking y aviso al cliente, seguía «sin enviar»).
    READY: "label_created",
    IN_TRANSIT: "in_transit",
    DELIVERED: "delivered",
    INCIDENT: "incident",
    CLOSED: None,
    # Anulado: no mueve el transporte por los arcos normales. El desenlace
    # (soltar el vínculo y volver a «sin enviar») lo hace `apply_shipment_state`.
    CANCELLED: None,
    OTHER: None,
    UNKNOWN_STATE: None,
}


def transport_status_for(bucket: str) -> str | None:
    return BUCKET_TO_TRANSPORT.get(bucket)


#: Buckets con el envío YA TRAMITADO (Genei estado 1 o posterior): la etiqueta
#: existe y se puede descargar. Antes (7 «pendiente de pago», 6 «pendiente de
#: tramitar») Genei responde 400 a la etiqueta; cerrado/destruido, tampoco vale.
#: Anulado (11/12), rectificativo (8) y desconocido NO cuentan como tramitados:
#: no hay etiqueta viva que ofrecer (el 11 la tenía, pero el envío no saldrá).
TRAMITADO_BUCKETS: frozenset[str] = frozenset({READY, IN_TRANSIT, DELIVERED, INCIDENT})


#: Buckets de una ANULACIÓN con devolución (11/12): el envío no saldrá. BoHub
#: suelta el vínculo y deja el pedido listo para crear/vincular otro.
CANCELLED_BUCKETS: frozenset[str] = frozenset({CANCELLED})


#: Buckets con el envío tramitado y SIN incidencia: un pedido aún «sin enviar»
#: pasa solo a «etiqueta creada» (en tránsito / entregado de Genei no lo llevan
#: más allá: eso es «📤 Marcar recogido»).
LABEL_CREATED_BUCKETS: frozenset[str] = frozenset({READY, IN_TRANSIT, DELIVERED})


def is_tramitado(bucket: str | None) -> bool:
    """¿El envío Genei está tramitado (estado 1+)? Solo entonces hay etiqueta."""
    return (bucket or "") in TRAMITADO_BUCKETS


def is_cancelled(bucket: str | None) -> bool:
    """¿El envío está anulado con devolución (Genei 11/12)? No saldrá."""
    return (bucket or "") in CANCELLED_BUCKETS


def is_unknown_state(bucket: str | None) -> bool:
    """¿El estado de Genei no está contemplado en la tabla? Se marca y se avisa."""
    return (bucket or "") == UNKNOWN_STATE
