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
READY = "ready"             # 1: tramitado, etiqueta disponible
IN_TRANSIT = "in_transit"    # recogido / en reparto / en oficina…
DELIVERED = "delivered"      # entregado
INCIDENT = "incident"        # incidencia (fallida, devuelto, siniestro…)
CLOSED = "closed"           # cerrado / destruido / abandonado
OTHER = "other"             # rectificativo / abono… (informativo)


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
    2: GeneiState(2, "Pendiente de depositar en oficina de recogida", IN_TRANSIT),
    13: GeneiState(13, "Concertado próximo reparto", IN_TRANSIT),
    86: GeneiState(86, "En el centro logístico", IN_TRANSIT),
    3: GeneiState(3, "Paquete entregado", DELIVERED),
    9: GeneiState(9, "Recogida fallida", INCIDENT),
    10: GeneiState(10, "En tránsito con incidencia", INCIDENT),
    14: GeneiState(14, "Envío devuelto", INCIDENT),
    15: GeneiState(15, "Gestionando siniestro", INCIDENT),
    78: GeneiState(78, "Incidencia gestionada", INCIDENT),
    8: GeneiState(8, "Envío rectificativo", OTHER),
    11: GeneiState(11, "Pendiente de abono", OTHER),
    12: GeneiState(12, "Envío abonado", OTHER),
    77: GeneiState(77, "Envío cerrado", CLOSED),
    79: GeneiState(79, "Destruido / abandonado", CLOSED),
}


def state_of(code: int | str | None) -> GeneiState:
    """`GeneiState` del código (int o str). Desconocido → `OTHER` con el código."""
    try:
        num = int(str(code).strip())
    except (TypeError, ValueError):
        return GeneiState(-1, "Estado desconocido", OTHER)
    return GENEI_STATES.get(num, GeneiState(num, f"Estado {num}", OTHER))


#: Bucket → estado de transporte de BoHub al que debe llevar (o None = no mover
#: el estado; solo informar). El webhook de PR-2 lo usará para transicionar.
BUCKET_TO_TRANSPORT: dict[str, str | None] = {
    CREATED: None,
    PROCESSING: None,
    READY: None,          # la etiqueta ya mueve a label_created
    IN_TRANSIT: "in_transit",
    DELIVERED: "delivered",
    INCIDENT: "incident",
    CLOSED: None,
    OTHER: None,
}


def transport_status_for(bucket: str) -> str | None:
    return BUCKET_TO_TRANSPORT.get(bucket)


#: Buckets con el envío YA TRAMITADO (Genei estado 1 o posterior): la etiqueta
#: existe y se puede descargar. Antes (7 «pendiente de pago», 6 «pendiente de
#: tramitar») Genei responde 400 a la etiqueta; cerrado/destruido, tampoco vale.
TRAMITADO_BUCKETS: frozenset[str] = frozenset({READY, IN_TRANSIT, DELIVERED, INCIDENT, OTHER})


def is_tramitado(bucket: str | None) -> bool:
    """¿El envío Genei está tramitado (estado 1+)? Solo entonces hay etiqueta."""
    return (bucket or "") in TRAMITADO_BUCKETS
