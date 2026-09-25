"""Envíos con OTRO courier (no Genei): courier, enlace de seguimiento y estado.

Hay envíos que no se tramitan con Genei: se hacen con UPS, MRW, GLS, DSV…, se
sube la etiqueta a mano, se apunta el tracking y se pulsa «📤 Marcar recogido».
Aquí vive lo que BoHub sabe de ellos, en `packing_json.envio` (sin migración):

- `courier`: nombre del courier (lista habitual o texto libre si «Otro»);
- `customer_email`: estado del aviso de envío al cliente (ver `shipment_email`),
  igual que en el bloque de Genei;
- `created_by_user_id`: quién lo marcó recogido (autor del aviso automático).

El tracking sigue en `Order.tracking_number` (el mismo campo de siempre). Los
envíos de Genei NO usan esto: su courier y estado vienen de Genei (#490).

Enlaces de seguimiento por courier: los de los conectores oficiales de OCA
(Odoo) para CTT Express, MRW y Correos Express, y los públicos de UPS, FedEx,
DHL y TNT. Sin URL conocida (GLS, DSV, Seitrans, DB Schenker, MBE, «Otro») →
el número sin enlace.
"""
from __future__ import annotations

import re
from typing import Any

#: Couriers habituales (desplegable de la Cola SAT y de la ficha).
COURIERS: tuple[str, ...] = (
    "UPS", "CTT Express", "MRW", "GLS", "DSV", "FedEx", "DHL", "Correos Express",
    "Seitrans", "TNT", "DB Schenker", "MBE",
)
#: Texto cuando el envío externo no tiene courier informado.
OTHER_COURIER_LABEL = "otro courier"

#: Web de seguimiento por courier (`{t}` = nº de seguimiento).
TRACKING_URLS: dict[str, str] = {
    "UPS": "https://www.ups.com/track?tracknum={t}",
    "CTT Express": "https://app.cttexpress.com/AreaClientes/Views/Destinatarios.aspx?s={t}",
    "MRW": ("https://www.mrw.es/seguimiento_envios/MRW_resultados_consultas.asp"
            "?modo=nacional&envio={t}"),
    "Correos Express": "https://s.correosexpress.com/c?n={t}",
    "FedEx": "https://www.fedex.com/fedextrack/?trknbr={t}",
    "DHL": "https://www.dhl.com/es-es/home/tracking/tracking-express.html?submit=1&tracking-id={t}",
    "TNT": ("https://www.tnt.com/express/es_es/site/herramientas-envio/seguimiento.html"
            "?searchType=con&cons={t}"),
}

#: Alias → nombre canónico (lo que escribe la gente o trae Genei).
_ALIASES: dict[str, str] = {
    "ups": "UPS",
    "ctt": "CTT Express", "ctt express": "CTT Express", "ctt premium": "CTT Express",
    "cttexpress": "CTT Express", "ctt exp": "CTT Express",
    "mrw": "MRW",
    "gls": "GLS", "asm": "GLS", "gls spain": "GLS",
    "dsv": "DSV",
    "fedex": "FedEx", "fed ex": "FedEx",
    "dhl": "DHL", "dhl express": "DHL", "dhl parcel": "DHL",
    "correos express": "Correos Express", "correosexpress": "Correos Express",
    "cex": "Correos Express",
    "seitrans": "Seitrans",
    "tnt": "TNT",
    "db schenker": "DB Schenker", "schenker": "DB Schenker", "dbschenker": "DB Schenker",
    "mbe": "MBE", "mail boxes etc": "MBE", "mail boxes": "MBE",
}

#: Formatos de tracking que se reconocen con SEGURIDAD → courier sugerido.
_TRACKING_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^1Z[0-9A-Z]{16}$"), "UPS"),
    (re.compile(r"^0033\d{18}$"), "CTT Express"),
)

EXTERNAL_KEY = "envio"


def normalize_courier(value: Any) -> str | None:
    """Nombre canónico si es uno conocido (UPS, «ctt premium» → CTT Express…);
    si no, el texto tal cual (recortado). Vacío → None."""
    text = str(value or "").strip()
    if not text:
        return None
    key = re.sub(r"\s+", " ", text).lower()
    if key in _ALIASES:
        return _ALIASES[key]
    for name in COURIERS:
        if key == name.lower():
            return name
    return text[:60]


def suggest_courier(tracking: Any) -> str | None:
    """Courier por el formato del nº de seguimiento (solo los seguros:
    `1Z…` → UPS; `0033` + 18 dígitos → CTT Express)."""
    t = re.sub(r"\s+", "", str(tracking or "")).upper()
    for pattern, courier in _TRACKING_PATTERNS:
        if pattern.match(t):
            return courier
    return None


