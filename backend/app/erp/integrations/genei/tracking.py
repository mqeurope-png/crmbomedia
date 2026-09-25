"""Tracking DETALLADO del transportista, leído de Genei.

`GET /api/v2/shipments/{code}/tracking` (Swagger oficial de Genei v2) devuelve,
por envío:

- `data.estadosAgencia[]` — los EVENTOS DEL TRANSPORTISTA (CTT, UPS, GLS…)
  tal cual: `{fecha, codigo_estado, descripcion}` («Pendiente de entrada en
  red», «En reparto», «Entregado»…). Pueden venir DESORDENADOS: se ordenan por
  fecha.
- `data.estadosInternos[]` — los estados de Genei (7 → 6 → 1 → 5 → 80 → 3).
- `message` — la URL de seguimiento web de la agencia.

El webhook de Genei (`notificationUrl`) solo manda el objeto del envío (su
estado grueso, sin estos eventos): el detalle se CONSULTA (al «Actualizar
estado», al llegar el webhook y en el sondeo del `worker-sync`).

Aquí, sin red: normalizar los eventos y CLASIFICAR el último (su texto es el
del transportista) en un paso real del envío, para ENSEÑARLO (Enviados, ficha,
hoja). El escaneo NO mueve el pedido de pestaña: eso lo hace «📤 Marcar
recogido» (o una incidencia, ver `transport_target`).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.erp.integrations.genei.status import transport_status_for

# --- pasos reales del envío según el transportista ---------------------------

PRE_TRANSIT = "pre_transit"          # datos/etiqueta recibidos, SIN escaneo físico
PICKED_UP = "picked_up"              # recogido / admitido en origen
IN_TRANSIT = "in_transit"            # en la red: tránsito, clasificado, en ruta…
OUT_FOR_DELIVERY = "out_for_delivery"  # en reparto
AVAILABLE_PICKUP = "available_pickup"  # disponible en oficina / punto de recogida
DELIVERED = "delivered"              # entregado
INCIDENT = "incident"                # ausente, dirección incorrecta, devuelto…
UNKNOWN = "unknown"                  # texto que no sabemos leer (se enseña tal cual)

#: Etiqueta (castellano, vocabulario cerrado) de cada paso. Es lo que va a la
#: columna Envío de la hoja; en la app se enseña además el texto literal.
CARRIER_STEP_LABELS: dict[str, str] = {
    PRE_TRANSIT: "Pendiente de entrada en red",
    PICKED_UP: "Recogido",
    IN_TRANSIT: "En tránsito",
    OUT_FOR_DELIVERY: "En reparto",
    AVAILABLE_PICKUP: "Disponible en oficina",
    DELIVERED: "Entregado",
    INCIDENT: "Incidencia",
}

def _fold(text: str) -> str:
    """Minúsculas y sin acentos, para casar texto de cualquier agencia."""
    norm = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


# El ORDEN importa: «No entregado · ausente» es incidencia, no entregado;
# «Pendiente de entrada en red» es pre-tránsito aunque diga «entrada en red».
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (INCIDENT, re.compile(
        r"incidencia|ausente|rehusad|rechazad|no entregad|no se ha podido|"
        r"imposible entregar|entrega fallida|fallid|direccion (incorrecta|erronea|"
        r"incompleta|desconocida)|desconocido|devoluc|devuelt|retornad|siniestr|"
        r"extraviad|perdid|danad|roto|retenid|anulad|cancelad|"
        r"exception|failed|refused|undeliver|not delivered|returned|damaged|lost|"
        r"delivery attempt")),
    (DELIVERED, re.compile(r"\bentregad[oa]s?\b|\bdelivered\b")),
    (AVAILABLE_PICKUP, re.compile(
        r"disponible (para|en)|recogeran en|listo para recoger|en (el )?punto|"
        r"parcel ?shop|punto pack|available for (pick ?up|collection)|"
        r"ready for (pick ?up|collection)")),
    (OUT_FOR_DELIVERY, re.compile(
        r"\ben reparto\b|\breparto\b|out for delivery|on vehicle for delivery")),
    (PRE_TRANSIT, re.compile(
        r"pendiente de (entrada|admision|recogida|deposito|depositar|entrega en)|"
        r"prerregistr|pre-?registr|grabad[oa]|manifestad|informacion recibida|"
        r"datos recibidos|etiqueta (creada|generada|impresa)|documentad|"
        r"label created|information received|info received|shipment information|"
        r"order processed|awaiting (pick ?up|collection)")),
    (PICKED_UP, re.compile(
        r"recogid[oa]|admitid[oa]|admision|recepcionad|picked up|pickup scan|"
        r"entrada en red|origin scan|we have your package")),
    (IN_TRANSIT, re.compile(
        r"transito|en ruta|clasificad|delegacion|plataforma|centro|hub|"
        r"\ben agencia\b|unidad|recanalizad|entrega parcial|"
        r"llegad|salida|en camino|aduana|in transit|arrived|departed|customs|"
        r"on the way|processing at")),
)


def classify_carrier_text(text: str | None) -> str:
    """Paso real del envío según el texto de un evento del transportista."""
    folded = _fold(str(text or ""))
    if not folded.strip():
        return UNKNOWN
    for step, pattern in _RULES:
        if pattern.search(folded):
            return step
    return UNKNOWN


# --- eventos -----------------------------------------------------------------


@dataclass(frozen=True)
class CarrierEvent:
    """Un evento del transportista, normalizado."""

    fecha: str            # ISO 8601 (UTC) si se pudo leer; si no, tal cual
    codigo: str
    descripcion: str
    step: str
    _order: tuple[float, int]  # clave de orden (fecha, posición original)

    def as_dict(self) -> dict[str, str]:
        return {"fecha": self.fecha, "codigo": self.codigo,
                "descripcion": self.descripcion, "step": self.step}


_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S",
)


def _parse_date(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _txt(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def carrier_events(raw: Any) -> list[CarrierEvent]:
    """`estadosAgencia[]` → eventos normalizados, del más ANTIGUO al más
    reciente (Genei puede mandarlos desordenados). Tolerante al envoltorio y a
    campos con otro nombre; los eventos sin descripción ni código se ignoran."""
    data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    items = data.get("estadosAgencia") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[CarrierEvent] = []
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        desc = _txt(item.get("descripcion") or item.get("description") or item.get("estado"))
        code = _txt(item.get("codigo_estado") or item.get("codigo") or item.get("code"))
        if not desc and not code:
            continue
        when = _parse_date(item.get("fecha") or item.get("date"))
        fecha = when.astimezone(UTC).isoformat() if when else _txt(item.get("fecha"))
        stamp = when.timestamp() if when else float("-inf")
        out.append(CarrierEvent(
            fecha=fecha, codigo=code, descripcion=desc or code,
            step=classify_carrier_text(desc or code), _order=(stamp, pos),
        ))
    out.sort(key=lambda e: e._order)
    return out


def tracking_url_of(raw: Any) -> str | None:
    """URL de seguimiento web de la agencia (en `message` de `/tracking`)."""
    if not isinstance(raw, dict):
        return None
    for value in (raw.get("message"), (raw.get("data") or {}).get("webSeguimiento")
                  if isinstance(raw.get("data"), dict) else None):
        text = _txt(value)
        if text.startswith(("http://", "https://")):
            return text
    return None


#: Cuántos eventos se guardan en el pedido (los más recientes).
MAX_STORED_EVENTS = 40


def summarize_tracking(raw: Any) -> dict[str, Any]:
    """Respuesta de `/tracking` → lo que se guarda en `packing_json.genei`:
    el último evento del transportista (texto literal + fecha + paso), el
    historial y la URL. Sin eventos → `carrier_step` None (Genei aún no tiene
    escaneos de la agencia: se sigue el estado de Genei)."""
    events = carrier_events(raw)
    last = events[-1] if events else None
    return {
        "carrier_status": last.descripcion if last else None,
        "carrier_status_code": last.codigo if last else None,
        "carrier_status_at": last.fecha if last else None,
        "carrier_step": last.step if last else None,
        "carrier_events": [e.as_dict() for e in events[-MAX_STORED_EVENTS:]],
        "tracking_url": tracking_url_of(raw),
    }


# --- qué puede mover el transporte SOLO (sin persona) -------------------------


def transport_target(genei_bucket: str | None, current_transport: str | None) -> str | None:
    """Estado de transporte al que BoHub mueve el pedido SIN intervención.

    Las pestañas de la Cola SAT solo cambian solas con una INCIDENCIA; el paso
    de «Pendiente de recogida» a «Enviados» lo hace una persona con «📤 Marcar
    recogido». Por eso:

    - Incidencia de Genei → incidencia (el pedido va a «Incidencias»).
    - Entregado de Genei → entregado, pero SOLO si ya se marcó recogido
      (`in_transit`: ya está en «Enviados», no cambia de pestaña).
    - Todo lo demás (recogido / en tránsito / en reparto de Genei o del
      transportista) NO mueve nada: se enseña y ya.

    El escaneo del transportista (`carrier_step`) es solo INFORMATIVO."""
    genei = transport_status_for(genei_bucket or "")
    if genei == "incident":
        return "incident"
    if genei == "delivered" and current_transport == "in_transit":
        return "delivered"
    return None


def carrier_step_label(step: str | None) -> str | None:
    """Etiqueta cerrada del paso (para la hoja), o None."""
    return CARRIER_STEP_LABELS.get(step or "")
