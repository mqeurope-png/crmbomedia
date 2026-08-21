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
from app.erp.api.deps import (
    require_erp_admin,
    require_erp_edit,
    require_erp_view,
)
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


def _fop_names(client, ejercicio: str) -> dict[str, str]:
    """`{codigo → nombre}` de F_FOP, reutilizando la cache de /formas-pago.
    Best-effort: si FACTUSOL no responde, dict vacío (el caller pinta el
    código crudo). Indexa también el código sin ceros a la izquierda: el
    documento guarda `'002'` y el catálogo puede servir `2`."""
    now = time.time()
    cached = _FOP_CACHE.get(ejercicio)
    if cached and cached[0] > now:
        items = cached[1]
    else:
        try:
            rows = client.load_table("F_FOP", ejercicio=ejercicio)
            items = [_normalise_fop(r) for r in rows]
            _FOP_CACHE[ejercicio] = (now + _FOP_CACHE_TTL_SECONDS, items)
        except Exception as exc:  # noqa: BLE001
            logger.warning("factusol F_FOP para nombres falló: %s", exc)
            return {}
    out: dict[str, str] = {}
    for item in items:
        codigo = str(item.get("codigo") or "").strip()
        nombre = str(item.get("nombre") or "").strip()
        if codigo and nombre:
            out[codigo] = nombre
            out[codigo.lstrip("0") or "0"] = nombre
    return out


def _normalise_fop(row: dict[str, Any]) -> dict[str, Any]:
    """F_FOP → {codigo, nombre}. Los nombres exactos de columna se confirman
    con la validación de Bart; se prueban varios candidatos habituales."""
    codigo = _first(row, "CODFOP", "COFOP")
    nombre = _first(row, "DESFOP", "NOMFOP", "TITFOP", "NORFOP")
    return {"codigo": str(codigo) if codigo is not None else None,
            "nombre": nombre or (str(codigo) if codigo is not None else "")}


#: Nombres de serie por defecto si aún no se han configurado en /erp/settings.
#: Confirmados por Bart (ERP-E2). Hay más series en uso para otras cosas; el
#: selector las lista igualmente como «Serie N» para no bloquear al operador.
FALLBACK_SERIES_NAMES: dict[int, str] = {
    1: "Bomedia",
    2: "MQ Europe",
    5: "Streamtec",
}


