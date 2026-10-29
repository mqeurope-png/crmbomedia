"""Un pedido web que entra sin cliente: qué le falta, qué empresa se propone
y vincularla desde la ficha.

El caso del 28/10/2026: BOPRIN-100015 (2.129,60 €) llegó de la web sin
empresa, sin NIF y sin dirección (lo creó una persona desde una intervención
de SAT, `_tsm_manual`). BoHub no pudo emparejar la empresa, el plugin de la
web no creó el pedido de cliente en FACTUSOL, y el pedido se quedó en la
bandeja con pinta de normal hasta que Bart fue a facturarlo. Hubo que
vincularlo a mano por SQL. El origen está en la web; aquí se trata de que
BoHub no lo deje pasar en silencio (`workflow.datos_cliente_que_faltan` lo
marca en la bandeja) y de que se pueda arreglar desde el pedido.

Las propuestas, de más a menos segura (nunca se vincula sola: decide una
persona):

1. el cliente de FACTUSOL del pedido de cliente (F_PCL) con la referencia de
   este pedido —el mismo pedido llegó a FACTUSOL por su cuenta—, que es el
   enlace en vivo del #566 (`codcli_for_web_order`);
2. la empresa del CRM con el NIF que trajo la web (`shipping_address.nif`,
   que el importador saca de `billing.vat` o de `billing_nif` / `_billing_nif`
   del plugin «WC - APG Campo NIF/CIF/NIE»);
3. el cliente de FACTUSOL (F_CLI) con ese NIF.

Sin nada con qué emparejar, se dice, y la empresa se busca a mano. FACTUSOL
solo se lee; vincular escribe solo en BoHub (el pedido y, si el cliente de
FACTUSOL aún no tiene ficha, la empresa creada a partir de él).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.erp.company_discovery import nif_key
from app.erp.models import Order
from app.erp.web_order_company import (
    WEB_ORDER_SYNC_SOURCE,
    _codcli_free_for,
    _company_from_customer,
    factusol_client_and_ejercicio,
    find_company_for_customer,
)
from app.erp.workflow import (
    FALTA_TEXTO,
    datos_cliente_que_faltan,
    direccion_del_pedido,
    is_invoiced,
    is_web_order,
)
from app.models.crm import Company

logger = logging.getLogger(__name__)

#: Acción de auditoría al vincular la empresa de un pedido desde su ficha.
ORDER_COMPANY_LINKED_EVENT = "erp.order_company_linked"


class VincularError(ValueError):
    """No se puede vincular (pedido ya facturado, empresa archivada, cliente
    de FACTUSOL sin NIF…). Lleva un código para la API."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _empresa(c: Company, motivo: str, origen: str) -> dict[str, Any]:
    return {
        "company_id": c.id,
        "codcli": (c.factusol_company_id or "").strip() or None,
        "nombre": c.name,
        "nif": c.tax_id or c.vat,
        "motivo": motivo,
        "origen": origen,
    }


def _cliente_factusol(
    session: Session, codcli: str, cust: dict[str, Any], motivo: str, origen: str,
) -> dict[str, Any] | None:
    """Un cliente de FACTUSOL como propuesta: su empresa del CRM si ya la tiene;
    si no, se creará su ficha al vincular (y solo si tiene NIF). None si su
    ficha está archivada: no se propone."""
    company = find_company_for_customer(session, codcli, cust.get("nif"))
    if company is not None:
        return None if company.is_archived else _empresa(company, motivo, origen)
    return {
        "company_id": None,
        "codcli": codcli,
        "nombre": str(cust.get("nombre") or "").strip() or f"Cliente {codcli}",
        "nif": cust.get("nif") or None,
        "motivo": motivo + " Aún no tiene ficha en BoHub: se creará con sus datos al vincular.",
        "origen": origen,
        "sin_nif": not nif_key(cust.get("nif")),
    }


