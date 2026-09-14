"""Company CRUD + contact-assignment endpoints.

Sprint Empresas. Mounted at `/api/companies` (list + detail + CRUD)
plus a focused `POST /api/contacts/{id}/assign-company` so the
contact-detail page can swap the assigned company without going
through a general-purpose PATCH.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

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
from app.services.vies import (
    check_vat_live,
    company_eu_vat,
    needs_vies_check,
    result_block,
    validate_company_vies,
    vies_state,
)

router = APIRouter(prefix="/api/companies", tags=["companies"])
logger = logging.getLogger(__name__)


def _nif_key(value: Any) -> str:
    """NIF / CIF / NIF-IVA comparable: sin espacios, puntos ni guiones, en
    mayúsculas (`b-64.113.590` ≡ `B64113590`)."""
    return re.sub(r"[\s.\-]", "", str(value or "")).upper()


def find_companies_by_nif(
    session: Session, *values: Any, exclude_id: str | None = None,
) -> list[Company]:
    """Empresas del CRM cuyo `tax_id` o `vat` coincide con alguno de los
    identificadores dados (comparación normalizada). Es el guard
    anti-duplicados del alta y del buscador de la Fase 2."""
    keys = {_nif_key(v) for v in values if _nif_key(v)}
    if not keys:
        return []

    def sql_key(column):  # noqa: ANN001, ANN202 — misma normalización, en SQL
        stripped = column
        for sep in (" ", ".", "-"):
            stripped = func.replace(stripped, sep, "")
        return func.upper(stripped)

    stmt = select(Company).where(or_(
        sql_key(Company.tax_id).in_(keys),
        sql_key(Company.vat).in_(keys),
    ))
    if exclude_id:
        stmt = stmt.where(Company.id != exclude_id)
    hits = list(session.scalars(stmt.order_by(Company.name.asc())))
    # Re-filtro en Python: `ilike` no normaliza puntos/guiones y `upper()`
    # no quita separadores; aquí manda la clave comparable.
    return [c for c in hits if _nif_key(c.tax_id) in keys or _nif_key(c.vat) in keys]


def _to_read(session: Session, row: Company) -> CompanyRead:
    """Hydrate a Company row into the API shape, including the
    JOIN-counted contacts_count so the list view doesn't need a
    follow-up roundtrip per row."""
    count = session.scalar(
        select(func.count(Contact.id)).where(Contact.company_id == row.id)
    ) or 0
    read = CompanyRead.model_validate(row)
    read.contacts_count = int(count)
    # Fase VIES: el estado interpretado para el NIF-IVA actual (si cambió el
    # NIF-IVA desde la validación, vuelve a «pendiente»).
    read.vies = vies_state(row)
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
    row.language = (payload.language or "").strip().lower() or None
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
    # PR-Fix-Filtros-Lista-Cortada. Cap subido de 200 a 5000 — pickers
    # como CustomFieldSelector (workflow filter builder) hidrataban
    # toda la lista de empresas para resolver el dropdown del field
    # `company_ref` y se quedaban cortados en 200. Tenants con &gt;200
    # empresas perdían el resto del alfabeto. Para listados con
    # autocomplete (CompanyPickerModal) ya viene con `q`, así que el
    # cap alto no añade coste — esos pasan `limit=10`.
    limit: int = Query(default=50, ge=1, le=5000),
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
                # Fase 2 (buscador unificado): el NIF-IVA también identifica.
                Company.vat.ilike(like),
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
    # Fase 2: no crear dos empresas con el mismo NIF / NIF-IVA. El buscador y
    # «Crear empresa» avisan antes; esto es la red de seguridad. Se devuelve
    # la existente para que la UI ofrezca usarla.
    clash_nif = find_companies_by_nif(session, payload.tax_id, payload.vat)
    if clash_nif:
        existing = clash_nif[0]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_tax_id",
                "detail": (
                    f"Ya existe una empresa con ese NIF: «{existing.name}»"
                    + (f" (FACTUSOL nº {existing.factusol_company_id})"
                       if existing.factusol_company_id else "")
                    + ". Usa la existente en vez de crear otra."
                ),
                "existing_company_id": existing.id,
                "existing_company_name": existing.name,
            },
        )
    row = Company(name=payload.name)
    _apply(row, payload)
    session.add(row)
    # Fase VIES: empresa de la UE con NIF-IVA → se valida al vuelo (timeout
    # corto; si VIES no responde queda «desconocido» y el alta sigue).
    vies = validate_company_vies(session, row)
    record_event(
        session,
        action=Action.COMPANY_CREATED,
        target_type="company",
        target_id=row.id,
        actor=current_user,
        metadata={"name": payload.name, **({"vies": vies["status"]} if vies else {})},
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


@router.get("/fiscal-check")
def fiscal_check(
    tax_id: str | None = Query(default=None, max_length=64),
    vat: str | None = Query(default=None, max_length=40),
    country: str | None = Query(default=None, max_length=120),
    exclude_id: str | None = Query(default=None, max_length=36),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> dict[str, Any]:
    """Fase 2 · «Crear empresa»: lo que la pantalla necesita saber de los
    datos fiscales ANTES de guardar, en una sola llamada.

    - `regime`: el régimen de IVA que saldría de país + NIF-IVA (la misma
      regla que fija `IFICLI`/`IVACLI`/`TIVCLI` al crear el cliente F_CLI).
    - `duplicates.crm`: empresas del CRM con ese NIF / NIF-IVA.
    - `duplicates.factusol`: cliente de F_CLI con ese NIF (best-effort: si
      FACTUSOL no responde, `factusol_checked=False` y se sigue; nunca
      bloquea el alta).
    - `vies` (Fase VIES): validación del NIF-IVA en el servicio oficial de la
      UE cuando el país es de la UE (no España) y hay NIF-IVA: `status`
      `valido` / `no_valido` / `desconocido` (VIES no respondió) /
      `pendiente` (VIES desactivado), `valid`, `checked_at`, nombre y
      dirección según VIES. El régimen ya tiene en cuenta el veredicto: con
      `no_valido` NO se puede eximir → nacional con IVA. Con `exclude_id`
      (la ficha) se reutiliza el resultado guardado en esa empresa si es
      reciente y del mismo NIF-IVA; si no, consulta en vivo (cacheada,
      timeout corto, nunca bloquea).
    """
    _ = current_user
    from app.erp.language import normalize_country  # noqa: PLC0415
    from app.integrations.factusol.vat_regime import (  # noqa: PLC0415
        EU_ISO2,
        REGIME_LABELS,
        eu_vat_for,
        normalize_vat,
        regime_for,
        regime_reason,
    )

    tax = (tax_id or "").strip()
    vat_raw = (vat or "").strip()
    iso2 = normalize_country(country) if (country or "").strip() else None

    # VIES: solo UE fuera de España y con NIF-IVA del país.
    eu_vat = (
        eu_vat_for(iso2, vat=vat_raw or None, nif=tax or None)
        if iso2 and iso2 != "ES" and iso2 in EU_ISO2 else None
    )
    vies_block: dict[str, Any] = result_block(None, vat=eu_vat)
    if eu_vat:
        own = session.get(Company, exclude_id) if exclude_id else None
        if own is not None and company_eu_vat(own) == eu_vat and not needs_vies_check(own):
            vies_block = {**vies_state(own), "error": None}
        else:
            vies_block = result_block(check_vat_live(eu_vat), vat=eu_vat)
    vies_valid = vies_block["valid"]

    regime = regime_for(iso2, vat=vat_raw or None, nif=tax or None, vies_valid=vies_valid)
    crm = find_companies_by_nif(session, tax, vat_raw, exclude_id=exclude_id)

    factusol: dict[str, Any] | None = None
    factusol_checked = False
    factusol_error: str | None = None
    nifs = [v for v in (tax, normalize_vat(vat_raw) or vat_raw) if v]
    if nifs:
        try:
            from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415
            from app.integrations.factusol.customers import find_by_nif  # noqa: PLC0415
            from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

            client = FactusolClient.from_settings()
            ejercicio = ejercicio_for(session)
            for nif in dict.fromkeys(nifs):
                hit = find_by_nif(client, nif, ejercicio=ejercicio)
                if hit and hit.get("codcli"):
                    factusol = {
                        "codcli": str(hit["codcli"]),
                        "nombre": hit.get("nombre"),
                        "nif": hit.get("nif"),
                    }
                    break
            factusol_checked = True
        except Exception as exc:  # noqa: BLE001 — sin credenciales / DELSOL caído
            factusol_error = str(exc)[:200]
            logger.info("fiscal-check: FACTUSOL no disponible: %s", factusol_error)

    return {
        "country_iso2": iso2,
        "in_eu": bool(iso2 and iso2 in EU_ISO2),
        "regime": regime,
        "regime_label": REGIME_LABELS[regime],
        "regime_reason": regime_reason(
            iso2, vat=vat_raw or None, nif=tax or None, vies_valid=vies_valid,
        ),
        "vat_normalized": normalize_vat(vat_raw) if vat_raw else None,
        "duplicates": {
            "crm": [
                {
                    "id": c.id, "name": c.name, "tax_id": c.tax_id, "vat": c.vat,
                    "country": c.country,
                    "factusol_company_id": c.factusol_company_id,
                }
                for c in crm
            ],
            "factusol": factusol,
            "factusol_checked": factusol_checked,
            "factusol_error": factusol_error,
        },
        "vies": vies_block,
    }


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
    # Fase VIES: si cambió el NIF-IVA (o toca revalidar) se consulta VIES;
    # con el mismo NIF-IVA y un resultado reciente no se vuelve a llamar.
    vies = validate_company_vies(session, row)
    record_event(
        session,
        action=Action.COMPANY_UPDATED,
        target_type="company",
        target_id=row.id,
        actor=current_user,
        metadata={"vies": vies["status"]} if vies else None,
    )
    session.commit()
    session.refresh(row)
    return _to_read(session, row)


@router.post("/{company_id}/vies-revalidate")
def revalidate_company_vies(
    company_id: str,
    force: bool = Query(default=True, description="Salta la caché y consulta VIES ya"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Fase VIES · «Revalidar en VIES»: consulta el NIF-IVA de la empresa en
    el servicio oficial de la UE y guarda el resultado en la empresa.

    - `force=true` (botón): siempre consulta, saltando la caché.
    - `force=false` (la ficha al cargar): solo si no hay resultado, cambió el
      NIF-IVA, o el resultado es viejo (30 días; «desconocido» → 1 hora).

    Devuelve el bloque `vies`, el régimen que sale con ese veredicto y la
    empresa actualizada. Si la empresa no es de la UE o no tiene NIF-IVA,
    `vies.applies=False` (VIES no aplica: exportación / nacional).
    """
    from app.erp.language import normalize_country  # noqa: PLC0415
    from app.integrations.factusol.vat_regime import (  # noqa: PLC0415
        REGIME_LABELS,
        regime_for,
        regime_reason,
    )

    row = session.get(Company, company_id)
    if row is None:
        raise not_found("Company")
    before = row.vies_status
    vies = validate_company_vies(session, row, force=force)
    if vies is not None and (force or vies["status"] != before):
        record_event(
            session,
            action=Action.COMPANY_UPDATED,
            target_type="company",
            target_id=row.id,
            actor=current_user,
            metadata={"vies": vies["status"], "vies_vat": vies["vat"], "forced": force},
        )
        session.commit()
        session.refresh(row)
    state = vies if vies is not None else vies_state(row)
    iso2 = normalize_country(row.country) if row.country else None
    regime = regime_for(iso2, vat=row.vat, nif=row.tax_id, vies_valid=state["valid"])
    return {
        "vies": state,
        "regime": regime,
        "regime_label": REGIME_LABELS[regime],
        "regime_reason": regime_reason(
            iso2, vat=row.vat, nif=row.tax_id, vies_valid=state["valid"],
        ),
        "company": _to_read(session, row).model_dump(mode="json"),
    }


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

from typing import Literal  # noqa: PLC0415, E402

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
