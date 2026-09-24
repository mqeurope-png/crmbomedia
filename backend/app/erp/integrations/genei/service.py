"""Servicio Genei: arma el envío desde un pedido de BoHub y normaliza lo que
Genei devuelve, sin tocar red (el cliente HTTP se inyecta).

- `build_destination(fields)`: los datos de entrega del pedido → bloque
  `destination` de Genei (tolerante a lo que falte).
- `build_shipment_payload(...)`: el body de `POST /shipments` (agencyId,
  origin/destination, packagesArray, `externalShippingCode`=nº pedido,
  `notificationUrl`=webhook).
- `summarize_shipment(raw)`: la respuesta de creación/consulta → resumen
  normalizado (código, estado+bucket, tracking, courier, paymentUrl).
- Estado guardado en `order.packing_json["genei"]` (sin migración): código de
  envío, estado, tracking, courier, url de pago (para el botón de PR-2)…
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.erp.factusol_albaran import packing_of, save_packing
from app.erp.integrations.genei.status import state_of
from app.erp.models.orders import Order
from app.integrations.country_codes import normalize_country

#: Bloque de `packing_json` donde vive el estado del envío Genei.
PACKING_KEY = "genei"

_STATE_KEYS = ("estado", "status", "state", "id_estado", "codigo_estado")
_TRACKING_KEYS = ("codigo_seguimiento", "tracking", "trackingNumber", "tracking_number")
_COURIER_KEYS = ("nombre_agencia", "courier", "agency", "agencia", "carrier")
# Al CREAR, Genei devuelve el código en `data.reference`; al leer/listar, en
# `codigo_envio` (verificado en vivo).
_CODE_KEYS = ("reference", "shipmentCode", "shipment_code", "codigo_envio", "code")
_PAYMENT_KEYS = ("paymentUrl", "payment_url", "url_pago", "urlPago")
#: Id de la transacción de pago (al crear, en `data.transactionId`). Se guarda
#: en el pedido para poder pagar por API con un token fresco (PR-2).
_TRANSACTION_KEYS = ("transactionId", "transaction_id", "id_transaccion", "idTransaccion")


def _pick(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for scope in (data, data.get("data") if isinstance(data.get("data"), dict) else None):
        if not isinstance(scope, dict):
            continue
        for key in keys:
            if key in scope and scope[key] not in (None, ""):
                return scope[key]
    return None


def _s(value: Any) -> str:
    return str(value).strip() if value is not None else ""


# --- estado en packing_json -------------------------------------------------


def genei_state_of(order: Order) -> dict[str, Any]:
    """Bloque `packing_json.genei` del pedido (vacío si no hay envío)."""
    block = packing_of(order).get(PACKING_KEY)
    return block if isinstance(block, dict) else {}


def set_genei_state(order: Order, patch: dict[str, Any]) -> dict[str, Any]:
    """Fusiona `patch` en `packing_json.genei` y lo persiste en el pedido."""
    data = packing_of(order)
    block = data.get(PACKING_KEY)
    block = dict(block) if isinstance(block, dict) else {}
    block.update({k: v for k, v in patch.items() if v is not None})
    data[PACKING_KEY] = block
    save_packing(order, data)
    return block


def clear_genei_state(order: Order) -> None:
    """Quita el bloque Genei (al eliminar/cancelar el envío)."""
    data = packing_of(order)
    if PACKING_KEY in data:
        data.pop(PACKING_KEY, None)
        save_packing(order, data)


def shipment_code_of_order(order: Order) -> str | None:
    return genei_state_of(order).get("shipment_code") or None


# --- destino / payload ------------------------------------------------------


def build_destination(fields: dict[str, Any]) -> dict[str, Any]:
    """Datos de entrega del pedido → bloque `destination` de Genei.

    `fields` lo resuelve el endpoint (dirección de envío del pedido + contacto/
    empresa para teléfono, email y NIF). Se rellena lo que haya; los vacíos van
    como cadena vacía (Genei valida al crear y su error se propaga con contexto).
    """
    raw_country = _s(fields.get("iso_country") or fields.get("country"))
    iso2, _name = normalize_country(raw_country)
    return {
        "name": _s(fields.get("name")),
        "contact": _s(fields.get("contact") or fields.get("name")),
        "dni": _s(fields.get("dni") or fields.get("nif")),
        "email": _s(fields.get("email")),
        "phone": _s(fields.get("phone")),
        "address": _s(fields.get("address")),
        "postalCode": _s(fields.get("postal_code")),
        "town": _s(fields.get("town") or fields.get("city")),
        # País a ISO2 de verdad («France»→FR, «España»→ES); si no se reconoce,
        # se deja lo que venía (Genei lo rechazará y el error se verá).
        "isoCountry": (iso2 or raw_country.upper()[:2]),
        "observations": _s(fields.get("observations")),
    }


def address_missing_fields(addr: dict[str, Any]) -> list[str]:
    """Campos mínimos que faltan en una dirección (origen o destino) para poder
    enviar: nombre, dirección, CP, población, país. Lista vacía = completa."""
    required = {
        "name": "nombre", "address": "dirección", "postalCode": "código postal",
        "town": "población", "isoCountry": "país",
    }
    return [label for key, label in required.items() if not _s(addr.get(key))]


def destination_is_complete(dest: dict[str, Any]) -> list[str]:
    """Campos mínimos que faltan en `destination` (alias de más contexto)."""
    return address_missing_fields(dest)


#: Forma de pago del envío. Doc de Genei: «Normally 4 (payment with balance)»
#: (pago contra el saldo de la cuenta). El pago en sí lo dispara una persona
#: (PR-2); esto solo declara la modalidad exigida al crear.
PAYMENT_METHOD_BALANCE = 4


def build_origin(raw: dict[str, Any]) -> dict[str, Any]:
    """Dirección registrada en Genei (`GET /addresses/{id}`) → bloque `origin`
    del envío (misma forma que `destination`). El remitente por defecto de la
    cuenta ya trae nombre, dirección, email y teléfono con prefijo (E.164).

    Genei EXIGE el bloque `origin` completo al crear (todos los campos del
    ejemplo son obligatorios, incluido el prefijo internacional del teléfono).
    """
    if not isinstance(raw, dict):
        raw = {}
    iso = _s(raw.get("country_code") or raw.get("country_code_unico"))
    if not iso:
        iso2, _name = normalize_country(_s(raw.get("nombre_pais")))
        iso = iso2
    name = _s(raw.get("nombre"))
    # El teléfono DEBE llevar prefijo internacional; Genei lo da ya en E.164.
    phone = _s(raw.get("telefono_e164"))
    if not phone:
        prefix = _s(raw.get("prefijo_telefonico"))
        national = _s(raw.get("telefono"))
        phone = f"+{prefix}{national}" if prefix and national else national
    return {
        "name": name,
        "contact": _s(raw.get("contact")) or name,
        "dni": _s(raw.get("vat_number") or raw.get("dni")),
        "email": _s(raw.get("mail") or raw.get("email")),
        "phone": phone,
        "address": _s(raw.get("direccion") or raw.get("address")),
        "postalCode": _s(raw.get("codigo_postal") or raw.get("postalCode")),
        "town": _s(raw.get("poblacion") or raw.get("town")),
        "isoCountry": iso.upper(),
        "observations": _s(raw.get("observaciones") or raw.get("observations")),
    }


def build_shipment_payload(
    *,
    agency_id: str,
    origin: dict[str, Any],
    destination: dict[str, Any],
    packages: list[dict[str, Any]],
    external_shipping_code: str,
    notification_url: str | None,
    payment_method_shipping: int = PAYMENT_METHOD_BALANCE,
    shipping_from_warehouse: int = 0,
    client_reference: str | None = None,
    observations: str | None = None,
) -> dict[str, Any]:
    """Body de `POST /shipments`. Genei exige `origin` (bloque de dirección
    completo del remitente, resuelto desde el `originAddressId` de la cuenta),
    `destination` (el del pedido), `packagesArray` y `paymentMethodShipping`.
    `externalShippingCode` enlaza con el nº de pedido de BoHub."""
    payload: dict[str, Any] = {
        "agencyId": agency_id,
        "externalShippingCode": external_shipping_code,
        "origin": origin,
        "destination": destination,
        "packagesArray": packages,
        # Obligatorio (faltaba): forma de pago; 4 = pago con saldo.
        "paymentMethodShipping": payment_method_shipping,
        # Origen = dirección propia de BoHub, no el centro logístico de Genei.
        "shippingFromWarehouse": shipping_from_warehouse,
    }
    if notification_url:
        payload["notificationUrl"] = notification_url
    if client_reference:
        payload["clientReference"] = client_reference
    if observations:
        payload["note"] = observations
    return payload


# --- normalización de la respuesta ------------------------------------------


def summarize_shipment(raw: dict[str, Any]) -> dict[str, Any]:
    """Respuesta de creación/consulta de Genei → resumen normalizado para BoHub.

    No persiste nada; el endpoint decide qué guardar. Traduce el código de
    estado a su etiqueta y bucket (ver `status.py`)."""
    code = _pick(raw, _STATE_KEYS)
    st = state_of(code)
    return {
        "shipment_code": _s(_pick(raw, _CODE_KEYS)) or None,
        "state_code": st.code if st.code >= 0 else None,
        "state_bucket": st.bucket,
        "state_label": st.label,
        "tracking": _s(_pick(raw, _TRACKING_KEYS)) or None,
        "courier": _s(_pick(raw, _COURIER_KEYS)) or None,
        "payment_url": _s(_pick(raw, _PAYMENT_KEYS)) or None,
        "transaction_id": _s(_pick(raw, _TRANSACTION_KEYS)) or None,
    }


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