@router.get("/series")
def series(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Series de facturación = empresas emisoras, para el selector del modal.

    En FACTUSOL la serie NO es una columna: identifica la empresa y va
    codificada en el rango del número de documento (serie N ⇒ `Nxxxxx`). Los
    nombres son configurables en `/erp/settings`; si no hay nada guardado se
    usan los conocidos y el resto se muestran como «Serie N»."""
    from app.integrations.factusol.service import (  # noqa: PLC0415
        VALID_SERIES,
        default_serie,
        series_names,
    )

    configured = series_names(session)
    predeterminada = default_serie(session)
    items = [
        {
            "serie": n,
            "nombre": configured.get(n) or FALLBACK_SERIES_NAMES.get(n)
            or f"Serie {n}",
            "is_default": n == predeterminada,
            # Sin nombre propio = serie que nadie ha reclamado todavía; la UI
            # las ordena detrás para que las de Bart salgan arriba.
            "is_known": n in configured or n in FALLBACK_SERIES_NAMES,
        }
        for n in VALID_SERIES
    ]
    return {"items": items, "default": predeterminada}


# --- explorador de documentos (ERP-E3-A, solo lectura) -----------------------


@router.get("/documents/{doc_type}")
def list_factusol_documents(
    doc_type: str,
    codcli: str | None = Query(default=None),
    serie: int | None = Query(default=None, ge=1, le=9),
    estado: str | None = Query(default=None, max_length=10),
    fecha_desde: str | None = Query(default=None, max_length=10),
    fecha_hasta: str | None = Query(default=None, max_length=10),
    q: str | None = Query(default=None, max_length=120),
    cliente_q: str | None = Query(default=None, max_length=120),
    sort: str = Query(default="numero", pattern="^(numero|cliente|fecha|total)$"),
    dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Listado EN VIVO de pedidos/presupuestos/albaranes/facturas de
    FACTUSOL, filtrable. Solo lectura — este endpoint no escribe nada."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.documents import (  # noqa: PLC0415
        DOC_SPECS,
        list_documents,
    )

    if doc_type not in DOC_SPECS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Tipo de documento desconocido: {doc_type!r}",
        )
    client, ejercicio = _client_and_ejercicio(session)
    try:
        return list_documents(
            client, doc_type, ejercicio=ejercicio,
            codcli=codcli, serie=serie, estado=estado,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
            q=q, cliente_q=cliente_q, sort=sort, direction=dir,
            limit=limit, offset=offset,
        )
    except FactusolError as exc:
        logger.warning("factusol documents/%s KO: %s", doc_type, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_list_failed", "detail": str(exc)[:200],
        }) from exc


@router.get("/documents/{doc_type}/{serie}/{codigo}")
def get_factusol_document(
    doc_type: str,
    serie: int,
    codigo: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Detalle read-only de un documento con sus líneas. La clave es
    COMPUESTA (serie, código): el código solo es único por serie."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.documents import (  # noqa: PLC0415
        DOC_SPECS,
        get_document,
    )

    if doc_type not in DOC_SPECS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Tipo de documento desconocido: {doc_type!r}",
        )
    client, ejercicio = _client_and_ejercicio(session)
    try:
        doc = get_document(
            client, doc_type, serie=serie, codigo=codigo, ejercicio=ejercicio,
        )
    except FactusolError as exc:
        logger.warning("factusol documents/%s detalle KO: %s", doc_type, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_detail_failed", "detail": str(exc)[:200],
        }) from exc
    if doc is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No existe el documento {serie}-{codigo} en {doc_type}",
        )
    # E3-A-fix1 — forma de pago con nombre (catálogo F_FOP de C-2-fix2).
    fop = str(doc.get("forma_pago") or "").strip()
    doc["forma_pago_nombre"] = None
    if fop:
        names = _fop_names(client, ejercicio)
        doc["forma_pago_nombre"] = (
            names.get(fop) or names.get(fop.lstrip("0") or "0")
        )
    return doc


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


@router.get("/customers/{codcli}/addresses")
def customer_addresses_endpoint(
    codcli: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Direcciones del cliente F_CLI: la principal (`codigo: 0`) más las
    adicionales configuradas (`ACO1CLI`…`ACO4CLI`) — las que el escritorio
    enseña en el botón «Direcciones».

    Para mandar una proforma a una delegación distinta de la sede. Solo
    lectura: se elige entre las que ya existan, nunca se editan desde el CRM."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import (  # noqa: PLC0415
        customer_addresses,
    )

    client, ejercicio = _client_and_ejercicio(session)
    try:
        items = customer_addresses(client, codcli, ejercicio=ejercicio)
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_addresses_failed") from exc
    return {"items": items, "ejercicio": ejercicio}


# --- conciliación masiva CRM ↔ FACTUSOL (Fase C · C-5) -----------------------


class BulkMatchDryRunPayload(BaseModel):
    filter: str = Field(default="unlinked_only", pattern="^(unlinked_only|all)$")
    #: `None` = sin tope (el modo por email procesa TODOS los contactos, C-5-fix2).
    #: El modo por empresa cae a `DEFAULT_BATCH_SIZE` porque sí pagina en SQL.
    batch_size: int | None = Field(default=None, ge=1, le=100_000)


@router.post("/bulk-match/dry-run")
def bulk_match_dry_run(
    payload: BulkMatchDryRunPayload | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Propone parejas empresa CRM ↔ cliente F_CLI. **Solo lectura.**

    Idempotente: no escribe nada, ni en el CRM ni en FACTUSOL. Lee `F_CLI`
    entero de una vez y cruza en Python — con miles de empresas, preguntar por
    cada una serían miles de peticiones contra un token de 3 minutos."""
    _ = current_user
    from app.integrations.factusol.bulk_match import (  # noqa: PLC0415
        DEFAULT_BATCH_SIZE,
        dry_run,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    payload = payload or BulkMatchDryRunPayload()
    client, ejercicio = _client_and_ejercicio(session)
    try:
        return dry_run(
            session, client, ejercicio=ejercicio,
            unlinked_only=payload.filter == "unlinked_only",
            batch_size=payload.batch_size or DEFAULT_BATCH_SIZE,
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_bulk_match_failed") from exc


class BulkMatchOperation(BaseModel):
    crm_company_id: str = Field(min_length=1, max_length=36)
    factusol_codcli: str = Field(min_length=1, max_length=36)
    fields_to_sync: list[str] = Field(default_factory=list)


class BulkMatchApplyPayload(BaseModel):
    operations: list[BulkMatchOperation] = Field(default_factory=list)


@router.post("/bulk-match/apply")
def bulk_match_apply(
    payload: BulkMatchApplyPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Sobrescribe SOLO las empresas y campos aprobados, guardando antes los
    valores previos en el AuditLog. Una empresa por transacción: que una falle
    no invalida el resto del lote."""
    from app.integrations.factusol.bulk_match import (  # noqa: PLC0415
        apply_operations,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    if not payload.operations:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "no_operations", "detail": "No hay nada que aplicar.",
        })
    client, ejercicio = _client_and_ejercicio(session)
    try:
        return apply_operations(
            session, client, ejercicio=ejercicio,
            operations=[op.model_dump() for op in payload.operations],
            actor_id=current_user.id,
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_bulk_apply_failed") from exc


@router.post("/bulk-match/by-contact-email/dry-run")
def bulk_match_by_email_dry_run(
    payload: BulkMatchDryRunPayload | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Propone parejas contacto → cliente F_CLI por **email exacto**.

    C-5-fix1: el modo por NIF/nombre da mucho ruido — la mayoría de las
    empresas del CRM vienen de imports sin NIF, y el nombre difuso produce
    falsos positivos («4d Factory» ↔ «FACTORY»). El email o coincide o no.

    Procesa **todos** los contactos con email: F_CLI se lee una sola vez y el
    resto es comparar strings, así que el coste apenas crece con el volumen."""
    _ = current_user
    from app.integrations.factusol.bulk_match import (  # noqa: PLC0415
        dry_run_by_contact_email,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    payload = payload or BulkMatchDryRunPayload()
    client, ejercicio = _client_and_ejercicio(session)
    try:
        return dry_run_by_contact_email(
            session, client, ejercicio=ejercicio, batch_size=payload.batch_size,
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_bulk_match_failed") from exc


class BulkMatchByEmailOperation(BaseModel):
    contact_id: str = Field(min_length=1, max_length=36)
    factusol_codcli: str = Field(min_length=1, max_length=36)
    fields_to_sync: list[str] = Field(default_factory=list)


class BulkMatchByEmailApplyPayload(BaseModel):
    operations: list[BulkMatchByEmailOperation] = Field(default_factory=list)


@router.post("/bulk-match/by-contact-email/apply")
def bulk_match_by_email_apply(
    payload: BulkMatchByEmailApplyPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Actualiza la empresa de cada contacto con los datos de su cliente F_CLI.

    Los desenlaces van desglosados aparte de `errors`: crear la empresa de un
    contacto huérfano, reutilizar una ya vinculada a ese CODCLI o reasignar un
    contacto mal agrupado (C-5-fix5) no son fallos, son resultados distintos."""
    from app.integrations.factusol.bulk_match import (  # noqa: PLC0415
        apply_by_contact_email,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    if not payload.operations:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "no_operations", "detail": "No hay nada que aplicar.",
        })
    client, ejercicio = _client_and_ejercicio(session)
    try:
        return apply_by_contact_email(
            session, client, ejercicio=ejercicio,
            operations=[op.model_dump() for op in payload.operations],
            actor_id=current_user.id,
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_bulk_apply_failed") from exc


class ImportOrphansDryRunPayload(BaseModel):
    filter: str = Field(default="all", pattern="^(all|only_with_email)$")


@router.post("/bulk-match/import-orphans/dry-run")
def import_orphans_dry_run(
    payload: ImportOrphansDryRunPayload | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Lista los clientes de FACTUSOL que no tiene ninguna empresa del CRM.

    C-6: después de conciliar lo que existía en los dos lados (C-5), quedan
    miles de `F_CLI` que nunca llegaron al CRM — facturación de años que no
    entró por Woo, formularios ni imports antiguos. Solo lee."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.import_orphans import (  # noqa: PLC0415
        dry_run_orphans,
    )

    payload = payload or ImportOrphansDryRunPayload()
    client, ejercicio = _client_and_ejercicio(session)
    try:
        return dry_run_orphans(
            session, client, ejercicio=ejercicio,
            only_with_email=payload.filter == "only_with_email",
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_import_orphans_failed") from exc


class ImportOrphanOperation(BaseModel):
    codcli: str = Field(min_length=1, max_length=36)
    #: Los datos de F_CLI tal cual los devolvió el dry-run. Con esto el apply
    #: no llama a FACTUSOL: el navegador ya los tiene y volver a pedirlos solo
    #: añadía un punto de fallo (C-6-fix1).
    factusol_data: dict[str, Any] | None = None


class ImportOrphansApplyPayload(BaseModel):
    operations: list[ImportOrphanOperation] = Field(default_factory=list)
    #: Formato viejo, solo códigos. Se mantiene para no romper a un cliente que
    #: no se haya recargado; obliga a releer esos CODCLI de F_CLI.
    codclis: list[str] = Field(default_factory=list)
    create_contacts_if_email: bool = True

    def as_operations(self) -> list[dict[str, Any]]:
        if self.operations:
            return [op.model_dump() for op in self.operations]
        return [{"codcli": c, "factusol_data": None} for c in self.codclis]


@router.post("/bulk-match/import-orphans/apply")
def import_orphans_apply(
    payload: ImportOrphansApplyPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Crea empresa (y contacto, si hay `EMACLI`) para los CODCLI marcados.

    Un CODCLI por transacción, y **siempre 200**: lo que falle va en `errors`
    con su CODCLI, para poder reintentar solo eso. Antes un `KO` de DELSOL en
    la relectura de F_CLI devolvía 502 y se llevaba el lote entero por delante
    (C-6-fix1)."""
    from app.integrations.factusol.import_orphans import (  # noqa: PLC0415
        apply_import_orphans,
    )

    operations = payload.as_operations()
    if not operations:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "no_operations", "detail": "No hay nada que importar.",
        })
    client, ejercicio = _client_and_ejercicio(session)
    # Sin `except FactusolError`: `apply_import_orphans` ya no lo deja escapar.
    # Un fallo de FACTUSOL afecta a las operaciones que lo necesiten, no al
    # lote; propagarlo como 502 tiraría también las que sí se han escrito.
    return apply_import_orphans(
        session, client, ejercicio=ejercicio, operations=operations,
        create_contacts_if_email=payload.create_contacts_if_email,
        actor_id=current_user.id,
    )


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


class CreateCrmAndLinkPayload(BaseModel):
    """Datos del cliente F_CLI con los que se crea la empresa CRM."""

    nombre: str = Field(min_length=1, max_length=255)
    nif: str = Field(default="", max_length=64)
    direccion: str = Field(default="", max_length=500)
    ciudad: str = Field(default="", max_length=200)
    cp: str = Field(default="", max_length=20)
    provincia: str = Field(default="", max_length=200)
    telefono: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)


class CreateCrmAndLinkIn(BaseModel):
    factusol_codcli: str = Field(min_length=1, max_length=36)
    factusol_customer_data: CreateCrmAndLinkPayload


@router.post("/customers/create-crm-and-link", status_code=201)
def create_crm_and_link(
    payload: CreateCrmAndLinkIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Crea la empresa CRM a partir de un cliente F_CLI **y la vincula, en una
    sola transacción** (C-3-fix3).

    Antes el frontend hacía dos llamadas (crear empresa → vincular). Si la
    segunda fallaba —p. ej. porque el CODCLI ya estaba vinculado— la empresa
    quedaba creada y **huérfana**: en producción aparecieron 3 empresas
    duplicadas de un mismo cliente por reintentos. Aquí se comprueba el
    vínculo ANTES de crear nada y todo va en una transacción: o hay empresa
    vinculada, o no hay empresa.
    """
    from app.integrations.factusol.customers import crm_links_for  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415

    codcli = payload.factusol_codcli.strip()
    # 1) ¿Ya está vinculado? → 409 SIN crear nada.
    taken = crm_links_for(session, [codcli]).get(codcli)
    if taken:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "already_linked",
            "detail": (
                f"El cliente FACTUSOL {codcli} ya está vinculado a "
                f"{taken['type']} «{taken['name']}» (id: {taken['id']}). "
                "Ve a esa ficha para gestionarlo."
            ),
        })

    data = payload.factusol_customer_data
    company = Company(
        name=data.nombre.strip(),
        tax_id=data.nif.strip() or None,
        address_line=data.direccion.strip() or None,
        city=data.ciudad.strip() or None,
        postal_code=data.cp.strip() or None,
        state=data.provincia.strip() or None,
        country="España",
        source="factusol",
        factusol_company_id=codcli,
        factusol_sync_source="erp_link",
    )
    try:
        # 2) Empresa + vínculo en la MISMA transacción: si algo falla, rollback
        # y no queda ninguna empresa huérfana.
        session.add(company)
        session.flush()
        _audit_customer_link(session, current_user, "company", company.id,
                             codcli, created=True)
        session.commit()
    except Exception as exc:  # noqa: BLE001 — integridad, BD caída, etc.
        session.rollback()
        logger.warning("factusol create-crm-and-link KO (rollback): %s", exc)
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "create_and_link_failed",
            "detail": f"No se pudo crear y vincular la empresa: {str(exc)[:200]}",
        }) from exc
    return {"company_id": company.id, "factusol_codcli": codcli, "created": True}


# --- proformas / presupuestos F_PRE (Fase C · C-4) ---------------------------
#
# F_PRE es MONO-LÍNEA (653 presupuestos verificados en la base real): cada fila
# es un presupuesto completo y no hay tabla de líneas. El desglose de las
# proformas que crea el CRM se guarda aparte, en `factusol_quote_lines_cache`.
# Ver `app/integrations/factusol/quotes.py` y `docs/erp/factusol-proformas.md`.


def _factusol_gateway_error(exc: Exception, code: str) -> HTTPException:
    logger.warning("factusol %s KO: %s", code, exc)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, {
        "code": code, "detail": str(exc)[:300],
    })


@router.get("/articles/search")
def search_articles_endpoint(
    q: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Busca artículos en F_ART por sus 6 identificadores.

    `precio_venta` viene de **F_LTA** (tarifa 1), no de F_ART: en F_ART solo
    está `PCOART`, que es el coste. `precio_venta_source` deja constancia de
    dónde salió cada precio; `null` = ese artículo no tiene tarifa 1
    configurada y el operador teclea el importe."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.quotes import search_articles  # noqa: PLC0415

    client, ejercicio = _client_and_ejercicio(session)
    try:
        items = search_articles(client, q, ejercicio=ejercicio)
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_article_search_failed") from exc
    return {"items": items, "ejercicio": ejercicio}


@router.get("/quotes")
def list_quotes_endpoint(
    company_id: str | None = Query(default=None),
    days_back: int = Query(default=180, ge=0, le=1825),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Proformas de una empresa del CRM (por su CODCLI vinculado).

    Sin `company_id` lista las de todos los clientes. Si la empresa existe pero
    **no está vinculada** a FACTUSOL devuelve lista vacía con
    `unlinked=True` — no es un error, es que aún no hay nada que enseñar."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.quotes import list_quotes  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415

    codcli: str | None = None
    if company_id:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, {
                "code": "company_not_found", "detail": "La empresa no existe.",
            })
        if not company.factusol_company_id:
            return {"items": [], "unlinked": True, "ejercicio": None}
        codcli = str(company.factusol_company_id)

    client, ejercicio = _client_and_ejercicio(session)
    try:
        items = list_quotes(client, ejercicio=ejercicio, codcli=codcli,
                            days_back=days_back)
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_quotes_failed") from exc
    return {"items": items, "unlinked": False, "ejercicio": ejercicio}


@router.get("/quotes/search")
def search_quotes_endpoint(
    q: str = Query(default="", max_length=120),
    days_back: int = Query(default=365, ge=0, le=1825),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Busca proformas de **cualquier cliente** para usarlas como plantilla.

    A diferencia de `GET /quotes`, aquí NO se filtra por empresa: el 80 % de las
    plantillas que reutiliza Bomedia son de otro cliente parecido («duplicar la
    de Laboratorios Duaner para Laboratorios Porta»). El texto casa contra
    referencia, nombre del cliente de origen y número de proforma.

    Se declara ANTES de `/quotes/{codpre}`: FastAPI casa por orden y «search»
    encajaría como CODPRE."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.quotes import list_quotes  # noqa: PLC0415

    client, ejercicio = _client_and_ejercicio(session)
    try:
        items = list_quotes(
            client, ejercicio=ejercicio, codcli=None, days_back=days_back,
            text=q, limit=limit,
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_quotes_search_failed") from exc
    return {"items": items, "ejercicio": ejercicio}


@router.get("/quotes/status/{job_id}")
def quote_job_status(
    job_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Estado del job de proforma para el polling del frontend.

    Va ANTES de `/quotes/{codpre}` a propósito: FastAPI casa las rutas por
    orden de declaración y `status` encajaría en `{codpre}`."""
    _ = current_user, session
    return _rq_quote_status(job_id)


def _rq_quote_status(job_id: str) -> dict[str, Any]:
    """Estado del job RQ (best-effort). Sin Redis (local/tests) → `pending`."""
    try:
        from redis import Redis  # noqa: PLC0415
        from rq.job import Job  # noqa: PLC0415

        from app.core.config import get_settings  # noqa: PLC0415

        conn = Redis.from_url(get_settings().redis_url)
        job = Job.fetch(job_id, connection=conn)
        rq_status = job.get_status(refresh=True)
        if rq_status == "failed":
            return _failed_status(job.exc_info or "")
        if rq_status == "finished":
            return {"status": "finished", "result": job.result}
        return {"status": "pending"}
    except Exception as exc:  # noqa: BLE001 — sin Redis o job caducado
        logger.debug("factusol quote job %s no consultable: %s", job_id, exc)
        return {"status": "pending"}


#: Marcador del rechazo por estado dentro del traceback que guarda RQ.
_NOT_EDITABLE_MARKER = "QuoteNotEditableError"


def _failed_status(exc_info: str) -> dict[str, Any]:
    """Traduce el fallo de un job a algo que el frontend pueda tratar.

    El PATCH responde 202, así que el rechazo por estado («la proforma está
    aceptada») llega por aquí y no como un 409. Se marca con un `code` propio
    para que la UI pueda ofrecer «Editar de todos modos» en vez de enseñar un
    error genérico — y NO lo haga cuando el fallo es otra cosa.
    """
    if _NOT_EDITABLE_MARKER in exc_info:
        # Última línea del traceback: «…QuoteNotEditableError: <mensaje>».
        last = exc_info.strip().splitlines()[-1]
        detail = last.split(": ", 1)[1] if ": " in last else last
        return {"status": "failed", "code": "quote_not_editable",
                "error": detail}
    return {"status": "failed", "error": (exc_info or "la operación falló")[-400:]}


@router.get("/quotes/{codpre}")
def get_quote_endpoint(
    codpre: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Una proforma con su desglose.

    `line_source` = `cache` (la creó el CRM, hay líneas reales) o `ref_text`
    (se creó en el escritorio FACTUSOL: solo existe el texto de REFPRE)."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.quotes import get_quote  # noqa: PLC0415

    client, ejercicio = _client_and_ejercicio(session)
    try:
        quote = get_quote(client, session, codpre, ejercicio=ejercicio)
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_quote_failed") from exc
    if quote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "quote_not_found",
            "detail": f"La proforma {codpre} no existe en el ejercicio {ejercicio}.",
        })
    return quote


class QuoteLineIn(BaseModel):
    codart: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=255)
    quantity: float = Field(default=1, gt=0)
    unit_price: float = Field(default=0, ge=0)
    discount_pct: float = Field(default=0, ge=0, le=100)
    iva_pct: float = Field(default=21, ge=0, le=100)


class QuoteAddressIn(BaseModel):
    """Dirección de envío elegida entre las que el cliente ya tiene en
    FACTUSOL. El CRM no las edita, solo escoge (C-4-fix6)."""

    direccion: str = Field(default="", max_length=255)
    ciudad: str = Field(default="", max_length=255)
    cp: str = Field(default="", max_length=20)
    provincia: str = Field(default="", max_length=255)
    pais: str = Field(default="", max_length=10)


class CreateQuotePayload(BaseModel):
    """Alta de proforma. El cliente sale de la empresa CRM vinculada; el
    operador solo elige líneas (o escribe una referencia libre)."""

    company_id: str = Field(min_length=1, max_length=36)
    #: «Su ref.» del documento: nº de pedido del cliente, obra, proyecto…
    referencia: str = Field(default="", max_length=250)
    lines: list[QuoteLineIn] = Field(default_factory=list)
    fecha: str | None = Field(default=None, max_length=10)
    fopfac: str | None = Field(default=None, max_length=10)
    #: Dirección alternativa; None → la de la empresa CRM.
    address: QuoteAddressIn | None = None


def _apply_address(
    customer: dict[str, Any], address: QuoteAddressIn | None,
) -> dict[str, Any]:
    """Sobrescribe la dirección del cliente con la elegida por el operador.

    Solo pisa los campos que vengan con valor: una dirección alternativa sin
    provincia no debe borrar la de la sede."""
    if address is None:
        return customer
    chosen = {k: v.strip() for k, v in address.model_dump().items() if v.strip()}
    return {**customer, **chosen}


def _customer_from_company(session: Session, company_id: str) -> dict[str, Any]:
    """Datos de cliente para la cabecera de F_PRE, tomados de la empresa CRM.
    409 si la empresa no está vinculada: sin CODCLI la proforma no tiene dueño
    en la contabilidad y acabaría huérfana."""
    from app.models.crm import Company  # noqa: PLC0415

    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "company_not_found", "detail": "La empresa no existe.",
        })
    if not company.factusol_company_id:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "company_not_linked",
            "detail": (
                f"«{company.name}» no está vinculada a un cliente de FACTUSOL. "
                "Vincúlala antes de crear la proforma."
            ),
        })
    return {
        "codcli": str(company.factusol_company_id),
        "nombre": company.name,
        "nif": company.tax_id or "",
        "direccion": company.address_line or "",
        "ciudad": company.city or "",
        "cp": company.postal_code or "",
        "provincia": company.state or "",
    }


