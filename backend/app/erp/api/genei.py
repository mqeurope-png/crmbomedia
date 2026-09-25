"""ERP · Genei — envío desde la Cola SAT (PR-1: sin pago ni webhook).

Endpoints sobre el pedido: preparar (prefill), comparar agencias (`prices`),
crear el envío, traer la etiqueta (auto-adjunta), «Actualizar estado» (tracking
manual) y eliminar/cancelar. Más los ajustes del carrier «Genei» (credenciales
cifradas + config de couriers/bulto/origen).

El PAGO («Pagar y tramitar») y el WEBHOOK de estados son PR-2: aquí el envío se
crea (nace en estado 7, pendiente de pago) y se paga a mano en la web de Genei
hasta que llegue PR-2.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.api.deps import require_config, require_sat_shipping, require_sat_tracking
from app.erp.integrations.genei.client import GeneiClient, GeneiConfigError, GeneiError
from app.erp.integrations.genei.config import GeneiConfig, choose_agencies
from app.erp.integrations.genei.service import (
    address_missing_fields,
    build_destination,
    build_origin,
    build_shipment_payload,
    clear_genei_state,
    destination_is_complete,
    genei_state_of,
    now_iso,
    set_genei_state,
    shipment_code_of_order,
    summarize_shipment,
)
from app.erp.integrations.genei.status import is_tramitado, state_of
from app.erp.models.carriers import Carrier
from app.erp.models.orders import Order, PreparationStatus
from app.erp.models.shipping import KIND_ETIQUETA, SOURCE_GENEI_API, ShipmentPackage
from app.erp.shipping_destination import resolve_shipping_destination
from app.models.crm import AuditLog, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/erp", tags=["erp-genei"])

#: Lo que ve la persona si pide la etiqueta antes de tiempo.
LABEL_NOT_READY = "La etiqueta estará disponible tras pagar y tramitar el envío."

GENEI_CARRIER_CODE = "genei"
GENEI_ADAPTER = "app.erp.integrations.genei.client.GeneiClient"
DEFAULT_BASE_URL = "https://apiv2.genei.es"


# --- inyección del cliente (los tests la monkeypatchean) --------------------


def build_client(carrier: Carrier) -> GeneiClient:
    """Cliente HTTP de Genei para ese carrier. Aislado para poder sustituirlo
    en tests (MockTransport / fake)."""
    return GeneiClient.from_carrier(carrier)


# --- helpers ----------------------------------------------------------------


def _get_order(session: Session, order_id: str) -> Order:
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(404, {"code": "order_not_found", "detail": "Pedido no encontrado."})
    return order


def get_genei_carrier(session: Session) -> Carrier | None:
    return session.scalar(select(Carrier).where(Carrier.code == GENEI_CARRIER_CODE))


def _require_carrier(session: Session) -> Carrier:
    carrier = get_genei_carrier(session)
    if carrier is None or not carrier.api_credentials_encrypted:
        raise HTTPException(400, {
            "code": "genei_not_configured",
            "detail": "Genei no está configurado (falta credenciales en Ajustes).",
        })
    return carrier


def _client_or_400(carrier: Carrier) -> GeneiClient:
    try:
        return build_client(carrier)
    except GeneiConfigError as exc:
        raise HTTPException(400, {"code": "genei_not_configured", "detail": str(exc)}) from exc


#: «GET /shipments/X/label → 400» y parecidos: método + ruta + flecha. Es la
#: cabecera técnica de `GeneiError`; nunca debe llegar a la pantalla.
_PREFIJO_HTTP = re.compile(r"^\s*(GET|POST|PUT|PATCH|DELETE)\s+\S+\s*→\s*")
_CODIGO_Y_CUERPO = re.compile(r"^(\d{3})(?::\s*(.*))?$", re.S)
#: Mensaje legible por código HTTP de Genei (sin enseñar el código).
_MENSAJE_POR_CODIGO: tuple[tuple[range, str], ...] = (
    (range(401, 404), "Genei ha rechazado las credenciales: revísalas en Ajustes → Envíos"),
    (range(404, 405), "Genei no encuentra ese envío"),
    (range(409, 410), "Genei no lo permite en el estado actual del envío"),
    (range(429, 430), "Genei está recibiendo demasiadas peticiones: prueba en un momento"),
    (range(500, 600), "Genei no responde ahora mismo: prueba en un momento"),
    (range(400, 500), "Genei no ha aceptado la petición"),
)


def _mensaje_del_cuerpo(cuerpo: str) -> str:
    """El mensaje que Genei pone en el cuerpo del error (JSON `message`/`error`
    …), si es legible. Nunca HTML ni volcados largos."""
    texto = (cuerpo or "").strip()
    if not texto:
        return ""
    try:
        data = json.loads(texto)
    except ValueError:
        return "" if texto.startswith("<") or len(texto) > 200 else texto
    if isinstance(data, dict):
        for key in ("message", "error", "msg", "detail", "mensaje"):
            valor = data.get(key)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()[:200]
        errores = data.get("errors")
        if isinstance(errores, list) and errores:
            return "; ".join(str(e) for e in errores[:3])[:200]
    return ""


def genei_user_message(exc: GeneiError) -> str:
    """Texto de un fallo de Genei para la PERSONA: sin método, ruta ni código
    HTTP (el detalle técnico queda en el log). Conserva lo que dice Genei
    («Saldo insuficiente», «"origin" is mandatory»…)."""
    texto = _PREFIJO_HTTP.sub("", str(exc)).strip()
    m = _CODIGO_Y_CUERPO.match(texto)
    if not m:
        return texto.replace("respuesta no-JSON de Genei",
                             "Genei ha respondido algo inesperado")[:400]
    codigo = int(m.group(1))
    base = next((msg for rango, msg in _MENSAJE_POR_CODIGO if codigo in rango),
                "Genei no ha podido completar la petición")
    detalle = _mensaje_del_cuerpo(exc.body or m.group(2) or "")
    return f"{base}: {detalle}" if detalle else f"{base}."


def _genei_error(exc: GeneiError) -> HTTPException:
    """Traduce un fallo de Genei a un 502 con contexto claro (sin romper la
    Cola SAT): agencia no factible, credenciales, timeout… El texto es para la
    persona (sin «GET /… → 400»); el técnico va al log."""
    logger.warning("genei: %s", exc)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, {
        "code": "genei_error",
        "detail": genei_user_message(exc),
        "status": exc.status,
    })


def resolve_destination_fields(
    session: Session, order: Order, *, completar: bool = False,
) -> dict[str, Any]:
    """Datos de entrega prellenados del pedido, sea cual sea su origen (web,
    manual, muestra, factura, albarán, proforma): ver `shipping_destination`.
    Con `completar=True` se completa lo que falte leyendo FACTUSOL. El operario
    los revisa y edita antes de crear el envío."""
    campos, _origen = resolve_shipping_destination(session, order, completar=completar)
    return campos


def _config_of(carrier: Carrier | None) -> GeneiConfig:
    return GeneiConfig.from_json(carrier.config_json) if carrier else GeneiConfig()


def _order_packages(session: Session, order_id: str) -> list[dict[str, float]]:
    """Bultos REALES del pedido (medidos por el SAT al embalar, tabla
    `shipment_packages`) en formato de bulto de Genei. `depth_cm` → `length`.
    Lista vacía si el pedido aún no tiene bultos medidos."""
    rows = session.scalars(
        select(ShipmentPackage).where(ShipmentPackage.order_id == order_id)
        .order_by(ShipmentPackage.position.asc())
    )
    return [
        {"weight": float(p.weight_kg), "height": float(p.height_cm),
         "width": float(p.width_cm), "length": float(p.depth_cm)}
        for p in rows
    ]


def _is_packed(order: Order) -> bool:
    """El pedido está embalado (en «Listos»): requisito para crear el envío."""
    return order.preparation_status == PreparationStatus.PACKED.value


def _audit(session: Session, user: User, action: str, order_id: str | None, meta: dict) -> None:
    session.add(AuditLog(
        actor_user_id=getattr(user, "id", None),
        actor_email=getattr(user, "email", None),
        action=action, target_type="order", target_id=order_id,
        metadata_json=json.dumps(meta),
    ))


def _serialise_state(order: Order) -> dict[str, Any]:
    """Bloque Genei del pedido + si la etiqueta ya se puede descargar (envío
    tramitado, estado 1+). Antes de tramitar no se ofrece la etiqueta."""
    state = dict(genei_state_of(order))
    if state.get("shipment_code"):
        state["label_available"] = is_tramitado(state.get("state_bucket"))
    return state


# --- modelos de entrada -----------------------------------------------------


class PackageIn(BaseModel):
    weight: float = Field(gt=0)
    height: float = Field(gt=0)
    width: float = Field(gt=0)
    length: float = Field(gt=0)

    def as_dict(self) -> dict[str, float]:
        return {"weight": self.weight, "height": self.height,
                "width": self.width, "length": self.length}


class DestinationIn(BaseModel):
    name: str = ""
    contact: str = ""
    dni: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    postalCode: str = ""
    town: str = ""
    isoCountry: str = ""
    observations: str = ""


class PricesIn(BaseModel):
    destination: DestinationIn
    packages: list[PackageIn] = Field(default_factory=list)
    home_only: bool = True


class CreateShipmentIn(BaseModel):
    agency_id: str
    destination: DestinationIn
    packages: list[PackageIn] = Field(default_factory=list)
    observations: str | None = None


class GeneiConfigIn(BaseModel):
    username: str | None = None
    password: str | None = None
    base_url: str | None = None
    default_address_id: str | None = None
    preferred_couriers: dict[str, list[str]] | None = None
    default_package: PackageIn | None = None
    origin: dict[str, str] | None = None
    is_warehouse: bool | None = None
    #: Base pública del backend para el webhook de estados (PR-2). Al fijarla se
    #: genera el secreto (cifrado); el `notificationUrl` se envía al crear.
    webhook_base_url: str | None = None


# --- prefill / prices -------------------------------------------------------


@router.get("/orders/{order_id}/genei/prefill")
def genei_prefill(
    order_id: str,
    # Al ABRIR «Crear envío» (acción de la persona) se completa el destino
    # leyendo FACTUSOL si faltan dirección/teléfono/email (pedidos de factura,
    # albarán o proforma creados antes de guardar su bloque de entrega). Al
    # pintar la sección, no: así ver la ficha no sale a FACTUSOL.
    completar: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_shipping),
) -> dict[str, Any]:
    """Datos prellenados para crear el envío: destino (de la dirección de envío
    del pedido), bulto por defecto, origen y estado actual del envío si ya
    existe. `missing` avisa de los campos que el operario debe completar."""
    _ = current_user
    order = _get_order(session, order_id)
    carrier = get_genei_carrier(session)
    cfg = _config_of(carrier)
    campos, fuentes = resolve_shipping_destination(session, order, completar=completar)
    dest = build_destination(campos)
    # Bultos REALES medidos por el SAT al embalar; si no hay, el modal cae al
    # bulto por defecto de la config (editable).
    packages = _order_packages(session, order.id)
    return {
        "order_id": order.id,
        "configured": bool(carrier and carrier.api_credentials_encrypted),
        "destination": dest,
        # De dónde sale cada dato (pedido, destinatario, contacto, documento,
        # ficha, empresa…): para saber qué revisar si algo no cuadra.
        "destination_sources": fuentes,
        "missing": destination_is_complete(dest),
        "packages": packages,
        "default_package": cfg.default_package.as_package(),
        "preferred_couriers": cfg.preferred_for(dest.get("isoCountry")),
        "origin_address_id": carrier.default_address_id if carrier else None,
        # El envío solo se puede crear si el pedido está embalado («Listos»).
        "is_packed": _is_packed(order),
        "state": _serialise_state(order),
    }


@router.post("/orders/{order_id}/genei/prices")
def genei_prices(
    order_id: str,
    payload: PricesIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_shipping),
) -> dict[str, Any]:
    """Comparador: agencias factibles para el destino y los bultos dados.
    Propone por defecto el courier preferido factible más barato del país de
    destino (entrega a domicilio salvo que se pida oficina)."""
    _ = current_user
    order = _get_order(session, order_id)
    carrier = _require_carrier(session)
    cfg = _config_of(carrier)
    dest = payload.destination
    packages = [p.as_dict() for p in payload.packages] or [cfg.default_package.as_package()]

    client = _client_or_400(carrier)
    try:
        rows = client.agency_prices(
            is_warehouse=cfg.is_warehouse,
            iso_country_origin=cfg.origin.iso_country or "ES",
            iso_country_destination=dest.isoCountry,
            postal_code_origin=cfg.origin.postal_code or None,
            postal_code_destination=dest.postalCode or None,
            town_origin=cfg.origin.town or None,
            town_destination=dest.town or None,
            packages=packages,
        )
    except GeneiError as exc:
        raise _genei_error(exc) from exc

    preferred = cfg.preferred_for(dest.isoCountry)
    choice = choose_agencies(rows, preferred, home_only=payload.home_only)

    def opt(a) -> dict[str, Any]:
        return {"agency_id": a.agency_id, "name": a.name, "price": a.price,
                "home_delivery": a.is_home_delivery}

    return {
        "order_id": order.id,
        "default": opt(choice.default) if choice.default else None,
        "home_options": [opt(a) for a in choice.home_options],
        "all_options": [opt(a) for a in choice.all_options],
        "preferred_couriers": preferred,
    }


# --- crear / etiqueta / estado / eliminar -----------------------------------


@router.post("/orders/{order_id}/genei/shipments", status_code=201)
def genei_create_shipment(
    order_id: str,
    payload: CreateShipmentIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_shipping),
) -> dict[str, Any]:
    """Crea el envío en Genei (nace en estado 7, pendiente de pago). Enlaza con
    el nº de pedido (`externalShippingCode`) y guarda el `shipmentCode` + estado
    en el pedido. NO paga (eso es una acción humana, PR-2)."""
    order = _get_order(session, order_id)
    if shipment_code_of_order(order):
        raise HTTPException(409, {
            "code": "shipment_exists",
            "detail": "El pedido ya tiene un envío en Genei; elimínalo antes de crear otro.",
            "state": _serialise_state(order),
        })
    # Regla de negocio: no se crea el envío si el pedido no está EMBALADO. En la
    # Cola SAT solo pasa a «Listos» cuando el SAT ha medido/embalado los bultos.
    if not _is_packed(order):
        raise HTTPException(409, {
            "code": "not_packed",
            "detail": ("Empaqueta el pedido primero: solo se puede crear el "
                       "envío cuando está en «Listos»."),
        })
    carrier = _require_carrier(session)
    cfg = _config_of(carrier)
    dest = payload.destination.model_dump()
    missing = destination_is_complete(dest)
    if missing:
        raise HTTPException(422, {
            "code": "destination_incomplete",
            "detail": f"Faltan datos del destino: {', '.join(missing)}.",
        })
    # Genei exige el bloque `origin` completo (remitente). Se resuelve desde la
    # dirección registrada de la cuenta (`originAddressId` = default_address_id),
    # así no se duplica el dato ni se desincroniza.
    if not carrier.default_address_id:
        raise HTTPException(400, {
            "code": "origin_not_configured",
            "detail": "Falta la dirección de origen (remitente) en los Ajustes de Genei.",
        })
    packages = [p.as_dict() for p in payload.packages] or [cfg.default_package.as_package()]
    client = _client_or_400(carrier)
    try:
        origin = build_origin(client.get_address(carrier.default_address_id))
    except GeneiError as exc:
        raise _genei_error(exc) from exc
    origin_missing = address_missing_fields(origin)
    if origin_missing:
        raise HTTPException(422, {
            "code": "origin_incomplete",
            "detail": ("La dirección de origen de Genei está incompleta: "
                       f"{', '.join(origin_missing)}."),
        })
    body = build_shipment_payload(
        agency_id=payload.agency_id,
        origin=origin,
        destination=dest,
        packages=packages,
        external_shipping_code=order.order_number or order.id,
        # PR-2: webhook de estados. `notificationUrl` = base pública +
        # `?token=<secreto>` (secreto cifrado en las credenciales). Si falta la
        # base o el secreto, va None y el webhook queda apagado (el resto sirve).
        notification_url=cfg.webhook_url(
            GeneiClient.webhook_secret_of(carrier.api_credentials_encrypted)
        ),
        observations=payload.observations,
    )
    try:
        created = client.create_shipment(body)
    except GeneiError as exc:
        raise _genei_error(exc) from exc

    summary = summarize_shipment(created)
    if not summary["shipment_code"]:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "genei_no_code",
            "detail": "Genei no devolvió el código del envío.",
        })
    # La respuesta de creación trae `reference` + `paymentUrl` pero NO el estado:
    # el envío nace en 7 (pendiente de pago). Lo fijamos para que la ficha no
    # muestre «Estado desconocido» (el refresh/webhook lo actualizará luego).
    if summary["state_code"] is None:
        born = state_of(7)
        summary["state_code"] = born.code
        summary["state_bucket"] = born.bucket
        summary["state_label"] = born.label
    # El pedido queda ligado al carrier Genei.
    order.carrier_id = carrier.id
    set_genei_state(order, {
        "shipment_code": summary["shipment_code"],
        "agency_id": payload.agency_id,
        "courier": summary["courier"],
        "state_code": summary["state_code"],
        "state_bucket": summary["state_bucket"],
        "state_label": summary["state_label"],
        "payment_url": summary["payment_url"],
        # PR-2: id de la transacción, para pagar por API con token fresco.
        "transaction_id": summary["transaction_id"],
        "created_at": now_iso(),
    })
    _audit(session, current_user, "erp.genei.shipment_created", order.id, {
        "shipment_code": summary["shipment_code"], "agency_id": payload.agency_id,
    })
    session.commit()
    session.refresh(order)
    return {"order_id": order.id, "summary": summary, "state": _serialise_state(order)}


@router.post("/orders/{order_id}/genei/pay")
def genei_pay(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_shipping),
) -> dict[str, Any]:
    """«Pagar y tramitar»: paga el envío por API contra el SALDO de la cuenta
    (sin popup) y refresca el estado. El envío pasa 7 → 6 → 1 (tramitado) y la
    etiqueta queda disponible.

    MUEVE DINERO REAL: SIEMPRE lo dispara una persona con este botón, nunca
    automático. Si no hay saldo o Genei rechaza el pago, el error se propaga
    (502) con el mensaje de Genei — no falla en silencio."""
    order = _get_order(session, order_id)
    state = genei_state_of(order)
    code = shipment_code_of_order(order)
    if not code:
        raise HTTPException(409, {
            "code": "no_shipment", "detail": "El pedido no tiene envío en Genei; créalo primero.",
        })
    transaction_id = state.get("transaction_id")
    if not transaction_id:
        raise HTTPException(409, {
            "code": "no_transaction",
            "detail": ("Este envío no tiene transacción de pago (creado antes de PR-2). "
                       "Elimínalo y créalo de nuevo para poder pagar por API."),
        })
    carrier = _require_carrier(session)
    client = _client_or_400(carrier)
    # Paga (token fresco pg=4 + pay/transactions). Un rechazo (sin saldo, ya
    # pagado…) llega como GeneiError y se ve como 502 con su mensaje.
    try:
        client.pay_transaction(str(transaction_id))
    except GeneiError as exc:
        raise _genei_error(exc) from exc
    # Tras pagar, refresca el estado real del envío (7 → 6 → 1) y el tracking.
    try:
        raw = client.get_shipment(code)
    except GeneiError as exc:
        raise _genei_error(exc) from exc
    summary = summarize_shipment(raw)
    if summary["tracking"]:
        order.tracking_number = summary["tracking"]
    set_genei_state(order, {
        "state_code": summary["state_code"],
        "state_bucket": summary["state_bucket"],
        "state_label": summary["state_label"],
        "tracking": summary["tracking"],
        "courier": summary["courier"],
        "paid_at": now_iso(),
    })
    _audit(session, current_user, "erp.genei.shipment_paid", order.id, {"shipment_code": code})
    session.commit()
    session.refresh(order)
    return {"order_id": order.id, "summary": summary, "state": _serialise_state(order)}


@router.post("/orders/{order_id}/genei/label", status_code=201)
def genei_fetch_label(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_shipping),
) -> dict[str, Any]:
    """Trae la etiqueta de Genei y la deja adjunta en la Cola SAT (mismo hueco
    de etiqueta que se rellena a mano). Subir la etiqueta mueve el transporte a
    `label_created` (como el flujo manual)."""
    from app.erp.api.shipping import _store_new_file, _transition_on_etiqueta  # noqa: PLC0415

    order = _get_order(session, order_id)
    code = shipment_code_of_order(order)
    if not code:
        raise HTTPException(409, {
            "code": "no_shipment",
            "detail": "El pedido no tiene envío en Genei; créalo primero.",
        })
    carrier = _require_carrier(session)
    client = _client_or_400(carrier)
    if not is_tramitado(genei_state_of(order).get("state_bucket")):
        # El estado guardado puede ir atrasado (pagado en la web de Genei sin
        # que llegara el webhook): se consulta a Genei antes de decir que no.
        from app.erp.integrations.genei.webhook import apply_shipment_state  # noqa: PLC0415

        try:
            apply_shipment_state(session, order, client.get_shipment(code))
        except GeneiError as exc:
            logger.info("genei: no se pudo refrescar el estado antes de la etiqueta: %s", exc)
        if not is_tramitado(genei_state_of(order).get("state_bucket")):
            session.commit()
            raise HTTPException(409, {"code": "label_not_ready", "detail": LABEL_NOT_READY})
    try:
        label = client.get_label(code)
    except GeneiError as exc:
        if exc.status in (400, 404):
            # Genei aún no tiene la etiqueta: no es un fallo técnico.
            raise HTTPException(409, {"code": "label_not_ready",
                                      "detail": LABEL_NOT_READY}) from exc
        raise _genei_error(exc) from exc

    row = _store_new_file(
        session, order, kind=KIND_ETIQUETA, source=SOURCE_GENEI_API,
        filename=label.filename, mime_type=label.mime_type,
        data=label.content, actor_id=getattr(current_user, "id", None),
    )
    applied, reason = _transition_on_etiqueta(session, order, row, current_user)
    set_genei_state(order, {"label_fetched_at": now_iso()})
    _audit(session, current_user, "erp.genei.label_fetched", order.id, {
        "shipment_code": code, "filename": label.filename,
    })
    session.commit()
    session.refresh(row)
    from app.erp.api.shipping import _serialise_file  # noqa: PLC0415
    return {
        "order_id": order.id, "file": _serialise_file(row),
        "transition_applied": applied, "transition_reason": reason,
        "state": _serialise_state(order),
    }


@router.post("/orders/{order_id}/genei/refresh")
def genei_refresh(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_tracking),
) -> dict[str, Any]:
    """«Actualizar estado»: consulta el envío en Genei y refresca el estado, el
    tracking y el courier en el pedido. Es el RESPALDO MANUAL del webhook: usa
    la misma lógica (`apply_shipment_state`), así que también mueve el
    `transport_status` del pedido (recogido / entregado / incidencia)."""
    from app.erp.integrations.genei.webhook import apply_shipment_state  # noqa: PLC0415

    order = _get_order(session, order_id)
    code = shipment_code_of_order(order)
    if not code:
        raise HTTPException(409, {
            "code": "no_shipment", "detail": "El pedido no tiene envío en Genei.",
        })
    carrier = _require_carrier(session)
    client = _client_or_400(carrier)
    try:
        raw = client.get_shipment(code)
    except GeneiError as exc:
        raise _genei_error(exc) from exc

    summary, _applied = apply_shipment_state(session, order, raw)
    session.commit()
    session.refresh(order)
    return {"order_id": order.id, "summary": summary, "state": _serialise_state(order)}


@router.delete("/orders/{order_id}/genei/shipment")
def genei_delete_shipment(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_shipping),
) -> dict[str, Any]:
    """Elimina/cancela el envío en Genei (mientras no esté en tránsito) y limpia
    el estado del pedido."""
    order = _get_order(session, order_id)
    code = shipment_code_of_order(order)
    if not code:
        raise HTTPException(409, {
            "code": "no_shipment", "detail": "El pedido no tiene envío en Genei.",
        })
    carrier = _require_carrier(session)
    client = _client_or_400(carrier)
    try:
        client.delete_shipment(code)
    except GeneiError as exc:
        raise _genei_error(exc) from exc
    clear_genei_state(order)
    _audit(session, current_user, "erp.genei.shipment_deleted", order.id, {"shipment_code": code})
    session.commit()
    return {"order_id": order.id, "deleted": True}


# --- ajustes del carrier «Genei» --------------------------------------------


@router.get("/genei/config")
def genei_config_get(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Config de Genei (NUNCA devuelve la password ni el token, solo si hay
    credenciales guardadas)."""
    _ = current_user
    carrier = get_genei_carrier(session)
    cfg = _config_of(carrier)
    username = ""
    if carrier and carrier.api_credentials_encrypted:
        try:
            username = GeneiClient._decode_credentials(carrier.api_credentials_encrypted).get(
                "username", "")
        except GeneiConfigError:
            username = ""
    return {
        "configured": bool(carrier and carrier.api_credentials_encrypted),
        "username": username,
        "base_url": (carrier.api_base_url if carrier else None) or DEFAULT_BASE_URL,
        "default_address_id": carrier.default_address_id if carrier else None,
        "preferred_couriers": cfg.preferred_couriers,
        "default_package": cfg.default_package.as_package(),
        "origin": {"iso_country": cfg.origin.iso_country,
                   "postal_code": cfg.origin.postal_code, "town": cfg.origin.town},
        "is_warehouse": cfg.is_warehouse,
        # PR-2 webhook: la base pública (sin secreto) y si ya está operativo
        # (base + secreto + credenciales). El secreto NUNCA se devuelve.
        "webhook_base_url": cfg.webhook_base_url,
        "webhook_configured": bool(
            cfg.webhook_url(
                GeneiClient.webhook_secret_of(carrier.api_credentials_encrypted)
            ) if carrier else None
        ),
    }


