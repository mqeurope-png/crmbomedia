"""Empresa CRM de un pedido WEB, resuelta por su CLIENTE de FACTUSOL.

El problema: los pedidos web (FesteWeb) crean/reutilizan el cliente en FACTUSOL
(F_CLI) y el pedido queda con ese cliente, pero en BoHub el mapper solo sabía
resolver la empresa del CRM cuando lograba sacar el NIF del *billing* de Woo
(`billing_nif`/`billing_cif`/…). Cuando el billing no lo traía, el pedido se
quedaba SIN empresa (~26 % de los pedidos web en producción).

Aquí la empresa se resuelve por el camino fiable, el que ya usa el resto del
ERP para hablar con FACTUSOL:

    pedido web → REFPCL (prefijo de tienda + nº Woo) → F_PCL.CLIPCL → F_CLI

y con el NIF de ese F_CLI se busca la empresa en el CRM por **clave canónica**
(`company_discovery.nif_key`: sin separadores y sin el prefijo de país de la
UE, así que `FR91523447399` ≡ `91523447399`). Si existe se vincula; si no,
se crea a partir del F_CLI y nace ya enlazada a su CODCLI.

Reglas:

- **Nunca rompe la ingesta.** Cualquier fallo de FACTUSOL (caído, sin
  credenciales, el F_PCL aún no existe porque FesteWeb no lo ha creado todavía)
  se traga y se devuelve el motivo: el pedido entra igual y el backfill lo
  recupera después.
- **No duplica.** Siempre se busca por CODCLI y por NIF canónico antes de crear.
  Con varias candidatas gana la que ya está vinculada a FACTUSOL y, entre
  iguales, la que tiene NIF.
- **Idempotente.** Reprocesar un pedido no crea empresas ni cambia una
  vinculación correcta.
- **Solo CRM.** Lee F_CLI/F_PCL; no escribe NADA en FACTUSOL (crear el cliente
  sigue siendo de FesteWeb).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.company_discovery import nif_key
from app.erp.models import Order
from app.models.crm import Company

logger = logging.getLogger(__name__)

#: `source` / `factusol_sync_source` de las empresas que nacen de este camino.
WEB_ORDER_SOURCE = "factusol"
WEB_ORDER_SYNC_SOURCE = "web_order_link"

# Motivos por los que un pedido se queda sin empresa (los lista el backfill).
REASON_ALREADY = "ya_tenia_empresa"
REASON_NOT_WEB = "no_es_pedido_web"
REASON_NO_FACTUSOL = "factusol_no_disponible"
REASON_NO_PCL = "sin_pedido_en_factusol"
REASON_NO_CLIENT = "sin_cliente_en_factusol"
REASON_NO_NIF = "cliente_sin_nif"
REASON_CODCLI_TAKEN = "codcli_vinculado_a_otra_ficha"


@dataclass(frozen=True)
class CompanyResolution:
    """Resultado de resolver la empresa de un pedido web."""

    company: Company | None = None
    codcli: str | None = None
    #: Se creó la empresa CRM a partir del F_CLI.
    created: bool = False
    #: Se rellenó `order.company_id` (antes estaba vacío).
    linked_order: bool = False
    #: Se rellenó `Company.factusol_company_id` (antes estaba vacío).
    linked_company: bool = False
    #: Por qué no se pudo resolver (None si se resolvió).
    reason: str | None = None

    @property
    def resolved(self) -> bool:
        return self.company is not None


def _is_web(order: Order) -> bool:
    from app.erp.factusol_albaran import is_web_order  # noqa: PLC0415

    return is_web_order(order)


def factusol_client_and_ejercicio(session: Session) -> tuple[Any, str] | None:
    """Cliente FACTUSOL + ejercicio para un contexto SIN request (worker, script).

    Mismo patrón que los jobs de FACTUSOL, pero **best-effort**: devuelve None
    si no hay configuración o el cliente no se puede construir, en vez de
    reventar la ingesta de pedidos."""
    try:
        from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415
        from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

        return FactusolClient.from_settings(), ejercicio_for(session)
    except Exception as exc:  # noqa: BLE001 — sin FACTUSOL se sigue sin empresa
        logger.info("web_order_company: FACTUSOL no disponible: %s", exc)
        return None


def codcli_for_web_order(
    session: Session, order: Order, client: Any, ejercicio: str,
    *, fallback_nif: str | None = None,
) -> str | None:
    """CODCLI del cliente FACTUSOL de este pedido web.

    Vía principal: el pedido de cliente (F_PCL) que FesteWeb creó con la
    referencia común `REFPCL` → su `CLIPCL`. Si aún no existe (carrera: BoHub
    importa antes de que FesteWeb escriba en FACTUSOL), se cae al NIF del
    billing de Woo buscando en F_CLI."""
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _store_ref_prefix,
        find_pcl_by_order,
    )

    try:
        pcl = find_pcl_by_order(
            client, order, ejercicio, ref_prefix=_store_ref_prefix(session, order),
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "web_order_company: F_PCL de %s no consultable: %s", order.order_number, exc,
        )
        pcl = None
    if pcl:
        codcli = str(pcl.get("CLIPCL") or "").strip()
        if codcli:
            return codcli
    # Fallback: el NIF del billing (lo que hacía el mapper), pero contra F_CLI.
    if fallback_nif:
        try:
            from app.integrations.factusol.customers import find_by_nif  # noqa: PLC0415

            row = find_by_nif(client, fallback_nif, ejercicio=ejercicio)
        except Exception as exc:  # noqa: BLE001
            logger.info("web_order_company: F_CLI por NIF KO: %s", exc)
            row = None
        if row and row.get("codcli"):
            return str(row["codcli"]).strip()
    return None


def find_company_for_customer(
    session: Session, codcli: str | None, nif: Any,
) -> Company | None:
    """Empresa del CRM que corresponde a un cliente F_CLI, sin crear nada.

    1) La que YA está vinculada a ese CODCLI (el vínculo manda).
    2) Si no, por NIF canónico (`nif_key`): `FR91523447399` ≡ `91523447399`.
       Con varias candidatas gana la que ya está vinculada a FACTUSOL y,
       entre iguales, la que tiene NIF — nunca se coge una al azar."""
    code = str(codcli or "").strip()
    if code:
        linked = session.scalar(
            select(Company).where(Company.factusol_company_id == code)
        )
        if linked is not None:
            return linked
    key = nif_key(nif)
    if not key:
        return None
    from app.api.companies import find_companies_by_nif  # noqa: PLC0415

    candidates = [c for c in find_companies_by_nif(session, nif) if not c.is_archived]
    if not candidates:
        return None
    candidates.sort(key=lambda c: (
        0 if (c.factusol_company_id or "").strip() else 1,
        0 if nif_key(c.tax_id) or nif_key(c.vat) else 1,
    ))
    return candidates[0]


def _company_from_customer(session: Session, codcli: str, cust: dict[str, Any]) -> Company:
    """Empresa CRM con los datos del cliente F_CLI. Nace ya vinculada."""
    company = Company(
        name=str(cust.get("nombre") or "").strip() or f"Cliente {codcli}",
        tax_id=str(cust.get("nif") or "").strip() or None,
        address_line=str(cust.get("domcli") or "").strip() or None,
        city=str(cust.get("pobcli") or "").strip() or None,
        postal_code=str(cust.get("cpocli") or "").strip() or None,
        state=str(cust.get("procli") or "").strip() or None,
        country=cust.get("pais_iso2") or None,
        source=WEB_ORDER_SOURCE,
        factusol_company_id=codcli,
        factusol_sync_source=WEB_ORDER_SYNC_SOURCE,
        factusol_synced_at=datetime.now(UTC),
    )
    session.add(company)
    session.flush()
    return company


def _codcli_free_for(session: Session, codcli: str, company: Company) -> bool:
    """¿Ese CODCLI está libre (o ya es de esta empresa)? Evita que dos fichas
    del CRM reclamen el mismo cliente de FACTUSOL."""
    from app.integrations.factusol.customers import crm_links_for  # noqa: PLC0415

    holder = crm_links_for(session, [codcli]).get(codcli)
    return holder is None or (holder.get("type") == "company" and holder.get("id") == company.id)


def ensure_web_order_company(
    session: Session, order: Order, *,
    client: Any = None, ejercicio: str | None = None,
    fallback_nif: str | None = None, create: bool = True,
) -> CompanyResolution:
    """Asegura que un pedido WEB tiene empresa CRM, resolviéndola por su cliente
    de FACTUSOL. No hace commit (el caller manda). Nunca lanza."""
    if not _is_web(order):
        return CompanyResolution(reason=REASON_NOT_WEB)

    already = session.get(Company, order.company_id) if order.company_id else None
    if already is not None and (already.factusol_company_id or "").strip():
        # Ya resuelto y vinculado: nada que hacer (idempotencia).
        return CompanyResolution(company=already, codcli=already.factusol_company_id,
                                 reason=REASON_ALREADY)

    if client is None or ejercicio is None:
        pair = factusol_client_and_ejercicio(session)
        if pair is None:
            return CompanyResolution(company=already, reason=REASON_NO_FACTUSOL)
        client, ejercicio = pair

    codcli = codcli_for_web_order(
        session, order, client, ejercicio, fallback_nif=fallback_nif,
    )
    if not codcli:
        return CompanyResolution(company=already, reason=REASON_NO_PCL)

    try:
        from app.integrations.factusol.customers import get_customer  # noqa: PLC0415

        cust = get_customer(client, codcli, ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001
        logger.info("web_order_company: F_CLI %s no legible: %s", codcli, exc)
        cust = None
    if not cust:
        return CompanyResolution(company=already, codcli=codcli, reason=REASON_NO_CLIENT)

    # El pedido ya tenía empresa pero sin vínculo: basta con enlazarla.
    company = already or find_company_for_customer(session, codcli, cust.get("nif"))
    created = False
    if company is None:
        if not create:
            return CompanyResolution(codcli=codcli, reason=REASON_NO_CLIENT)
        if not nif_key(cust.get("nif")):
            # Sin NIF no se crea nada a ciegas: se lista para revisión.
            return CompanyResolution(codcli=codcli, reason=REASON_NO_NIF)
        company = _company_from_customer(session, codcli, cust)
        created = True

    linked_company = False
    if not created and not (company.factusol_company_id or "").strip():
        # Guard: si el pedido ya traía una empresa y su NIF NO es el del cliente
        # de FACTUSOL, no se le cuelga ese CODCLI (sería un vínculo falso).
        own = nif_key(company.tax_id) or nif_key(company.vat)
        mismatched = bool(own) and own != nif_key(cust.get("nif"))
        if mismatched:
            logger.info(
                "web_order_company: la empresa %s del pedido %s tiene otro NIF "
                "que el F_CLI %s; no se toca su vínculo",
                company.id, order.order_number, codcli,
            )
        elif _codcli_free_for(session, codcli, company):
            company.factusol_company_id = codcli
            company.factusol_sync_source = WEB_ORDER_SYNC_SOURCE
            company.factusol_synced_at = datetime.now(UTC)
            linked_company = True
        else:
            logger.info(
                "web_order_company: CODCLI %s ya vinculado a otra ficha; "
                "el pedido %s se enlaza a la empresa sin tocar el vínculo",
                codcli, order.order_number,
            )

    linked_order = False
    if order.company_id != company.id:
        order.company_id = company.id
        linked_order = True
    session.flush()
    return CompanyResolution(
        company=company, codcli=codcli, created=created,
        linked_order=linked_order, linked_company=linked_company,
    )
