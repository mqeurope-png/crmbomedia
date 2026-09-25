"""Destino del ENVÍO de un pedido (dirección, teléfono, email) venga de donde venga.

Genei necesita dirección de entrega, teléfono y email. Cada origen de pedido
los deja en un sitio distinto:

- **WEB** (WooCommerce): `packing_json.shipping_address`, completo (con
  teléfono, email y NIF del billing);
- **manual**: `packing_json.shipping_address` (solo la dirección) y, si lo hay,
  el contacto del pedido;
- **muestra**: `shipping_address` (dirección) + `shipping_contact` (a quién:
  nombre, teléfono y email);
- **factura / albarán / proforma / pedido de FACTUSOL**: sin contacto ni
  dirección propios. El destino está en el bloque de cliente/entrega de la
  CABECERA del documento (`CDO/CPO/CCP/CPR/CPA/TEL/CEM*`) y en la ficha del
  cliente (F_CLI). Desde este cambio, al crearlos se guarda ese bloque en
  `shipping_contact`. Los creados antes lo leen de FACTUSOL al pedirlo
  (`completar=True`).

Aquí se juntan las fuentes por capas, CAMPO A CAMPO para el teléfono, el email,
el nombre y el NIF. La DIRECCIÓN va en BLOQUE: se toma entera de la primera
fuente que la tenga, porque mezclar la calle de una con la ciudad de otra sería
una dirección inventada. Solo el país se completa de otra fuente si falta.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import Order

logger = logging.getLogger(__name__)

#: Campos del destino (las claves que ya usaba `resolve_destination_fields`).
CAMPOS = ("name", "contact", "email", "phone", "dni",
          "address", "postal_code", "city", "country")
_DIRECCION = ("address", "postal_code", "city", "country")

#: Precedencia de fuentes por campo (la primera con dato gana).
#:   pedido      → `packing_json.shipping_address` (+ `shipping_name`)
#:   destinatario→ `packing_json.shipping_contact` (muestras; y, desde ahora, el
#:                 bloque de entrega del documento FACTUSOL de origen)
#:   contacto    → el contacto del pedido
#:   documento   → cabecera del documento FACTUSOL de origen (en vivo)
#:   ficha       → ficha F_CLI del cliente (en vivo)
#:   empresa     → la empresa del pedido
#:   contacto_empresa → primer contacto activo de la empresa con ese dato
_ORDEN_DIRECCION = ("pedido", "destinatario", "documento", "empresa", "ficha", "contacto")
_ORDEN_CONTACTO = ("pedido", "destinatario", "contacto", "documento", "ficha",
                   "contacto_empresa")
_ORDEN_NOMBRE = ("pedido", "destinatario", "empresa", "documento", "ficha", "contacto")
_ORDEN_NIF = ("pedido", "empresa", "documento", "ficha")


def _t(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _persona(first: Any, last: Any) -> str:
    return " ".join(p for p in (_t(first), _t(last)) if p)


def _bloque(data: Any) -> dict[str, Any]:
    return data if isinstance(data, dict) else {}


def _desde_envio(block: dict[str, Any]) -> dict[str, str]:
    """`shipping_address` / `shipping_contact` (claves del alta, de Woo y de la
    muestra) → campos del destino."""
    return {
        "name": _t(block.get("name")) or _t(block.get("company")),
        "contact": _t(block.get("name")),
        "email": _t(block.get("email")),
        "phone": _t(block.get("phone")),
        "dni": _t(block.get("nif")),
        "address": _t(block.get("address_line")),
        "postal_code": _t(block.get("postal_code")),
        "city": _t(block.get("city")),
        "country": _t(block.get("country")),
    }


def entrega_a_bloque(entrega: dict[str, Any] | None) -> dict[str, Any] | None:
    """Bloque de entrega de una cabecera FACTUSOL (`documents.delivery_block`)
    → la forma de `packing_json.shipping_contact`. None si no trae nada."""
    e = _bloque(entrega)
    out = {
        "name": _t(e.get("nombre")) or None,
        "email": _t(e.get("email")) or None,
        "phone": _t(e.get("telefono")) or None,
        "nif": _t(e.get("nif")) or None,
        "address_line": _t(e.get("direccion")) or None,
        "city": _t(e.get("poblacion")) or None,
        "postal_code": _t(e.get("cp")) or None,
        "state": _t(e.get("provincia")) or None,
        "country": _t(e.get("pais")) or None,
        "source": "factusol_documento",
    }
    return out if any(v for k, v in out.items() if k != "source") else None


def _desde_ficha(row: dict[str, Any]) -> dict[str, str]:
    """Ficha F_CLI normalizada (`customers.get_customer`) → campos."""
    return {
        "name": _t(row.get("nombre")) or _t(row.get("nofcli")),
        "contact": "",
        "email": _t(row.get("emacli")),
        "phone": _t(row.get("telcli")),
        "dni": _t(row.get("nif")),
        "address": _t(row.get("domcli")),
        "postal_code": _t(row.get("cpocli")),
        "city": _t(row.get("pobcli")),
        "country": _t(row.get("pais_iso2")) or _t(row.get("paicli")),
    }


def _fuentes_locales(session: Session, order: Order) -> dict[str, dict[str, str]]:
    """Lo que hay en BoHub, sin salir a FACTUSOL."""
    from app.erp.factusol_albaran import packing_of  # noqa: PLC0415
    from app.integrations.factusol.albaran_manual import order_shipping_name  # noqa: PLC0415
    from app.models.crm import Company, Contact  # noqa: PLC0415

    packing = packing_of(order)
    fuentes: dict[str, dict[str, str]] = {}

    pedido = _desde_envio(_bloque(packing.get("shipping_address")))
    # El nombre de envío del pedido (dropshipping) manda sobre el de la
    # dirección solo si la dirección no trae nombre propio.
    pedido["name"] = pedido["name"] or order_shipping_name(order)
    fuentes["pedido"] = pedido
    fuentes["destinatario"] = _desde_envio(_bloque(packing.get("shipping_contact")))

    contact = session.get(Contact, order.contact_id) if order.contact_id else None
    if contact is not None:
        nombre = _persona(contact.first_name, contact.last_name)
        fuentes["contacto"] = {
            "name": nombre, "contact": nombre,
            "email": _t(contact.email), "phone": _t(contact.phone), "dni": "",
            "address": _t(contact.address_line),
            "postal_code": _t(contact.address_postal_code),
            "city": _t(contact.address_city), "country": _t(contact.address_country),
        }

    company = session.get(Company, order.company_id) if order.company_id else None
    if company is not None:
        fuentes["empresa"] = {
            "name": _t(company.name), "contact": "", "email": "", "phone": "",
            "dni": _t(company.vat), "address": _t(company.address_line),
            "postal_code": _t(company.postal_code), "city": _t(company.city),
            "country": _t(company.country),
        }
        # Teléfono / email: la empresa no los tiene; el primer contacto activo
        # que los tenga (los pedidos de FACTUSOL no llevan contacto propio).
        email = phone = persona = ""
        for c in session.scalars(
            select(Contact).where(
                Contact.company_id == company.id, Contact.is_active.is_(True),
            ).order_by(Contact.last_name.asc(), Contact.first_name.asc())
        ):
            if not email and c.is_email_valid and _t(c.email):
                email = _t(c.email)
                persona = persona or _persona(c.first_name, c.last_name)
            if not phone and _t(c.phone):
                phone = _t(c.phone)
                persona = persona or _persona(c.first_name, c.last_name)
            if email and phone:
                break
        fuentes["contacto_empresa"] = {
            "name": "", "contact": persona, "email": email, "phone": phone, "dni": "",
            "address": "", "postal_code": "", "city": "", "country": "",
        }
    return fuentes


def _fuentes_factusol(
    session: Session, order: Order, client: Any, ejercicio: str,
) -> dict[str, dict[str, str]]:
    """Cabecera del documento FACTUSOL de origen (bloque de entrega) y ficha
    F_CLI del cliente. De mejor esfuerzo: si FACTUSOL falla, lo que haya."""
    from app.erp.factusol_albaran import packing_of  # noqa: PLC0415
    from app.integrations.factusol.customers import get_customer  # noqa: PLC0415
    from app.integrations.factusol.documents import DOC_SPECS, get_header  # noqa: PLC0415

    fuentes: dict[str, dict[str, str]] = {}
    origen = _bloque(packing_of(order).get("factusol_source"))
    codcli = _t(origen.get("cliente_codigo"))
    doc_type = _t(origen.get("doc_type"))
    if doc_type in DOC_SPECS and origen.get("serie") is not None and origen.get("codigo"):
        try:
            header = get_header(client, doc_type, serie=int(origen["serie"]),
                                codigo=int(origen["codigo"]), ejercicio=ejercicio)
        except Exception as exc:  # noqa: BLE001 — de mejor esfuerzo
            logger.info("destino: no se pudo leer la cabecera FACTUSOL: %s", exc)
            header = None
        if header:
            bloque = entrega_a_bloque(header.get("entrega"))
            if bloque:
                fuentes["documento"] = _desde_envio(bloque)
            codcli = codcli or _t(header.get("cliente_codigo"))
    if not codcli:
        try:
            from app.erp.order_factusol_customer import resolve_order_codcli  # noqa: PLC0415

            codcli = _t(resolve_order_codcli(
                session, order, client=client, ejercicio=ejercicio)[0])
        except Exception as exc:  # noqa: BLE001
            logger.info("destino: no se pudo resolver el cliente FACTUSOL: %s", exc)
    if codcli:
        try:
            row = get_customer(client, codcli, ejercicio=ejercicio)
        except Exception as exc:  # noqa: BLE001
            logger.info("destino: no se pudo leer la ficha F_CLI %s: %s", codcli, exc)
            row = None
        if row:
            fuentes["ficha"] = _desde_ficha(row)
    return fuentes


def _faltan(campos: dict[str, str]) -> bool:
    return not (campos["email"] and campos["phone"] and campos["address"])


def componer(fuentes: dict[str, dict[str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    """`(campos, origen_de_cada_campo)` aplicando la precedencia."""
    campos = dict.fromkeys(CAMPOS, "")
    origen: dict[str, str] = {}

    def primero(campo: str, orden: tuple[str, ...]) -> None:
        for nombre in orden:
            valor = (fuentes.get(nombre) or {}).get(campo, "")
            if valor:
                campos[campo], origen[campo] = valor, nombre
                return

    # Dirección en BLOQUE: la primera fuente con calle, CP o población.
    for nombre in _ORDEN_DIRECCION:
        f = fuentes.get(nombre) or {}
        if f.get("address") or f.get("postal_code") or f.get("city"):
            for campo in _DIRECCION:
                campos[campo] = f.get(campo, "")
            origen["address"] = nombre
            break
    if not campos["country"]:
        primero("country", _ORDEN_DIRECCION)
    for campo in ("email", "phone"):
        primero(campo, _ORDEN_CONTACTO)
    primero("name", _ORDEN_NOMBRE)
    primero("contact", ("contacto", "destinatario", "contacto_empresa", "pedido"))
    campos["contact"] = campos["contact"] or campos["name"]
    primero("dni", _ORDEN_NIF)
    return campos, origen


def resolve_shipping_destination(
    session: Session, order: Order, *, completar: bool = False,
    factusol: Callable[[Session], tuple[Any, str] | None] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Destino del envío del pedido y de qué fuente sale cada campo.

    Con `completar=True`, si faltan dirección, teléfono o email, se consulta
    FACTUSOL (cabecera del documento de origen y ficha del cliente). Es de
    mejor esfuerzo: si FACTUSOL no está configurado o falla, se devuelve lo
    que haya en BoHub."""
    fuentes = _fuentes_locales(session, order)
    campos, origen = componer(fuentes)
    if completar and _faltan(campos):
        if factusol is None:
            from app.erp.web_order_company import factusol_client_and_ejercicio  # noqa: PLC0415

            factusol = factusol_client_and_ejercicio
        conexion = factusol(session)
        if conexion is not None:
            client, ejercicio = conexion
            fuentes.update(_fuentes_factusol(session, order, client, ejercicio))
            campos, origen = componer(fuentes)
    return campos, origen