@router.post("/quotes", status_code=202)
def create_quote_endpoint(
    payload: CreateQuotePayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola la creación de la proforma en `factusol:writes` (202 + job_id).

    Va por cola porque el CODPRE se calcula con `MAX+1` justo antes de escribir
    y el worker serializado (concurrency=1) es lo que impide que dos altas
    simultáneas se pisen la numeración."""
    from app.integrations.factusol.jobs import enqueue_create_quote  # noqa: PLC0415

    if not payload.lines and not payload.referencia.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "empty_quote",
            "detail": "Añade al menos una línea o escribe una referencia.",
        })
    customer = _apply_address(
        _customer_from_company(session, payload.company_id), payload.address,
    )
    job_id = enqueue_create_quote(
        customer,
        [line.model_dump() for line in payload.lines],
        payload.referencia.strip() or None,
        payload.fecha,
        payload.fopfac,
    )
    _audit_quote(session, current_user, "erp.factusol_quote_create",
                 payload.company_id, {"job_id": job_id,
                                      "lines": len(payload.lines)})
    session.commit()
    return {"job_id": job_id, "status": "queued"}


class UpdateQuotePayload(CreateQuotePayload):
    """Edición de proforma. Mismo cuerpo que el alta más `force`, que salta el
    guard de estado (ver `quotes.ESTPRE_PENDING`)."""

    force: bool = False


@router.patch("/quotes/{codpre}", status_code=202)
def update_quote_endpoint(
    codpre: str,
    payload: UpdateQuotePayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola la reescritura de la proforma (cabecera + líneas).

    El job comprueba `ESTPRE` antes de tocar nada: si la proforma no está
    pendiente, falla y el frontend ofrece reintentar con `force`. La
    comprobación va en el job, no aquí, porque entre el 202 y la escritura
    puede pasar tiempo — validarlo ahora no garantizaría nada."""
    from app.integrations.factusol.jobs import enqueue_update_quote  # noqa: PLC0415

    if not payload.lines and not payload.referencia.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "empty_quote",
            "detail": "Añade al menos una línea o escribe una referencia.",
        })
    customer = _apply_address(
        _customer_from_company(session, payload.company_id), payload.address,
    )
    job_id = enqueue_update_quote(
        codpre, customer, [line.model_dump() for line in payload.lines],
        payload.referencia.strip() or None, payload.force,
    )
    _audit_quote(session, current_user, "erp.factusol_quote_update", codpre,
                 {"job_id": job_id, "lines": len(payload.lines),
                  "force": payload.force})
    session.commit()
    return {"job_id": job_id, "status": "queued", "codpre": codpre}


