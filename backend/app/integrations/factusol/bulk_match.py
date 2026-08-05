"""Conciliación masiva CRM ↔ FACTUSOL (Fase C · C-5).

El CRM arrastra miles de empresas de fuentes heterogéneas (WooCommerce, el
AgileCRM viejo, imports manuales, formularios web) con datos incompletos,
mal escritos o duplicados. `F_CLI` tiene 4533 clientes con NIF válido,
dirección fiscal correcta y contabilidad al día: es la fuente limpia.

Flujo en dos tiempos, **nada se toca sin confirmación**:

1. `dry_run` — propone parejas y enseña las diferencias campo a campo.
2. `apply_operations` — sobrescribe SOLO las empresas y campos que el operador
   marque, guardando antes los valores previos en el AuditLog.

### Una sola lectura de F_CLI, no una por empresa

Preguntar a DELSOL por cada empresa del CRM serían **miles** de peticiones, con
un token que caduca a los 3 minutos. En vez de eso se lee `F_CLI` entero de una
vez (4533 filas caben de sobra en memoria) y el cruce se hace en Python. Una
llamada, y además permite el match difuso por nombre, que la API no sabe hacer.
"""
from __future__ import annotations

import json
import logging
import unicodedata
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.factusol.client import FactusolClient
from app.integrations.factusol.customers import CUSTOMER_FIELDS

logger = logging.getLogger(__name__)

#: Campos sincronizables CRM ← FACTUSOL: `(campo CRM, columna F_CLI)`.
#:
#: `phone` **no está**: la tabla `companies` no tiene columna de teléfono (los
#: teléfonos viven en `contacts`). El email tampoco, por lo mismo.
SYNCABLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "NOFCLI"),
    ("tax_id", "NIFCLI"),
    ("address_line", "DOMCLI"),
    ("city", "POBCLI"),
    ("postal_code", "CPOCLI"),
    ("state", "PROCLI"),
)

#: Longitud mínima del nombre para intentar el match difuso. Por debajo, un
#: «SL» o un «TC» casaría con media base.
MIN_NAME_MATCH_LENGTH = 6

#: Tope de empresas por dry-run. Sin él, una base grande devolvería una
#: respuesta enorme y la tabla del frontend sería inmanejable.
DEFAULT_BATCH_SIZE = 200
MAX_BATCH_SIZE = 1000

#: Marca del backup en el AuditLog.
BULK_SYNC_ACTION = "erp.factusol_bulk_sync"
#: Valor de `companies.factusol_sync_source` (la columna es String(16)).
BULK_SYNC_SOURCE = "bulk_match"


def _norm(value: Any) -> str:
    """Normaliza para comparar: sin acentos, sin dobles espacios, en minúscula.

    Los nombres del CRM y los de FACTUSOL rara vez coinciden carácter a
    carácter («AUDIOVISUALES DATA» vs «AUDIOVISUALES DATA SL»)."""
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.split())


def _norm_tax_id(value: Any) -> str:
    """NIF comparable: sin espacios, guiones ni puntos, en mayúscula."""
    return "".join(
        c for c in str(value or "").upper() if c.isalnum()
    )


def _differences(company: Any, row: dict[str, Any]) -> list[dict[str, Any]]:
    """Diferencias campo a campo entre la empresa CRM y el cliente F_CLI."""
    out = []
    for field, column in SYNCABLE_FIELDS:
        crm_value = str(getattr(company, field, None) or "").strip()
        fac_value = str(row.get(column) or "").strip()
        out.append({
            "field": field,
            "crm": crm_value,
            "factusol": fac_value,
            # Un FACTUSOL vacío NO cuenta como diferencia: sobrescribir un dato
            # del CRM con nada sería perder información, no limpiarla.
            "differs": bool(fac_value) and _norm(crm_value) != _norm(fac_value),
        })
    return out


def _candidate(row: dict[str, Any], company: Any) -> dict[str, Any]:
    out = {f"factusol_{k.lower()}": row.get(k) for k in CUSTOMER_FIELDS}
    out["factusol_codcli"] = (
        str(row.get("CODCLI")) if row.get("CODCLI") is not None else None
    )
    out["differences"] = _differences(company, row)
    out["differing_fields"] = sum(1 for d in out["differences"] if d["differs"])
    return out


def _index_customers(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]], list[tuple[str, dict]]]:
    """Índices de F_CLI para cruzar sin recorrer la tabla por cada empresa."""
    by_nif: dict[str, list[dict]] = {}
    by_email: dict[str, list[dict]] = {}
    by_name: list[tuple[str, dict]] = []
    for row in rows:
        nif = _norm_tax_id(row.get("NIFCLI"))
        if nif:
            by_nif.setdefault(nif, []).append(row)
        email = _norm(row.get("EMACLI"))
        if email:
            by_email.setdefault(email, []).append(row)
        for column in ("NOFCLI", "NOCCLI"):
            name = _norm(row.get(column))
            if len(name) >= MIN_NAME_MATCH_LENGTH:
                by_name.append((name, row))
    return by_nif, by_email, by_name


def _match_company(
    company: Any, by_nif: dict, by_email: dict, by_name: list,
) -> tuple[str, str, list[dict]]:
    """`(match_type, confidence, filas F_CLI candidatas)` para una empresa.

    Prioridad: NIF exacto → email exacto → nombre difuso. En cuanto una casa se
    para: un match por NIF es contable, uno por nombre es una sugerencia."""
    nif = _norm_tax_id(getattr(company, "tax_id", None))
    if nif and nif in by_nif:
        return "nif", "high", by_nif[nif]

    # El email de la empresa no vive en `companies`; se busca por el de sus
    # contactos, que es lo más cercano que tiene el CRM.
    for email in _company_emails(company):
        key = _norm(email)
        if key and key in by_email:
            return "email", "medium", by_email[key]

    name = _norm(getattr(company, "name", None))
    if len(name) >= MIN_NAME_MATCH_LENGTH:
        hits = [row for candidate, row in by_name
                if name in candidate or candidate in name]
        if hits:
            # Dedupe: un mismo cliente puede casar por NOFCLI y por NOCCLI.
            seen, unique = set(), []
            for row in hits:
                key = str(row.get("CODCLI"))
                if key not in seen:
                    seen.add(key)
                    unique.append(row)
            return "name", "low", unique
    return "none", "none", []


