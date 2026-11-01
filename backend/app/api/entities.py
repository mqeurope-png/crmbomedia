"""Unified entity filter/column schema + generic list/search endpoints.

PR-A shipped the schema surface. PR-B adds the generic list endpoint
`POST /api/entities/{entity}/search` (returns `{items, total, limit,
offset}`) and the matching id-enumeration sibling
`POST /api/entities/{entity}/search/ids` for "select all filtered"
flows. Both reuse `build_entity_filter` so the engine's anti-injection
boundary, NOT-with-arbitrary-nesting, and 24 comparators carry over
for free.

Sort keys are resolved through `EntityDescriptor.sort_column`, which
extends the whitelist to ordering (a caller can't sort on an unknown
column or one a spec marked non-sortable).

Contact stays on the legacy `POST /api/contacts/search` for now —
PR-E retires that one when the contacts UI migrates. The generic
endpoint works for every entity (including contact) but doesn't
implement the contact-specific extras (`assigned_to_me`, full
`ContactRead` projection with `tag_objects`); those happen in PR-E.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.auth import ROLE_LEVELS, require_viewer
from app.core.errors import forbidden, not_found
from app.db.session import get_session
from app.models.crm import User, UserRole
from app.services.entities import (
    get_entity,
    list_entities,
    list_fields_for_entity,
)
from app.services.segments import columnas_empresa, un_por_empresa
from app.services.segments.engine import SegmentRuleError, build_entity_filter

router = APIRouter(prefix="/api/entities", tags=["entities"])

# Sprint Filtros & Listas — keep the "select all filtered" id-list cap
# in lock-step with the legacy `/api/contacts/search/ids` constant.
MAX_IDS = 10_000


# --- schemas -----------------------------------------------------


class EntitySearchRequest(BaseModel):
    rules_json: dict[str, Any] | None = None
    sort_by: str | None = None
    sort_dir: str = "desc"
    limit: int = Field(default=25, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    #: Limpieza de empresas: por defecto se OCULTAN las archivadas (entidades
    #: con `is_archived`). El toggle «Ver archivadas» del listado lo pone True.
    include_archived: bool = False
    #: 1/11/2026 (contactos): un solo contacto por empresa, el primero según
    #: `orden` (ver `services/segments/un_por_empresa.py`).
    uno_por_empresa: bool = False
    orden: list[str] | None = None


class EntitySearchPage(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
    #: Contactos: cuántos cumplen, de cuántas empresas y cuántos sin empresa
    #: (con «uno por empresa», esos quedan fuera).
    recuentos: dict[str, int] | None = None


class EntitySearchIdsResult(BaseModel):
    ids: list[str]
    count: int
    truncated: bool
    max_ids: int


# --- schema endpoints --------------------------------------------


@router.get("")
def list_registered_entities(
    current_user: User = Depends(require_viewer),
) -> list[dict[str, str]]:
    """List the entities that expose a unified filter/column schema."""
    _ = current_user
    out: list[dict[str, str]] = []
    for key in list_entities():
        descriptor = get_entity(key)
        if descriptor is None:  # pragma: no cover - registry is static
            continue
        out.append({"key": descriptor.key, "label": descriptor.label})
    return out


@router.get("/{entity}/filter-schema")
def entity_filter_schema(
    entity: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> dict[str, Any]:
    """Declarative field schema for one entity: drives the filter
    builder (filterable fields + comparators + enum/reference hints) and
    the column configurator (displayable + default_visible + grouped_under).
    """
    _ = current_user
    fields = list_fields_for_entity(entity)
    if fields is None:
        raise not_found("Entity")
    descriptor = get_entity(entity)
    assert descriptor is not None  # list_fields_for_entity already checked
    # Las opciones que salen de los datos (gamas, modelos, familias, orígenes).
    from app.services.segments.opciones import rellenar  # noqa: PLC0415

    fields = rellenar(session, fields, dict(descriptor.field_specs))
    return {
        "entity": descriptor.key,
        "label": descriptor.label,
        "default_sort": descriptor.default_sort,
        "default_sort_dir": descriptor.default_sort_dir,
        "fields": fields,
    }


# --- search endpoints --------------------------------------------


def _resolve_sort(descriptor: Any, payload: EntitySearchRequest):
    """Resolve `(sort_by, sort_dir)` against the entity whitelist.
    Returns (column, dir) or raises 400 on an unknown/non-sortable key —
    the same anti-injection rule that the engine applies to filter
    fields, lifted to ordering."""
    sort_by = payload.sort_by or descriptor.default_sort
    sort_dir = (payload.sort_dir or descriptor.default_sort_dir).lower()
    if sort_dir not in {"asc", "desc"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort_dir {payload.sort_dir!r}",
        )
    column = descriptor.sort_column(sort_by)
    if column is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown or non-sortable field {sort_by!r}",
        )
    return column, sort_dir


def _visibility_clauses(descriptor: Any, *, include_archived: bool) -> list[Any]:
    """Filtros de visibilidad por defecto de la entidad. Hoy: las empresas
    archivadas quedan fuera salvo `include_archived` (toggle «Ver
    archivadas»). Genérico: cualquier modelo con `is_archived`."""
    col = getattr(descriptor.base_model, "is_archived", None)
    if col is not None and not include_archived:
        return [col.is_(False)]
    return []


#: Entidades cuya pantalla propia exige ser Usuario (no Solo lectura): el
#: correo (`/api/emails/*`) y el marketing (`/api/brevo/*`). La búsqueda
#: genérica no puede abrir más que su pantalla (auditoría de permisos
#: 30/10/2026, F-1).
_ENTIDADES_DE_USUARIO = frozenset({"email_thread", "brevo_template", "brevo_campaign"})


def _alcance_del_usuario(
    descriptor: Any, session: Session, user: User,
) -> list[Any]:
    """Lo que este usuario puede ver de la entidad, como en su pantalla.
    El correo, con la misma regla que el buzón y la ficha de contacto
    (`thread_visibility_filter`): el admin todo; el resto, sus hilos. Sin
    esto, cualquier rol del CRM leía los asuntos de todos los buzones."""
    if descriptor.key in _ENTIDADES_DE_USUARIO and (
        ROLE_LEVELS[user.role] < ROLE_LEVELS[UserRole.USER]
    ):
        raise forbidden()
    if descriptor.key == "email_thread":
        from app.services.email_aliases import thread_visibility_filter  # noqa: PLC0415

        filtro = thread_visibility_filter(session, user)
        return [] if filtro is None else [filtro]
    return []


def _segment_resolver_for(session: Session):
    """Closure factory for `in_segment` field. Sprint Filtros & Listas
    (PR-Cf): el motor levanta `SegmentRuleError("in_segment requires a
    segment_resolver")` si una rule usa `in_segment` y nadie pasa el
    resolver. El endpoint legacy `/api/contacts/search` ya lo hace —
    el genérico `/api/entities/{entity}/search` también debe.

    El resolver es Contact-only en la práctica (los segmentos son un
    concepto de contactos), pero pasarlo a cualquier entidad es
    inocuo porque sólo se invoca cuando una rule lo necesita.
    """
    from app.services.segments.engine import session_segment_resolver  # noqa: PLC0415

    return session_segment_resolver(session)


@router.post("/{entity}/search")
def entity_search(
    entity: str,
    payload: EntitySearchRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> EntitySearchPage:
    """Generic paginated list. The body's `rules_json` is the engine's IR
    tree (same shape as `POST /api/contacts/search`); empty body returns
    the entity universe (what this user may see of it)."""
    descriptor = get_entity(entity)
    if descriptor is None:
        raise not_found("Entity")

    try:
        clause = build_entity_filter(
            descriptor,
            payload.rules_json or {},
            segment_resolver=_segment_resolver_for(session),
        )
    except SegmentRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    visibility = [
        *_visibility_clauses(descriptor, include_archived=payload.include_archived),
        *_alcance_del_usuario(descriptor, session, current_user),
    ]
    column, sort_dir = _resolve_sort(descriptor, payload)
    order_by = column.asc() if sort_dir == "asc" else column.desc()

    es_contacto = descriptor.key == "contact"
    recuentos = None
    if es_contacto:
        base = and_(clause, *visibility)
        recuentos = un_por_empresa.recuentos(session, base)
        clause, visibility = un_por_empresa.filtro(
            base, uno_por_empresa=payload.uno_por_empresa, orden=payload.orden), []

    if es_contacto and payload.uno_por_empresa:
        # Uno por empresa: tantos como empresas con algún contacto que cumple
        # (ya contado en los recuentos; no hace falta otra pasada).
        total = recuentos["empresas"]
    else:
        total = session.scalar(
            select(func.count()).select_from(descriptor.base_model).where(clause, *visibility)
        ) or 0

    stmt = (
        select(descriptor.base_model)
        .where(clause, *visibility)
        .order_by(order_by, descriptor.id_column)
        .offset(payload.offset)
        .limit(payload.limit)
    )
    # PR-Cd: apply per-entity loader paths so serialize_row doesn't N+1
    # on every list expansion (Contact's tag_objects is the obvious
    # one; other entities have an empty `eager_load_paths`). Each path
    # walks `(rel_name, rel_name, ...)` and resolves each step against
    # the previous step's target mapper so multi-hop chains
    # (`tag_assignments → tag`) become
    # `selectinload(Contact.tag_assignments).selectinload(ContactTag.tag)`.
    for path in descriptor.eager_load_paths:
        loader = None
        current_model = descriptor.base_model
        for attr_name in path:
            attr = getattr(current_model, attr_name)
            loader = (
                selectinload(attr)
                if loader is None
                else loader.selectinload(attr)
            )
            rel = current_model.__mapper__.relationships.get(attr_name)
            if rel is not None:
                current_model = rel.mapper.class_
        if loader is not None:
            stmt = stmt.options(loader)
    rows = list(session.scalars(stmt).unique())
    items = [descriptor.serialize_row(row) for row in rows]
    if es_contacto:
        # Las cifras del ERP (parque, gasto, compras), a partir de usuario,
        # como la ficha de empresa; al lector, solo nombre y sector.
        columnas_empresa.rellenar(session, items, con_erp=(
            ROLE_LEVELS[current_user.role] >= ROLE_LEVELS[UserRole.USER]))
        if payload.uno_por_empresa:
            por = un_por_empresa.motivos(session, base, payload.orden,
                                         [i["id"] for i in items])
            for i in items:
                i["contacto_elegido_por"] = (por.get(i["id"]) or {}).get("texto")
    return EntitySearchPage(
        items=items, total=int(total), limit=payload.limit, offset=payload.offset,
        recuentos=recuentos,
    )


@router.post("/{entity}/search/ids")
def entity_search_ids(
    entity: str,
    payload: EntitySearchRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> EntitySearchIdsResult:
    """Return matching ids only — feeds the "select all filtered" banner.
    Capped at MAX_IDS; truncation is reported back so the UI can warn
    the operator. (PR-C lands the proper set-based bulk that doesn't
    enumerate ids at all.)"""
    descriptor = get_entity(entity)
    if descriptor is None:
        raise not_found("Entity")

    try:
        clause = build_entity_filter(
            descriptor,
            payload.rules_json or {},
            segment_resolver=_segment_resolver_for(session),
        )
    except SegmentRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    visibility = [
        *_visibility_clauses(descriptor, include_archived=payload.include_archived),
        *_alcance_del_usuario(descriptor, session, current_user),
    ]
    if descriptor.key == "contact" and payload.uno_por_empresa:
        clause, visibility = un_por_empresa.clausula(
            and_(clause, *visibility), payload.orden), []
    rows = list(
        session.scalars(
            select(descriptor.id_column).where(clause, *visibility).limit(MAX_IDS + 1)
        )
    )
    truncated = len(rows) > MAX_IDS
    ids = [str(r) for r in rows[:MAX_IDS]]
    return EntitySearchIdsResult(
        ids=ids,
        count=len(ids),
        truncated=truncated,
        max_ids=MAX_IDS,
    )