@router.post("/quotes/{codpre}/duplicate", status_code=202)
def duplicate_quote_endpoint(
    codpre: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola la duplicación de una proforma (CODPRE nuevo, fecha de hoy)."""
    from app.integrations.factusol.jobs import enqueue_duplicate_quote  # noqa: PLC0415

    job_id = enqueue_duplicate_quote(codpre)
    _audit_quote(session, current_user, "erp.factusol_quote_duplicate",
                 codpre, {"job_id": job_id})
    session.commit()
    return {"job_id": job_id, "status": "queued", "source_codpre": codpre}


@router.post("/quotes/{codpre}/convert-to-order", status_code=202)
def convert_quote_endpoint(
    codpre: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola la conversión de la proforma en pedido manual del CRM.

    No escribe nada nuevo en FACTUSOL — ver el docstring de
    `quotes.convert_quote_to_order` para el porqué."""
    from app.integrations.factusol.jobs import (  # noqa: PLC0415
        enqueue_convert_quote_to_order,
    )

    job_id = enqueue_convert_quote_to_order(codpre, current_user.id)
    _audit_quote(session, current_user, "erp.factusol_quote_convert",
                 codpre, {"job_id": job_id})
    session.commit()
    return {"job_id": job_id, "status": "queued", "codpre": codpre}


def _audit_quote(
    session: Session, actor: User, action: str, target_id: str,
    metadata: dict[str, Any],
) -> None:
    from app.models.crm import AuditLog  # noqa: PLC0415

    session.add(AuditLog(
        actor_user_id=actor.id, action=action,
        target_type="factusol_quote", target_id=target_id,
        metadata_json=json.dumps(metadata),
    ))
