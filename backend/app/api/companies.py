"""Company CRUD + contact-assignment endpoints.

Sprint Empresas. Mounted at `/api/companies` (list + detail + CRUD)
plus a focused `POST /api/contacts/{id}/assign-company` so the
contact-detail page can swap the assigned company without going
through a general-purpose PATCH.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.audit import Action, record_event
from app.core.auth import require_admin, require_user, require_viewer
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import Company, Contact, User
from app.schemas.companies import (
    CompanyAssignPayload,
    CompanyList,
    CompanyRead,
    CompanyWrite,
)

router = APIRouter(prefix="/api/companies", tags=["companies"])


def _to_read(session: Session, row: Company) -> CompanyRead:
    """Hydrate a Company row into the API shape, including the
    JOIN-counted contacts_count so the list view doesn't need a
    follow-up roundtrip per row."""
    count = session.scalar(
        select(func.count(Contact.id)).where(Contact.company_id == row.id)
    ) or 0
    read = CompanyRead.model_validate(row)
    read.contacts_count = int(count)
    return read


def _apply(row: Company, payload: CompanyWrite) -> None:
    """Snapshot-style update — clears NULL when the operator
    explicitly sends an empty value. Matches the v2.4d draft
    semantics so the helper feels familiar."""
    row.name = payload.name
    row.website = payload.website
    row.domain = payload.domain
    row.tax_id = payload.tax_id
    row.vat = payload.vat
    row.country = payload.country
    row.region = payload.region
    row.state = payload.state
    row.city = payload.city
    row.address_line = payload.address_line
    row.postal_code = payload.postal_code
    row.sector = payload.sector
    row.size_category = payload.size_category
    row.notes = payload.notes
    row.source = payload.source
    row.external_references_json = (
        json.dumps(payload.external_references)
        if payload.external_references
        else None
    )
    row.custom_fields_json = (
        json.dumps(payload.custom_fields) if payload.custom_fields else None
    )


@router.get("", response_model=CompanyList)
def list_companies(
    q: str | None = Query(default=None, description="LIKE on name/domain/tax_id"),
    country: str | None = Query(default=None),
    source: str | None = Query(default=None),
    has_contacts: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> CompanyList:
    _ = current_user
    stmt = select(Company)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Company.name.ilike(like),
                Company.domain.ilike(like),
                Company.tax_id.ilike(like),
            )
        )
    if country:
        stmt = stmt.where(Company.country == country)
    if source:
        stmt = stmt.where(Company.source == source)
    if has_contacts is True:
        contact_q = select(Contact.company_id).where(
            Contact.company_id.is_not(None)
        )
        stmt = stmt.where(Company.id.in_(contact_q))
    elif has_contacts is False:
        contact_q = select(Contact.company_id).where(
            Contact.company_id.is_not(None)
        )
        stmt = stmt.where(~Company.id.in_(contact_q))

    total = int(
        session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    )
    items = list(
        session.scalars(
            stmt.order_by(Company.name.asc()).offset(offset).limit(limit)
        )
    )
    return CompanyList(
        items=[_to_read(session, c) for c in items],
        total=total,
    )


@router.post("", response_model=CompanyRead, status_code=201)
def create_company(
    payload: CompanyWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> CompanyRead:
    if payload.domain:
        clash = session.scalar(
            select(Company).where(Company.domain == payload.domain)
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Ya existe una empresa con ese dominio. "
                    f"Edita la existente ({clash.name})."
                ),
            )
    row = Company(name=payload.name)
    _apply(row, payload)
    session.add(row)
    record_event(
        session,
        action=Action.COMPANY_CREATED,
        target_type="company",
        target_id=row.id,
        actor=current_user,
        metadata={"name": payload.name},
    )
    session.commit()
    session.refresh(row)
    return _to_read(session, row)


@router.get("/count")
def count_companies(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> dict[str, int]:
    """Legacy stat-card endpoint preserved across the v2 rewrite.
    The dashboard's "Empresas" KPI reads this; we keep the
    response shape (`{total: N}`) so the frontend doesn't have to
    change. Counts only active rows, mirroring the legacy
    handler."""
    _ = current_user
    total = int(
        session.scalar(
            select(func.count()).select_from(Company).where(
                Company.is_active.is_(True)
            )
        )
        or 0
    )
    return {"total": total}


@router.get("/{company_id}", response_model=CompanyRead)
def get_company(
    company_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> CompanyRead:
    _ = current_user
    row = session.get(Company, company_id)
    if row is None:
        raise not_found("Company")
    return _to_read(session, row)


@router.put("/{company_id}", response_model=CompanyRead)
def update_company(
    company_id: str,
    payload: CompanyWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> CompanyRead:
    row = session.get(Company, company_id)
    if row is None:
        raise not_found("Company")
    # Domain uniqueness: only enforce when changing AND new value
    # isn't NULL — otherwise the UNIQUE constraint takes over.
    if payload.domain and payload.domain != row.domain:
        clash = session.scalar(
            select(Company).where(
                Company.domain == payload.domain, Company.id != company_id
            )
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe otra empresa con ese dominio.",
            )
    _apply(row, payload)
    record_event(
        session,
        action=Action.COMPANY_UPDATED,
        target_type="company",
        target_id=row.id,
        actor=current_user,
    )
    session.commit()
    session.refresh(row)
    return _to_read(session, row)


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company(
    company_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> Response:
    row = session.get(Company, company_id)
    if row is None:
        raise not_found("Company")
    record_event(
        session,
        action=Action.COMPANY_DELETED,
        target_type="company",
        target_id=row.id,
        actor=current_user,
        metadata={"name": row.name},
    )
    session.delete(row)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{company_id}/merge/{target_id}", response_model=CompanyRead
)
def merge_companies(
    company_id: str,
    target_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> CompanyRead:
    """Merge `company_id` (source) into `target_id` (kept). Every
    contact pointing at the source flips to the target; the
    source is then deleted. Admin-only because the operation is
    not reversible without a backup."""
    if company_id == target_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes fusionar una empresa consigo misma.",
        )
    source = session.get(Company, company_id)
    target = session.get(Company, target_id)
    if source is None or target is None:
        raise not_found("Company")
    session.execute(
        Contact.__table__.update()  # type: ignore[attr-defined]
        .where(Contact.company_id == source.id)
        .values(company_id=target.id)
    )
    record_event(
        session,
        action=Action.COMPANY_DELETED,
        target_type="company",
        target_id=source.id,
        actor=current_user,
        metadata={
            "merged_into": target.id,
            "source_name": source.name,
        },
    )
    session.delete(source)
    session.commit()
    session.refresh(target)
    return _to_read(session, target)


@router.get("/{company_id}/contacts")
def list_company_contacts(
    company_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[dict]:
    _ = current_user
    row = session.get(
        Company,
        company_id,
    )
    if row is None:
        raise not_found("Company")
    rows = list(
        session.scalars(
            select(Contact)
            .where(Contact.company_id == company_id)
            .order_by(Contact.last_name.asc(), Contact.first_name.asc())
            .options(selectinload(Contact.company))
        )
    )
    return [
        {
            "id": c.id,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "email": c.email,
            "phone": c.phone,
            "commercial_status": c.commercial_status,
            "owner_user_id": c.owner_user_id,
        }
        for c in rows
    ]


# --- contact-side endpoint ------------------------------------------

assign_router = APIRouter(prefix="/api/contacts", tags=["contacts"])


@assign_router.post(
    "/{contact_id}/assign-company", response_model=dict
)
def assign_company_to_contact(
    contact_id: str,
    payload: CompanyAssignPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict:
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise not_found("Contact")
    if payload.company_id is not None:
        company = session.get(Company, payload.company_id)
        if company is None:
            raise not_found("Company")
    previous = contact.company_id
    contact.company_id = payload.company_id
    record_event(
        session,
        action=Action.CONTACT_UPDATED,
        target_type="contact",
        target_id=contact.id,
        actor=current_user,
        metadata={
            "field": "company_id",
            "from": previous,
            "to": payload.company_id,
        },
    )
    session.commit()
    return {
        "contact_id": contact.id,
        "company_id": contact.company_id,
        "updated_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Bulk actions (Sprint Filtros & Listas — PR-F).
# ---------------------------------------------------------------------------

from typing import Any, Literal  # noqa: PLC0415, E402

from pydantic import BaseModel, Field  # noqa: PLC0415, E402

#: Cap defensivo de filas por llamada — espejo de
#: `MAX_BULK_CONTACTS` en `app/api/bulk.py` (Sprint A).
MAX_BULK_COMPANIES = 1000


CompanyBulkAction = Literal["activate", "deactivate", "change_sector"]


class CompanyBulkPayload(BaseModel):
    company_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_COMPANIES)
    action: CompanyBulkAction
    payload: dict[str, Any] = Field(default_factory=dict)


class CompanyBulkResult(BaseModel):
    action: CompanyBulkAction
    affected_count: int
    company_ids: list[str]


@router.post("/bulk-action", response_model=CompanyBulkResult)
def bulk_company_action(
    payload: CompanyBulkPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> CompanyBulkResult:
    """Dispatch genérico para acciones masivas desde la lista de
    empresas (PR-F). Las acciones soportadas hoy son las más
    útiles según la auditoría:

    - `activate` / `deactivate` — flag `is_active`. Admin / manager /
      user pueden hacerlo (no es destructivo).
    - `change_sector` — espera `payload.sector` y lo aplica en bulk.
      Útil para etiquetar empresas tras una importación masiva sin
      necesidad de PATCH una a una.

    `delete` queda fuera del set por v1 — el delete individual sigue
    siendo admin-only en `/api/companies/{id}` y el flujo de borrado
    masivo merece más diseño (qué hacer con los contacts asociados).
    """
    if len(payload.company_ids) > MAX_BULK_COMPANIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {MAX_BULK_COMPANIES} companies per bulk call.",
        )

    rows = list(
        session.scalars(
            select(Company).where(Company.id.in_(payload.company_ids))
        )
    )

    affected: list[str] = []
    metadata: dict[str, Any] = {"action": payload.action}

    if payload.action in ("activate", "deactivate"):
        target = payload.action == "activate"
        for row in rows:
            if row.is_active != target:
                row.is_active = target
                affected.append(row.id)
        metadata["is_active"] = target
    elif payload.action == "change_sector":
        sector = payload.payload.get("sector")
        if not isinstance(sector, str) or not sector.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="`payload.sector` is required for change_sector.",
            )
        sector = sector.strip()
        for row in rows:
            if row.sector != sector:
                row.sector = sector
                affected.append(row.id)
        metadata["sector"] = sector
    else:  # pragma: no cover — Literal exhausts this
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown action {payload.action!r}",
        )

    metadata["requested"] = len(payload.company_ids)
    metadata["affected"] = len(affected)
    record_event(
        session,
        action=Action.COMPANY_BULK_ACTION,
        target_type="company",
        target_id=None,
        actor=current_user,
        metadata=metadata,
    )
    session.commit()
    return CompanyBulkResult(
        action=payload.action,
        affected_count=len(affected),
        company_ids=affected,
    )