def _company_emails(company: Any) -> list[str]:
    """Emails de los contactos de la empresa (companies no tiene email)."""
    try:
        return [c.email for c in (company.contacts or []) if c.email]
    except Exception:  # noqa: BLE001 — relación no cargada / sin contactos
        return []


def dry_run(
    session: Session, client: FactusolClient, *, ejercicio: str,
    unlinked_only: bool = True, batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """Propone parejas CRM ↔ F_CLI **sin tocar nada**.

    `unlinked_only` deja fuera las empresas que ya tienen `factusol_company_id`:
    esas se gestionan desde su ficha, con «Ver diferencias». En `False` sirve
    para un refresco masivo de las ya vinculadas.
    """
    from app.models.crm import Company  # noqa: PLC0415

    batch_size = max(1, min(int(batch_size or DEFAULT_BATCH_SIZE), MAX_BATCH_SIZE))
    query = select(Company).where(Company.is_active.is_(True))
    if unlinked_only:
        query = query.where(Company.factusol_company_id.is_(None))
    companies = list(session.scalars(query.order_by(Company.name).limit(batch_size)))

    rows = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
    logger.info("factusol bulk-match: %d empresas CRM contra %d clientes F_CLI",
                len(companies), len(rows))
    by_nif, by_email, by_name = _index_customers(rows)

    matches, no_match = [], []
    for company in companies:
        match_type, confidence, hits = _match_company(
            company, by_nif, by_email, by_name,
        )
        if not hits:
            no_match.append({"crm_company_id": company.id,
                             "crm_name": company.name,
                             "crm_tax_id": company.tax_id})
            continue
        matches.append({
            "crm_company_id": company.id,
            "crm_name": company.name,
            "crm_tax_id": company.tax_id,
            "match_type": match_type,
            "confidence": confidence,
            "candidates": [_candidate(row, company) for row in hits],
        })
    return {
        "total_crm_companies": len(companies),
        "total_factusol_customers": len(rows),
        "matches": matches,
        "no_match": no_match,
        "ejercicio": ejercicio,
    }


def apply_operations(
    session: Session, client: FactusolClient, *, ejercicio: str,
    operations: list[dict[str, Any]], actor_id: str | None = None,
) -> dict[str, Any]:
    """Aplica los syncs aprobados. Una empresa por transacción.

    Que una falle no bloquea a las demás: en una limpieza de cientos de
    registros, abortar el lote entero por un caso raro obligaría a repetir todo
    el trabajo de revisión.
    """
    from app.models.crm import Company  # noqa: PLC0415

    rows = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
    by_codcli = {str(r.get("CODCLI")): r for r in rows}
    columns = dict(SYNCABLE_FIELDS)

    applied, errors = 0, []
    for op in operations:
        crm_id = str(op.get("crm_company_id") or "")
        codcli = str(op.get("factusol_codcli") or "")
        fields = [f for f in (op.get("fields_to_sync") or []) if f in columns]
        try:
            company = session.get(Company, crm_id)
            if company is None:
                raise ValueError("la empresa no existe en el CRM")
            if company.factusol_company_id:
                raise ValueError(
                    f"ya está vinculada al cliente {company.factusol_company_id}"
                )
            row = by_codcli.get(codcli)
            if row is None:
                raise ValueError(f"el cliente FACTUSOL {codcli} no existe")

            previous = {f: getattr(company, f, None) for f in fields}
            for field in fields:
                value = str(row.get(columns[field]) or "").strip()
                # Un valor vacío en FACTUSOL no pisa el del CRM: el objetivo es
                # limpiar datos, no borrarlos.
                if value:
                    setattr(company, field, value)
            company.factusol_company_id = codcli
            company.factusol_sync_source = BULK_SYNC_SOURCE
            company.factusol_synced_at = datetime.now(UTC)
            _log_backup(session, company, codcli, fields, previous, actor_id)
            session.commit()
            applied += 1
        except Exception as exc:  # noqa: BLE001 — un fallo no tumba el lote
            session.rollback()
            logger.warning("factusol bulk-match: %s KO: %s", crm_id, exc)
            errors.append({"crm_company_id": crm_id, "error": str(exc)[:200]})
    return {"applied": applied, "errors": errors}


def _log_backup(
    session: Session, company: Any, codcli: str, fields: list[str],
    previous: dict[str, Any], actor_id: str | None,
) -> None:
    """Guarda los valores previos en el AuditLog.

    `companies` no tiene columna `metadata_json`, así que el backup va al
    AuditLog — que además es su sitio natural: queda fechado, atribuido a quien
    lo hizo y es consultable sin ensuciar el modelo de dominio. Es lo que se
    lee para revertir a mano (ver `docs/erp/factusol-bulk-match.md`).
    """
    from app.models.crm import AuditLog  # noqa: PLC0415

    session.add(AuditLog(
        actor_user_id=actor_id,
        action=BULK_SYNC_ACTION,
        target_type="company",
        target_id=company.id,
        metadata_json=json.dumps({
            "factusol_codcli": codcli,
            "applied_fields": fields,
            "previous_values": previous,
        }, ensure_ascii=False, default=str),
    ))
