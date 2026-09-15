"""Limpieza de empresas del CRM contra FACTUSOL (FACTUSOL SIEMPRE solo
lectura). Tres operaciones, cada una en dos tiempos (plan → apply) para poder
verlas en seco antes de tocar nada:

1. Auto-vincular por NIF (`plan_links` / `apply_links`): empresas sin CODCLI
   guardado cuyo CIF / VAT normalizado casa con UN ÚNICO cliente de F_CLI →
   se guarda el CODCLI en el CRM. Si el NIF casa con VARIOS CODCLI no se
   vincula: va a revisión.
2. Rellenar NIF desde FACTUSOL (`plan_backfill` / `apply_backfill`): empresas
   vinculadas por CODCLI pero sin NIF en el CRM → se copia `NIFCLI` del
   cliente a `tax_id` (nunca al revés: FACTUSOL no se toca). Si el CRM ya
   tenía un NIF distinto no vacío no se pisa: va a revisión.
3. Archivar (reversible) (`plan_archive` / `apply_archive`): empresas que NO
   están en FACTUSOL (ni por CODCLI válido ni por NIF/VAT) y SIN negocio vivo
   (sin pedidos, proformas, tareas ni actividad/email reciente; un contacto
   suelto NO protege) → `is_archived=1`. Nunca se borra.

El cruce por NIF y la actividad viva se reutilizan del discovery
(`company_discovery`). La escritura la confirma el caller (commit).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.company_discovery import (
    build_report,
    factusol_customers,
    index_by_nif,
    nif_key,
)
from app.models.crm import Company

DEFAULT_ARCHIVE_REASON = "limpieza: no en FACTUSOL y sin negocio vivo"


# --- 1) auto-vincular por NIF ---------------------------------------------------------------


@dataclass
class LinkAction:
    company_id: str
    name: str
    keys: list[str]
    codcli: str
    factusol_nombre: str | None


@dataclass
class LinkReview:
    company_id: str
    name: str
    keys: list[str]
    codclis: list[str]
    reason: str


@dataclass
class LinkPlan:
    to_link: list[LinkAction] = field(default_factory=list)
    review: list[LinkReview] = field(default_factory=list)


def plan_links(session: Session, fcli_rows: list[dict[str, Any]]) -> LinkPlan:
    customers = factusol_customers(fcli_rows)
    by_nif = index_by_nif(customers)
    plan = LinkPlan()
    companies = session.scalars(
        select(Company).where(
            Company.factusol_company_id.is_(None), Company.is_archived.is_(False),
        ).order_by(Company.name.asc())
    ).all()
    for c in companies:
        keys = [k for k in dict.fromkeys(nif_key(v) for v in (c.tax_id, c.vat)) if k]
        matches = []
        for key in keys:
            for cust in by_nif.get(key, []):
                if cust not in matches:
                    matches.append(cust)
        if not matches:
            continue
        # Distintos CODCLI (con y sin ceros a la izquierda cuentan como uno).
        codclis = list(dict.fromkeys(m.codcli for m in matches))
        distinct = {str(int(x)) if x.isdigit() else x for x in codclis}
        if len(distinct) == 1:
            plan.to_link.append(LinkAction(
                company_id=c.id, name=c.name, keys=keys, codcli=codclis[0],
                factusol_nombre=matches[0].nombre,
            ))
        else:
            plan.review.append(LinkReview(
                company_id=c.id, name=c.name, keys=keys, codclis=codclis,
                reason="el NIF casa con varios CODCLI de FACTUSOL",
            ))
    return plan


def apply_links(session: Session, plan: LinkPlan, *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    n = 0
    for item in plan.to_link:
        c = session.get(Company, item.company_id)
        if c is None or c.factusol_company_id:
            continue
        c.factusol_company_id = item.codcli
        c.factusol_sync_source = "auto_link_nif"
        c.factusol_synced_at = now
        n += 1
    return n


# --- 2) rellenar NIF desde FACTUSOL ---------------------------------------------------------


@dataclass
class BackfillAction:
    company_id: str
    name: str
    codcli: str
    nif: str


@dataclass
class BackfillReview:
    company_id: str
    name: str
    codcli: str
    crm_nif: str
    factusol_nif: str
    reason: str


@dataclass
class BackfillPlan:
    to_fill: list[BackfillAction] = field(default_factory=list)
    review: list[BackfillReview] = field(default_factory=list)


def plan_backfill(session: Session, fcli_rows: list[dict[str, Any]]) -> BackfillPlan:
    customers = factusol_customers(fcli_rows)
    by_codcli: dict[str, Any] = {}
    for cust in customers:
        by_codcli[cust.codcli] = cust
        if cust.codcli.isdigit():
            by_codcli.setdefault(str(int(cust.codcli)), cust)
    plan = BackfillPlan()
    companies = session.scalars(
        select(Company).where(
            Company.factusol_company_id.is_not(None), Company.is_archived.is_(False),
        ).order_by(Company.name.asc())
    ).all()
    for c in companies:
        code = str(c.factusol_company_id or "").strip()
        cust = by_codcli.get(code) or (by_codcli.get(str(int(code))) if code.isdigit() else None)
        if cust is None or not cust.nif:
            continue
        crm_keys = {k for k in (nif_key(c.tax_id), nif_key(c.vat)) if k}
        if not crm_keys:
            plan.to_fill.append(BackfillAction(
                company_id=c.id, name=c.name, codcli=code, nif=cust.nif,
            ))
        elif cust.nif_key and cust.nif_key not in crm_keys:
            plan.review.append(BackfillReview(
                company_id=c.id, name=c.name, codcli=code,
                crm_nif=c.tax_id or c.vat or "", factusol_nif=cust.nif,
                reason="el CRM ya tiene otro NIF distinto (no se pisa)",
            ))
        # crm_keys ya contiene el NIF de FACTUSOL → nada que hacer.
    return plan


def apply_backfill(session: Session, plan: BackfillPlan) -> int:
    n = 0
    for item in plan.to_fill:
        c = session.get(Company, item.company_id)
        if c is None:
            continue
        # Solo si sigue sin NIF (idempotente).
        if (c.tax_id or "").strip() or (c.vat or "").strip():
            continue
        c.tax_id = item.nif
        n += 1
    return n


# --- 3) archivar (reversible) ---------------------------------------------------------------


@dataclass
class ArchiveCandidate:
    company_id: str
    name: str
    nif: str
    bucket: str
    reason: str


@dataclass
class ArchivePlan:
    candidates: list[ArchiveCandidate] = field(default_factory=list)
    buckets: dict[str, int] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.candidates)


def _bucket(has_nif: bool, alive_any: bool) -> str:
    if not has_nif:
        return "sin_nif_solo_contacto" if alive_any else "sin_nif_sin_nada"
    return "con_nif_solo_contacto" if alive_any else "con_nif_sin_nada"


def plan_archive(
    session: Session, fcli_rows: list[dict[str, Any]], *, recent_days: int = 180,
    now: datetime | None = None,
) -> ArchivePlan:
    """Candidatas a archivar: NO en FACTUSOL y sin negocio vivo (un contacto
    suelto NO protege). Desglose por bucket para el dry-run."""
    report = build_report(session, fcli_rows, recent_days=recent_days, now=now)
    plan = ArchivePlan(buckets={
        "sin_nif_sin_nada": 0, "sin_nif_solo_contacto": 0,
        "con_nif_sin_nada": 0, "con_nif_solo_contacto": 0,
    })
    for row in report.rows:
        # Ya archivada → no reentra (idempotente); en FACTUSOL o con negocio
        # vivo → se protege. `alive_strict` = negocio vivo sin contar contactos.
        if row.in_factusol or row.alive_strict:
            continue
        bucket = _bucket(bool(row.keys), row.alive_any)
        plan.buckets[bucket] += 1
        plan.candidates.append(ArchiveCandidate(
            company_id=row.company_id, name=row.name,
            nif=(row.tax_id or row.vat or ""), bucket=bucket,
            reason="no en FACTUSOL; " + (
                "solo contactos (no protege)" if row.alive_any else "sin ninguna actividad"
            ),
        ))
    return plan


def apply_archive(
    session: Session, plan: ArchivePlan, *, reason: str = DEFAULT_ARCHIVE_REASON,
    now: datetime | None = None,
) -> int:
    """Marca `is_archived` (NO borra). Idempotente: una empresa que ya no
    cumple la regla (le entró un pedido entre el plan y el apply) o ya está
    archivada, se salta."""
    now = now or datetime.now(UTC)
    n = 0
    for item in plan.candidates:
        c = session.get(Company, item.company_id)
        if c is None or c.is_archived:
            continue
        c.is_archived = True
        c.archived_at = now
        c.archived_reason = reason
        n += 1
    return n