@router.put("/genei/config")
def genei_config_put(
    payload: GeneiConfigIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Guarda credenciales (cifradas) y config de Genei. La password solo se
    escribe si viene; enviar vacío no la borra."""
    carrier = get_genei_carrier(session)
    if carrier is None:
        carrier = Carrier(
            name="Genei", code=GENEI_CARRIER_CODE, has_api=True,
            adapter_class=GENEI_ADAPTER, api_base_url=DEFAULT_BASE_URL,
        )
        session.add(carrier)

    if payload.base_url is not None:
        carrier.api_base_url = payload.base_url.strip() or DEFAULT_BASE_URL
    if not carrier.api_base_url:
        carrier.api_base_url = DEFAULT_BASE_URL
    if payload.default_address_id is not None:
        carrier.default_address_id = payload.default_address_id.strip() or None
    carrier.has_api = True
    carrier.adapter_class = GENEI_ADAPTER

    # Credenciales: se guardan cifradas solo si vienen ambas; si solo cambia el
    # email, se conserva la password ya guardada.
    if payload.username is not None or payload.password is not None:
        current = {}
        if carrier.api_credentials_encrypted:
            try:
                current = GeneiClient._decode_credentials(carrier.api_credentials_encrypted)
            except GeneiConfigError:
                current = {}
        username = (payload.username if payload.username is not None
                    else current.get("username", "")) or ""
        password = (payload.password if payload.password else current.get("password", "")) or ""
        if username and password:
            carrier.api_credentials_encrypted = GeneiClient.encode_credentials(username, password)

    cfg = _config_of(carrier)
    if payload.preferred_couriers is not None:
        cfg.preferred_couriers = payload.preferred_couriers
    if payload.default_package is not None:
        pkg = payload.default_package
        cfg.default_package.weight = pkg.weight
        cfg.default_package.height = pkg.height
        cfg.default_package.width = pkg.width
        cfg.default_package.length = pkg.length
    if payload.origin is not None:
        from app.erp.integrations.genei.config import OriginAddress  # noqa: PLC0415
        cfg.origin = OriginAddress.from_dict(payload.origin)
    if payload.is_warehouse is not None:
        cfg.is_warehouse = payload.is_warehouse
    # PR-2 webhook: la base pública va en config (no es secreta); el SECRETO se
    # genera al activar el webhook (base fijada + credenciales) y se guarda
    # CIFRADO con las credenciales, nunca en config_json en claro.
    if payload.webhook_base_url is not None:
        cfg.webhook_base_url = payload.webhook_base_url.strip()
    if cfg.webhook_base_url and carrier.api_credentials_encrypted:
        try:
            creds = GeneiClient._decode_credentials(carrier.api_credentials_encrypted)
        except GeneiConfigError:
            creds = {}
        if creds.get("username") and creds.get("password") and not creds.get("webhook_secret"):
            import secrets as _secrets  # noqa: PLC0415
            carrier.api_credentials_encrypted = GeneiClient.encode_credentials(
                creds["username"], creds["password"],
                webhook_secret=_secrets.token_urlsafe(24),
            )
    carrier.config_json = cfg.to_json()

    session.add(AuditLog(
        actor_user_id=getattr(current_user, "id", None),
        actor_email=getattr(current_user, "email", None),
        action="erp.genei.config_updated", target_type="carrier",
        target_id=carrier.id,
        metadata_json=json.dumps({"has_credentials": bool(carrier.api_credentials_encrypted)}),
    ))
    session.commit()
    session.refresh(carrier)
    return genei_config_get(session=session, current_user=current_user)