def propuestas(session: Session, order: Order) -> dict[str, Any]:
    """Qué le falta al pedido y con qué empresa se propone vincularlo."""
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _store_ref_prefix,
        pcl_ref_for_order,
    )

    company = session.get(Company, order.company_id) if order.company_id else None
    faltan = datos_cliente_que_faltan(order, company)
    d = direccion_del_pedido(order)
    nif = str(d.get("nif") or "").strip() or None
    referencia = pcl_ref_for_order(session, order)
    out: list[dict[str, Any]] = []
    factusol: dict[str, Any] = {
        "disponible": False, "referencia": referencia, "pedido_encontrado": False,
    }

    pair = factusol_client_and_ejercicio(session)
    # Por qué el pedido de cliente de FACTUSOL no da propuesta, si está.
    nota_pcl: str | None = None
    if pair is not None:
        client, ejercicio = pair
        factusol["disponible"] = True
        from app.integrations.factusol.customers import find_by_nif, get_customer  # noqa: PLC0415
        from app.integrations.factusol.service import find_pcl_by_order  # noqa: PLC0415

        try:
            pcl = find_pcl_by_order(
                client, order, ejercicio, ref_prefix=_store_ref_prefix(session, order),
            )
        except Exception as exc:  # noqa: BLE001 — FACTUSOL caído: se dice
            logger.info("cliente_pedido_web: F_PCL de %s: %s", order.order_number, exc)
            factusol["disponible"] = False
            pcl = None
        codcli = str((pcl or {}).get("CLIPCL") or "").strip()
        if codcli:
            factusol["pedido_encontrado"] = True
            factusol["pedido"] = f"{pcl.get('TIPPCL')}-{pcl.get('CODPCL')}"
            try:
                cust = get_customer(client, codcli, ejercicio=ejercicio)
            except Exception:  # noqa: BLE001
                cust = None
            propuesta = _cliente_factusol(
                session, codcli, cust,
                f"El pedido llegó a FACTUSOL como pedido de cliente {referencia}, "
                f"del cliente {codcli}.",
                "pedido_factusol",
            ) if cust else None
            if propuesta is not None:
                out.append(propuesta)
            else:
                nota_pcl = (
                    f"el pedido está en FACTUSOL como pedido de cliente ({referencia}), pero "
                    + (f"la ficha de su cliente {codcli} en BoHub está archivada" if cust
                       else f"su cliente {codcli} no se ha podido leer")
                )
        if nif and factusol["disponible"]:
            try:
                row = find_by_nif(client, nif, ejercicio=ejercicio)
            except Exception:  # noqa: BLE001
                row = None
            if row and row.get("codcli"):
                propuesta = _cliente_factusol(
                    session, str(row["codcli"]).strip(), row,
                    f"El NIF del pedido ({nif}) es el del cliente {row['codcli']} de FACTUSOL.",
                    "nif_factusol",
                )
                if propuesta is not None:
                    out.append(propuesta)

    if nif:
        from app.api.companies import find_companies_by_nif  # noqa: PLC0415

        for c in find_companies_by_nif(session, nif):
            if not c.is_archived:
                out.append(_empresa(c, f"El NIF del pedido ({nif}) es el de esta empresa.", "nif"))

    # Sin repetir: la misma empresa o el mismo cliente por dos caminos, una vez
    # (con el motivo del camino más seguro, que va primero).
    vistas: set[str] = set()
    unicas: list[dict[str, Any]] = []
    for p in out:
        clave = p["company_id"] or f"codcli:{p['codcli']}"
        if clave in vistas:
            continue
        vistas.add(clave)
        unicas.append(p)

    mensaje = None
    if not unicas:
        if not nif:
            web = "La web no trae NIF"
        elif factusol["disponible"]:
            web = f"Nadie tiene el NIF {nif}, ni en BoHub ni en FACTUSOL,"
        else:
            web = f"Nadie en BoHub tiene el NIF {nif}"
        if not factusol["disponible"]:
            fs = "no se ha podido consultar FACTUSOL"
        else:
            fs = nota_pcl or (
                f"el pedido no está en FACTUSOL como pedido de cliente ({referencia})"
            )
        mensaje = f"{web} y {fs}: no hay con qué emparejarlo. Búscala a mano."
    return {
        "faltan": faltan,
        "faltan_texto": [FALTA_TEXTO[f] for f in faltan],
        "nif": nif,
        "nombre": d.get("name") or None,
        "email": d.get("email") or None,
        "propuestas": unicas,
        "factusol": factusol,
        "mensaje": mensaje,
    }