def tracking_url_for(courier: Any, tracking: Any) -> str | None:
    """Web de seguimiento del courier con ese número, o None si no se conoce."""
    t = re.sub(r"\s+", "", str(tracking or ""))
    name = normalize_courier(courier)
    if not t or not name or name not in TRACKING_URLS:
        return None
    from urllib.parse import quote  # noqa: PLC0415

    return TRACKING_URLS[name].format(t=quote(t, safe=""))


# --- el bloque del envío externo ------------------------------------------------


def external_state(order: Any) -> dict[str, Any]:
    from app.erp.factusol_albaran import packing_of  # noqa: PLC0415

    block = packing_of(order).get(EXTERNAL_KEY)
    return block if isinstance(block, dict) else {}


def set_external_state(order: Any, patch: dict[str, Any]) -> dict[str, Any]:
    """Fusiona `patch` en `packing_json.envio` (un valor None BORRA la clave)."""
    from app.erp.factusol_albaran import packing_of, save_packing  # noqa: PLC0415

    data = packing_of(order)
    block = dict(data.get(EXTERNAL_KEY) or {})
    for key, value in patch.items():
        if value is None:
            block.pop(key, None)
        else:
            block[key] = value
    data[EXTERNAL_KEY] = block
    save_packing(order, data)
    return block


def is_genei_shipment(order: Any) -> bool:
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415

    return bool(genei_state_of(order).get("shipment_code"))


def _transport(order: Any) -> str:
    return str(getattr(order.transport_status, "value", order.transport_status) or "")


#: Transporte con el paquete ya fuera (marcado recogido o posterior).
SHIPPED_TRANSPORT = ("in_transit", "delivered", "already_shipped_externally")


def is_external_shipment(order: Any) -> bool:
    """¿Envío con OTRO courier ya recogido? (sin envío de Genei)."""
    return not is_genei_shipment(order) and _transport(order) in SHIPPED_TRANSPORT


def shipment_courier(order: Any) -> str | None:
    """Courier del envío: el de Genei (su agencia) o el apuntado a mano."""
    if is_genei_shipment(order):
        from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415

        return (str(genei_state_of(order).get("courier") or "").strip() or None)
    return external_state(order).get("courier") or None


def shipment_tracking_url(order: Any) -> str | None:
    """Enlace de seguimiento: el de Genei o el de la tabla por courier."""
    if is_genei_shipment(order):
        from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415

        return genei_state_of(order).get("tracking_url") or None
    return tracking_url_for(external_state(order).get("courier"), order.tracking_number)


def external_envio_label(order: Any) -> str:
    """«Enviado · UPS» / «Enviado · otro courier» (envío externo recogido)."""
    courier = external_state(order).get("courier")
    return f"Enviado · {courier or OTHER_COURIER_LABEL}"


def external_envio_vocabulary() -> list[str]:
    """Los valores «Enviado · X» de los couriers habituales (lista de la hoja)."""
    return [f"Enviado · {c}" for c in (*COURIERS, OTHER_COURIER_LABEL)]


def picked_up_at(order: Any) -> str | None:
    """Cuándo se marcó recogido (primera transición a «en tránsito»), ISO."""
    fechas = [
        h.changed_at for h in (getattr(order, "status_history", None) or [])
        if str(getattr(h.domain, "value", h.domain)) == "transport"
        and h.to_status == "in_transit" and h.changed_at is not None
    ]
    return min(fechas).isoformat() if fechas else None


def shipment_info(session: Any, order: Any) -> dict[str, Any]:
    """Resumen del envío para la ficha y las listas, sea de Genei o de otro
    courier: tipo, courier, tracking (y su enlace), cuándo se recogió, el aviso
    al cliente y la sugerencia de courier por el formato del tracking."""
    _ = session
    genei = is_genei_shipment(order)
    tracking = str(order.tracking_number or "").strip() or None
    if genei:
        from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415

        tracking = tracking or (genei_state_of(order).get("tracking") or None)
        ce = genei_state_of(order).get("customer_email")
    else:
        ce = external_state(order).get("customer_email")
    transport = _transport(order)
    if genei:
        kind = "genei"
    elif transport in SHIPPED_TRANSPORT or external_state(order).get("courier"):
        kind = "externo"
    else:
        kind = None
    return {
        "kind": kind,
        "courier": shipment_courier(order),
        "tracking": tracking,
        "tracking_url": shipment_tracking_url(order),
        "transport_status": transport,
        "picked_up_at": picked_up_at(order),
        "customer_email": ce or None,
        "suggested_courier": suggest_courier(tracking),
        "couriers": list(COURIERS),
    }
