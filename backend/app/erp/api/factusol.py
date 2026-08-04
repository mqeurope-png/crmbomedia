"""BoHub ERP — API auxiliar de FACTUSOL (Fase C · C-2-fix2).

Catálogos de solo lectura que alimentan el modal de emisión de factura (formas
de pago). Se cachean en proceso unos minutos para no re-autenticar en DELSOL en
cada apertura del modal. Las escrituras NO viven aquí (van por la cola
serializada `factusol:writes`).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.api.deps import require_erp_edit, require_erp_view
from app.models.crm import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/erp/factusol", tags=["erp-factusol"])

#: Cache en proceso {ejercicio: (expira_epoch, items)} de formas de pago.
_FOP_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_FOP_CACHE_TTL_SECONDS = 300  # 5 min


def _first(row: dict[str, Any], *cols: str) -> Any:
    for c in cols:
        v = row.get(c)
        if v not in (None, ""):
            return v
    return None


def _normalise_fop(row: dict[str, Any]) -> dict[str, Any]:
    """F_FOP → {codigo, nombre}. Los nombres exactos de columna se confirman
    con la validación de Bart; se prueban varios candidatos habituales."""
    codigo = _first(row, "CODFOP", "COFOP")
    nombre = _first(row, "DESFOP", "NOMFOP", "TITFOP", "NORFOP")
    return {"codigo": str(codigo) if codigo is not None else None,
            "nombre": nombre or (str(codigo) if codigo is not None else "")}


@router.get("/formas-pago")
def formas_pago(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Catálogo de formas de pago (F_FOP) para el desplegable del modal de
    emisión. Best-effort: si FACTUSOL no responde, devuelve lista vacía (el
    modal permite entonces dejarlo en blanco). Cache en proceso de 5 min."""
    _ = current_user
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    ejercicio = ejercicio_for(session)
    cached = _FOP_CACHE.get(ejercicio)
    now = time.time()
    if cached and cached[0] > now:
        return {"items": cached[1], "ejercicio": ejercicio, "cached": True}

    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415

    try:
        client = FactusolClient.from_settings()
        rows = client.load_table("F_FOP", ejercicio=ejercicio)
        items = [_normalise_fop(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 — FACTUSOL caído / sin credenciales
        logger.warning("factusol formas-pago falló: %s", exc)
        return {"items": [], "ejercicio": ejercicio, "error": "factusol_unreachable"}

    _FOP_CACHE[ejercicio] = (now + _FOP_CACHE_TTL_SECONDS, items)
    return {"items": items, "ejercicio": ejercicio, "cached": False}


# --- clientes: búsqueda / vínculo / alta (Fase C · C-3) ----------------------


def _client_and_ejercicio(session: Session):
    """Cliente FACTUSOL + ejercicio activo. 503 si no hay credenciales."""
    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    try:
        return FactusolClient.from_settings(), ejercicio_for(session)
    except Exception as exc:  # noqa: BLE001 — sin credenciales / config rota
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "factusol_unavailable", "detail": str(exc)[:200],
        }) from exc


@router.get("/customers/search")
def search_customers_endpoint(
    q: str = Query(..., min_length=1),
    by: str = Query(default="nif", pattern="^(nif|email|name)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Busca clientes en F_CLI (por NIF/email exactos o nombre LIKE) y marca
    cuáles ya están vinculados a una empresa/contacto del CRM."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import (  # noqa: PLC0415
        crm_links_for,
        search_customers,
    )

    client, ejercicio = _client_and_ejercicio(session)
    try:
        found = search_customers(client, q, by=by, ejercicio=ejercicio)
    except FactusolError as exc:
        logger.warning("factusol customers/search KO: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_search_failed", "detail": str(exc)[:200],
        }) from exc
    links = crm_links_for(session, [c["codcli"] for c in found if c.get("codcli")])
    for cust in found:
        link = links.get(str(cust.get("codcli")))
        cust["crm_link"] = link
        # Compat con el nombre que pedía el spec de C-3.
        cust["factusol_matches_crm_id"] = link["id"] if link else None
    return {"items": found, "ejercicio": ejercicio}


class LinkCustomerPayload(BaseModel):
    crm_type: str = Field(pattern="^(company|contact)$")
    #: Los IDs del CRM son UUID (String 36), no enteros.
    crm_id: str = Field(min_length=1, max_length=36)
    factusol_codcli: str = Field(min_length=1, max_length=36)


