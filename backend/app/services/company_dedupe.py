"""Deduplicar empresas del CRM por NIF (Fase C · C-7).

Tras los imports masivos de C-6 aparecieron empresas repetidas con el mismo
`tax_id`. Caso real: «Exatronic Lda» (`PT503420506`) existe dos veces porque en
FACTUSOL hay **dos CODCLI con el mismo NIF** —2629 y 2819, duplicado histórico
del escritorio— y el import las trajo por separado.

Dos tiempos, como el resto de las pantallas masivas:

1. `find_duplicates` — agrupa por `tax_id` y enseña qué aporta cada una.
2. `merge_groups` — el operador elige la principal y las demás se absorben.

### Lo que hace peligroso un merge aquí

Las tres FK que apuntan a `companies.id` —`contacts`, `tasks`, `orders`— son
todas **`ON DELETE SET NULL`**. Borrar una empresa NO falla: pone a NULL las
referencias **en silencio**. Un merge que se olvide de una tabla no revienta,
simplemente pierde datos sin decirlo.

Por eso las tablas a mover no se escriben a mano: se leen de los metadatos de
SQLAlchemy (`_company_fk_tables`) y se comparan con las que sabemos mover. Si
alguien añade una cuarta FK y no la registra aquí, el merge **se niega a
ejecutarse** en vez de vaciarla calladamente.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Marca del backup en el AuditLog.
COMPANY_MERGE_ACTION = "erp.company_merge"

#: Campos que la principal **completa** desde las absorbidas si los tiene
#: vacíos. Nunca se sobrescribe un valor que la principal ya tenga: si el
#: operador la eligió como buena, sus datos mandan.
FILLABLE_FIELDS: tuple[str, ...] = (
    "name", "tax_id", "address_line", "city", "postal_code", "state",
    "country", "website", "domain", "vat", "region", "sector",
    "size_category", "notes",
)

#: Snapshot completo de una empresa absorbida, para poder rehacerla a mano.
SNAPSHOT_FIELDS: tuple[str, ...] = FILLABLE_FIELDS + (
    "id", "source", "factusol_company_id", "factusol_sync_source",
    "is_active", "created_at",
)

#: Tablas que sabemos reapuntar, con la etiqueta que sale en el resumen.
#: La clave es el nombre de tabla; ver `_check_all_fks_are_handled`.
MOVABLE_TABLES: dict[str, str] = {
    "contacts": "contacts_moved",
    "orders": "orders_moved",
    "tasks": "tasks_moved",
}


def _company_fk_tables() -> set[str]:
    """Tablas con una FK a `companies.id`, leídas del propio esquema.

    No se listan a mano a propósito: las tres FK son `ON DELETE SET NULL`, así
    que olvidarse de una no da error — vacía la columna en silencio."""
    from app.db.base import Base  # noqa: PLC0415

    out = set()
    for table in Base.metadata.tables.values():
        for column in table.columns:
            for fk in column.foreign_keys:
                if fk.target_fullname == "companies.id":
                    out.add(table.name)
    return out


def _check_all_fks_are_handled() -> None:
    """Se niega a fusionar si el esquema tiene una FK que no sabemos mover."""
    unknown = _company_fk_tables() - set(MOVABLE_TABLES)
    if unknown:
        raise ValueError(
            "hay tablas que apuntan a companies.id y este merge no sabe mover: "
            + ", ".join(sorted(unknown))
            + ". Añádelas a MOVABLE_TABLES antes de fusionar — la FK es "
              "ON DELETE SET NULL y borrar la empresa las vaciaría en silencio."
        )


def _snapshot(company: Any) -> dict[str, Any]:
    return {f: getattr(company, f, None) for f in SNAPSHOT_FIELDS}


def _counts_for(session: Session, company_ids: list[str]) -> dict[str, dict[str, int]]:
    """`{company_id: {contacts_count, orders_count, tasks_count}}`.

    Tres consultas agrupadas, no tres por empresa: un grupo de duplicados con
    50 filas serían 150 SELECTs."""
    from app.erp.models.orders import Order  # noqa: PLC0415
    from app.models.crm import Contact, Task  # noqa: PLC0415

    out = {cid: {"contacts_count": 0, "orders_count": 0, "tasks_count": 0}
           for cid in company_ids}
    for model, key in ((Contact, "contacts_count"), (Order, "orders_count"),
                       (Task, "tasks_count")):
        rows = session.execute(
            select(model.company_id, func.count())
            .where(model.company_id.in_(company_ids))
            .group_by(model.company_id)
        ).all()
        for company_id, n in rows:
            if company_id in out:
                out[company_id][key] = int(n)
    return out


def find_duplicates(session: Session) -> dict[str, Any]:
    """Grupos de empresas que comparten `tax_id`. **Solo lectura.**

    Se ignoran las de NIF vacío: sin NIF no hay evidencia de que sean la misma
    empresa, y agruparlas todas juntas sería un disparate.
    """
    from app.models.crm import Company  # noqa: PLC0415

    dup_tax_ids = [
        t for (t,) in session.execute(
            select(Company.tax_id)
            .where(Company.tax_id.is_not(None), Company.tax_id != "")
            .group_by(Company.tax_id)
            .having(func.count() > 1)
        ).all()
    ]
    if not dup_tax_ids:
        return {"total_groups": 0, "total_companies_involved": 0, "groups": []}

    companies = list(session.scalars(
        select(Company).where(Company.tax_id.in_(dup_tax_ids))
        .order_by(Company.tax_id, Company.created_at)
    ))
    counts = _counts_for(session, [c.id for c in companies])

    grouped: dict[str, list[dict[str, Any]]] = {}
    for company in companies:
        grouped.setdefault(str(company.tax_id), []).append({
            "id": company.id,
            "name": company.name,
            "city": company.city,
            "address_line": company.address_line,
            "postal_code": company.postal_code,
            "state": company.state,
            "country": company.country,
            "website": company.website,
            "domain": company.domain,
            "notes": company.notes,
            "factusol_company_id": company.factusol_company_id,
            "source": company.source,
            "created_at": company.created_at,
            **counts[company.id],
        })

    # Los grupos más gordos primero: son los que más ensucian la base.
    groups = sorted(
        ({"tax_id": tax_id, "companies": rows} for tax_id, rows in grouped.items()),
        key=lambda g: (-len(g["companies"]), g["tax_id"]),
    )
    logger.info("dedupe empresas: %d grupos, %d empresas implicadas",
                len(groups), len(companies))
    return {
        "total_groups": len(groups),
        "total_companies_involved": len(companies),
        "groups": groups,
    }


def merge_groups(
    session: Session, *, operations: list[dict[str, Any]],
    actor: Any = None,
) -> dict[str, Any]:
    """Absorbe cada grupo en su empresa principal.

    Una operación por transacción: en un lote de decenas de grupos, abortar
    todo por un caso raro obligaría a repetir la revisión entera. **Nunca
    lanza por una operación**: lo que falle va a `errors` y el resto sigue.
    """
    from app.models.crm import Company  # noqa: PLC0415

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for op in operations:
        keep_id = str(op.get("keep_id") or "")
        merge_ids = [str(x) for x in (op.get("merge_ids") or []) if x]
        try:
            _check_all_fks_are_handled()
            if not keep_id or not merge_ids:
                raise ValueError("faltan keep_id o merge_ids")
            if keep_id in merge_ids:
                raise ValueError("la principal no puede estar entre las absorbidas")

            keep = session.get(Company, keep_id)
            if keep is None:
                raise ValueError(f"la empresa {keep_id} no existe")
            merges = []
            for merge_id in merge_ids:
                other = session.get(Company, merge_id)
                if other is None:
                    raise ValueError(f"la empresa {merge_id} no existe")
                # Guard anti-error: fusionar dos NIF distintos sería juntar dos
                # empresas de verdad, y eso no se deshace con un UPDATE.
                if _norm_tax(other.tax_id) != _norm_tax(keep.tax_id):
                    raise ValueError(
                        f"«{other.name}» tiene NIF {other.tax_id!r} y la "
                        f"principal {keep.tax_id!r}: no se fusionan")
                merges.append(other)

            outcome = _merge_into(session, keep, merges, actor)
            session.commit()
            results.append({"keep_id": keep_id, "merged_ids": merge_ids,
                            "result": "merged", **outcome})
        except Exception as exc:  # noqa: BLE001 — un fallo no tumba el lote
            session.rollback()
            logger.warning("dedupe empresas: %s KO: %s", keep_id, exc)
            errors.append({"keep_id": keep_id, "merge_ids": merge_ids,
                           "error": str(exc)[:300]})

    return {
        "merged_groups": len(results),
        "companies_deleted": sum(len(r["merged_ids"]) for r in results),
        "contacts_moved": sum(r["contacts_moved"] for r in results),
        "orders_moved": sum(r["orders_moved"] for r in results),
        "tasks_moved": sum(r["tasks_moved"] for r in results),
        "results": results,
        "errors": errors,
    }


def _norm_tax(value: Any) -> str:
    """NIF comparable: sin espacios, guiones ni puntos, en mayúscula."""
    return "".join(c for c in str(value or "").upper() if c.isalnum())


def _merge_into(
    session: Session, keep: Any, merges: list[Any], actor: Any,
) -> dict[str, Any]:
    """Mueve todo lo que cuelga de `merges` a `keep` y las borra."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.models.orders import Order  # noqa: PLC0415
    from app.models.crm import Contact, Task  # noqa: PLC0415

    moved = {"contacts_moved": 0, "orders_moved": 0, "tasks_moved": 0}
    discarded_codclis: list[str] = []
    snapshots: list[dict[str, Any]] = []
    filled: dict[str, str] = {}

    for other in merges:
        snapshots.append(_snapshot(other))
        for model, key in ((Contact, "contacts_moved"), (Order, "orders_moved"),
                           (Task, "tasks_moved")):
            result = session.execute(
                update(model.__table__)
                .where(model.company_id == other.id)
                .values(company_id=keep.id)
            )
            moved[key] += int(result.rowcount or 0)

        # Completar: solo lo que la principal tenga vacío. Si el operador la
        # eligió como buena, sus datos mandan.
        for field in FILLABLE_FIELDS:
            if str(getattr(keep, field, None) or "").strip():
                continue
            value = getattr(other, field, None)
            if str(value or "").strip():
                setattr(keep, field, value)
                filled[field] = other.id

        # El vínculo con FACTUSOL: se hereda si la principal no tenía. Si tenía
        # otro, se queda el suyo y el descartado va al audit — puede haber
        # facturación colgando de ese CODCLI y hay que poder rastrearlo.
        if other.factusol_company_id:
            if not keep.factusol_company_id:
                keep.factusol_company_id = other.factusol_company_id
                keep.factusol_sync_source = other.factusol_sync_source
            elif other.factusol_company_id != keep.factusol_company_id:
                discarded_codclis.append(other.factusol_company_id)

    for other in merges:
        session.delete(other)

    record_event(
        session,
        action=COMPANY_MERGE_ACTION,
        target_type="company",
        target_id=keep.id,
        actor=actor,
        metadata={
            "keep_id": keep.id,
            "merge_ids": [s["id"] for s in snapshots],
            "merged_data_snapshot": snapshots,
            "filled_fields": filled,
            "moved": moved,
            # Ojo al revisar: esos CODCLI se quedan sin empresa en el CRM.
            "discarded_factusol_codclis": discarded_codclis,
        },
        message=f"Fusionadas {len(merges)} empresa(s) en «{keep.name}»",
    )
    if discarded_codclis:
        logger.warning("dedupe empresas: %s conserva el codcli %s; se descartan "
                       "%s", keep.id, keep.factusol_company_id,
                       ", ".join(discarded_codclis))
    return {**moved, "filled_fields": sorted(filled),
            "discarded_factusol_codclis": discarded_codclis}