def vincular(
    session: Session, order: Order, *, company_id: str | None = None,
    codcli: str | None = None,
) -> tuple[Company, str | None, bool]:
    """Pone la empresa del pedido. Devuelve (empresa, empresa anterior, creada).

    Con `company_id`, esa empresa del CRM. Con `codcli`, la del cliente de
    FACTUSOL (la que ya esté vinculada a él o por su NIF; si no hay, se crea
    su ficha con sus datos, y solo si tiene NIF). No hace commit.

    Solo para un pedido WEB que aún no tiene empresa: no se cambia la de un
    pedido que ya la tiene (eso se hace en su ficha), ni la de uno ya
    facturado (el cliente es el de la factura)."""
    if not is_web_order(order):
        raise VincularError("no_es_web", "Esto es para pedidos web que entran sin empresa.")
    if is_invoiced(order):
        raise VincularError(
            "ya_facturado",
            "El pedido ya está facturado: su cliente es el de la factura.",
        )
    if order.company_id:
        raise VincularError(
            "ya_tiene_empresa",
            "El pedido ya tiene empresa. Recarga la ficha para verla.",
        )
    creada = False
    if company_id:
        company = session.get(Company, company_id)
        if company is None:
            raise VincularError("empresa_no_existe", "Esa empresa no existe.")
        if company.is_archived:
            raise VincularError("empresa_archivada", "Esa empresa está archivada.")
    elif codcli:
        pair = factusol_client_and_ejercicio(session)
        if pair is None:
            raise VincularError("sin_factusol", "No se ha podido consultar FACTUSOL.")
        client, ejercicio = pair
        from app.integrations.factusol.customers import get_customer  # noqa: PLC0415

        code = str(codcli).strip()
        try:
            cust = get_customer(client, code, ejercicio=ejercicio)
        except Exception as exc:  # noqa: BLE001 — FACTUSOL caído: se dice
            logger.info("cliente_pedido_web: F_CLI %s: %s", code, exc)
            raise VincularError(
                "sin_factusol", "No se ha podido consultar FACTUSOL. Prueba en un rato.",
            ) from exc
        if not cust:
            raise VincularError("cliente_no_existe", f"No hay cliente {code} en FACTUSOL.")
        company = find_company_for_customer(session, code, cust.get("nif"))
        if company is not None and company.is_archived:
            raise VincularError(
                "empresa_archivada",
                f"La ficha del cliente {code} en BoHub ({company.name}) está archivada: "
                "desarchívala o busca otra empresa.",
            )
        if company is None:
            if not nif_key(cust.get("nif")):
                raise VincularError(
                    "cliente_sin_nif",
                    f"El cliente {code} de FACTUSOL no tiene NIF: no se crea su ficha a ciegas. "
                    "Búscala o créala a mano.",
                )
            company = _company_from_customer(session, code, cust)
            creada = True
        elif not (company.factusol_company_id or "").strip() and _codcli_free_for(
            session, code, company,
        ):
            company.factusol_company_id = code
            company.factusol_sync_source = WEB_ORDER_SYNC_SOURCE
    else:
        raise VincularError("falta_empresa", "Indica la empresa.")

    anterior = order.company_id
    order.company_id = company.id
    # El contacto del pedido sin empresa pasa a ser de ella (lo que hace el
    # importador cuando la resuelve él).
    if order.contact_id:
        from app.models.crm import Contact  # noqa: PLC0415

        contact = session.get(Contact, order.contact_id)
        if contact is not None and not contact.company_id:
            contact.company_id = company.id
    session.flush()
    return company, anterior, creada