@router.post("/customers/link")
def link_customer_endpoint(
    payload: LinkCustomerPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Vincula un CODCLI de FACTUSOL a una empresa/contacto del CRM. 409 si ese
    código ya está vinculado a otro registro."""
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import link_to_crm  # noqa: PLC0415

    try:
        row = link_to_crm(
            session, crm_type=payload.crm_type, crm_id=payload.crm_id,
            codcli=payload.factusol_codcli,
        )
    except FactusolError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "already_linked", "detail": str(exc)[:300],
        }) from exc
    _audit_customer_link(session, current_user, payload.crm_type,
                         payload.crm_id, payload.factusol_codcli)
    session.commit()
    return {"crm_type": payload.crm_type, "crm_id": row.id,
            "factusol_codcli": payload.factusol_codcli, "linked": True}


class CreateCustomerPayload(BaseModel):
    crm_type: str = Field(pattern="^(company|contact)$")
    crm_id: str = Field(min_length=1, max_length=36)
    nombre: str = Field(min_length=1, max_length=255)
    nif: str = Field(default="", max_length=64)
    direccion: str = Field(default="", max_length=255)
    ciudad: str = Field(default="", max_length=120)
    cp: str = Field(default="", max_length=20)
    provincia: str = Field(default="", max_length=120)
    pais: str = Field(default="ES", max_length=10)
    email: str | None = Field(default=None, max_length=255)
    telefono: str | None = Field(default=None, max_length=40)


@router.post("/customers/create", status_code=201)
def create_customer_endpoint(
    payload: CreateCustomerPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Crea el cliente en F_CLI y lo vincula al CRM.

    Dos guards heredados del bug de C-2-fix1 (`BDEscribirRegistroError`):
    1. **Dedupe**: si ya existe un F_CLI con ese NIF, NO escribe — devuelve el
       CODCLI existente y lo vincula (`created=False`).
    2. **Origen Woo**: si el cliente tiene pedidos de WooCommerce, el cliente lo
       gestiona la app externa Woo→FACTUSOL → 409, solo se vincula.
    """
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import (  # noqa: PLC0415
        create_customer,
        link_to_crm,
    )

    _reject_if_woo_managed(session, payload.crm_type, payload.crm_id)
    client, ejercicio = _client_and_ejercicio(session)
    try:
        codcli, created = create_customer(
            client, payload.model_dump(), ejercicio=ejercicio,
        )
    except FactusolError as exc:
        logger.warning("factusol customers/create KO: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_create_failed", "detail": str(exc)[:300],
        }) from exc
    try:
        link_to_crm(session, crm_type=payload.crm_type,
                    crm_id=payload.crm_id, codcli=codcli)
    except FactusolError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "already_linked", "detail": str(exc)[:300],
        }) from exc
    _audit_customer_link(session, current_user, payload.crm_type,
                         payload.crm_id, codcli, created=created)
    session.commit()
    return {"factusol_codcli": codcli, "created": created,
            "crm_type": payload.crm_type, "crm_id": payload.crm_id}


def _reject_if_woo_managed(session: Session, crm_type: str, crm_id: str) -> None:
    """En los pedidos de WooCommerce el cliente lo crea la app externa
    Woo→FACTUSOL; si el CRM lo crease también reproduciría el
    `BDEscribirRegistroError` de C-2-fix1. Ahí solo se vincula."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.erp.models import Order, OrderSource  # noqa: PLC0415

    column = Order.company_id if crm_type == "company" else Order.contact_id
    has_woo = session.scalar(
        select(Order.id).where(
            column == crm_id,
            Order.external_source == OrderSource.WOOCOMMERCE.value,
        ).limit(1)
    )
    if has_woo:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "woo_managed_customer",
            "detail": (
                "Este cliente tiene pedidos de WooCommerce: lo crea la app "
                "Woo→FACTUSOL. Búscalo en FACTUSOL y vincúlalo en vez de crearlo."
            ),
        })


def _audit_customer_link(
    session: Session, actor: User, crm_type: str, crm_id: str,
    codcli: str, *, created: bool | None = None,
) -> None:
    from app.models.crm import AuditLog  # noqa: PLC0415

    session.add(AuditLog(
        actor_user_id=actor.id,
        action="erp.factusol_customer_link",
        target_type=crm_type,
        target_id=crm_id,
        metadata_json=json.dumps({
            "factusol_codcli": codcli,
            **({"created_in_factusol": created} if created is not None else {}),
        }),
    ))
