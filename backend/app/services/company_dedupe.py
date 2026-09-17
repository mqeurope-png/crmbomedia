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
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Columnas con índice ÚNICO que la fusión NO puede duplicar. Al copiarlas de la
#: absorbida a la superviviente hay que respetar el índice (`domain` →
#: `uq_companies_domain`); al archivar la absorbida se liberan (a NULL) para no
#: dejar el valor colisionando con el que hereda la superviviente. `website` no
#: es único, pero se trata igual (mismo filtro de basura, solo si vacío).
UNIQUE_FILL_FIELDS: tuple[str, ...] = ("domain", "website")

#: Hosts que NO son el dominio de una empresa: redes sociales, marketplaces y
#: acortadores. No se heredan (aunque no romperían el índice, ensucian la
#: ficha). Los free-mail salen de `company_extraction.PERSONAL_DOMAINS`.
GARBAGE_DOMAINS: frozenset[str] = frozenset({
    "instagram.com", "facebook.com", "m.facebook.com", "fb.com",
    "linkedin.com", "twitter.com", "x.com", "youtube.com", "youtu.be",
    "tiktok.com", "wa.me", "whatsapp.com", "t.me", "pinterest.com",
    "wordpress.com", "blogspot.com", "wix.com", "sites.google.com",
    "amazon.com", "amazon.es", "aliexpress.com", "ebay.com", "ebay.es",
    "ww", "www", "n/a", "na", "-",
})

#: Un dominio «de verdad»: etiquetas alfanuméricas separadas por puntos y un
#: TLD alfabético de ≥2 letras. Rechaza «ww» (sin punto), markdown
#: `[www.x](https://www.x)` (lo descarta antes `normalise_domain`) y basura.
_BARE_DOMAIN_RE = re.compile(
    r"^(?=.{4,255}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$"
)


def _usable_domain(value: Any) -> str | None:
    """Normaliza un dominio/URL y devuelve el host SOLO si es un dominio de
    empresa aprovechable; None para vacío, basura (`ww`, `instagram.com`),
    free-mail o markdown. Así un dominio basura de la absorbida no se hereda —
    y nunca puede romper la fusión."""
    from app.services.company_extraction import (  # noqa: PLC0415
        PERSONAL_DOMAINS,
        normalise_domain,
    )

    host = normalise_domain(value)
    if not host or host in PERSONAL_DOMAINS or host in GARBAGE_DOMAINS:
        return None
    return host if _BARE_DOMAIN_RE.match(host) else None


def _domain_in_use(session: Session, domain: str, *, exclude_id: str) -> bool:
    """¿Hay ya OTRA empresa (archivada o no) con ese `domain`? El índice único
    `uq_companies_domain` abarca todas las filas, así que asignarlo a la
    superviviente reventaría si alguien más lo tiene."""
    from app.models.crm import Company  # noqa: PLC0415

    other_id = session.scalar(
        select(Company.id)
        .where(Company.domain == domain, Company.id != exclude_id)
        .limit(1)
    )
    return other_id is not None

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


def merge_companies_into(
    session: Session, keep: Any, merges: list[Any], *, actor: Any = None,
) -> dict[str, Any]:
    """Absorbe `merges` en `keep`: reasigna TODO lo que cuelga de las absorbidas
    (contactos, pedidos, tareas — las tres FK a `companies.id`), re-apunta su
    actividad/timeline (`audit_logs`), hereda el vínculo FACTUSOL y las notas, y
    **ARCHIVA** las absorbidas (`is_archived=True`, reversible — NUNCA se borran
    físicamente). Deja constancia en el timeline de `keep`. Sin commit (el caller
    decide). Núcleo compartido por el endpoint de fusión, el vincular→fusionar y
    el comando masivo."""
    return _merge_into(session, keep, merges, actor)


def _merge_into(
    session: Session, keep: Any, merges: list[Any], actor: Any,
) -> dict[str, Any]:
    """Mueve todo lo que cuelga de `merges` a `keep` y las ARCHIVA (reversible)."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.models.orders import Order  # noqa: PLC0415
    from app.models.crm import AuditLog, Contact, Task  # noqa: PLC0415

    moved = {"contacts_moved": 0, "orders_moved": 0, "tasks_moved": 0,
             "timeline_moved": 0}
    discarded_codclis: list[str] = []
    snapshots: list[dict[str, Any]] = []
    filled: dict[str, str] = {}
    # Candidatos de campos únicos (domain/website) a heredar si la superviviente
    # los tiene vacíos: {campo: (host_aprovechable, id_absorbida)}. Se asignan
    # DESPUÉS de liberar los de las absorbidas, respetando el índice único.
    inherit_unique: dict[str, tuple[str, str]] = {}

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

        # Actividad / timeline: los eventos de auditoría de la absorbida (su
        # ficha es `target`) pasan a la principal, para que su historial no se
        # pierda al archivarla. El propio evento de fusión se registra después.
        tl = session.execute(
            update(AuditLog.__table__)
            .where(AuditLog.target_type == "company", AuditLog.target_id == other.id)
            .values(target_id=keep.id)
        )
        moved["timeline_moved"] += int(tl.rowcount or 0)

        # Completar: solo lo que la principal tenga vacío. Si el operador la
        # eligió como buena, sus datos mandan.
        for field in FILLABLE_FIELDS:
            # `notes` se ACUMULA (abajo); `domain`/`website` son campos únicos y
            # se resuelven aparte tras liberar los de las absorbidas (más abajo).
            if field == "notes" or field in UNIQUE_FILL_FIELDS:
                continue
            if str(getattr(keep, field, None) or "").strip():
                continue
            value = getattr(other, field, None)
            if str(value or "").strip():
                setattr(keep, field, value)
                filled[field] = other.id

        # domain/website: se anota el primer valor APROVECHABLE de las absorbidas
        # (ignora basura: `ww`, `instagram.com`, markdown). Solo se asigna a la
        # superviviente si esta lo tiene vacío y nadie más lo usa; el valor real
        # se fija abajo, tras liberar los de las absorbidas. Para `domain` se
        # guarda el host normalizado (lo que va al índice); para `website`, el
        # valor original (no es único y conserva ruta/esquema).
        for field in UNIQUE_FILL_FIELDS:
            if field in inherit_unique or str(getattr(keep, field, None) or "").strip():
                continue
            raw = getattr(other, field, None)
            host = _usable_domain(raw)
            if host is None:
                continue
            value = host if field == "domain" else str(raw).strip()
            inherit_unique[field] = (value, other.id)

        # Notas: se acumulan en la principal (no se pierde ninguna al archivar).
        other_notes = str(getattr(other, "notes", None) or "").strip()
        if other_notes:
            prefix = f"[Fusionada «{other.name}»] "
            if prefix + other_notes not in (keep.notes or ""):
                keep.notes = (
                    f"{keep.notes}\n{prefix}{other_notes}" if (keep.notes or "").strip()
                    else f"{prefix}{other_notes}"
                )
                filled["notes"] = other.id

        # El vínculo con FACTUSOL: se hereda si la principal no tenía. Si tenía
        # otro, se queda el suyo y el descartado va al audit — puede haber
        # facturación colgando de ese CODCLI y hay que poder rastrearlo.
        if other.factusol_company_id:
            if not keep.factusol_company_id:
                keep.factusol_company_id = other.factusol_company_id
                keep.factusol_sync_source = other.factusol_sync_source
            elif other.factusol_company_id != keep.factusol_company_id:
                discarded_codclis.append(other.factusol_company_id)

    # Archivado reversible (NO borrado físico): la ficha absorbida deja de
    # aparecer en las listas pero se puede reactivar (`is_archived=0`). Se
    # LIBERAN sus campos únicos (`domain`/`website` a NULL) para que no colisionen
    # con el que herede la superviviente ni queden bloqueando el índice
    # (`uq_companies_domain`). El valor original queda en el snapshot del audit.
    for other in merges:
        other.is_archived = True
        for field in UNIQUE_FILL_FIELDS:
            setattr(other, field, None)

    # Se vuelca el NULL de las absorbidas ANTES de asignar el dominio heredado,
    # para que el índice único no vea dos filas con el mismo valor a la vez.
    session.flush()
    for field, (value, source_id) in inherit_unique.items():
        # Reconfirmado contra la base ya liberada: si OTRA ficha (no una de las
        # absorbidas, que ya están a NULL) tiene ese dominio, no se hereda.
        if field == "domain" and _domain_in_use(session, value, exclude_id=keep.id):
            continue
        setattr(keep, field, value)
        filled[field] = source_id

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
            "archived_ids": [s["id"] for s in snapshots],
            # Ojo al revisar: esos CODCLI se quedan sin empresa en el CRM.
            "discarded_factusol_codclis": discarded_codclis,
        },
        message=f"Fusionadas y archivadas {len(merges)} empresa(s) en «{keep.name}»",
    )
    if discarded_codclis:
        logger.warning("dedupe empresas: %s conserva el codcli %s; se descartan "
                       "%s", keep.id, keep.factusol_company_id,
                       ", ".join(discarded_codclis))
    return {**moved, "filled_fields": sorted(filled),
            "discarded_factusol_codclis": discarded_codclis}


# --- fusión masiva por NIF normalizado (Lote 8 · B3) --------------------------
#
# El comando `python -m scripts.fusionar_empresas_duplicadas` agrupa las fichas
# CRM NO archivadas por su NIF/VAT NORMALIZADO (`company_discovery.nif_key`, la
# misma normalización que el discovery de limpieza: quita separadores y el
# prefijo de país de la UE, de modo que `ESB123` y `B123` caen en el mismo
# grupo). Elige superviviente y planifica la fusión, pero NUNCA fusiona a
# ciegas: si el grupo es ambiguo va «a revisar».

#: Por debajo de este parecido de nombre, la absorbida y la superviviente se
#: parecen tan poco que el grupo va a revisión (no se fusiona solo por el NIF).
NAME_SIMILARITY_LOW = 0.45


@dataclass
class MergeGroupAction:
    """Un grupo que SÍ se fusiona: `keep` (superviviente, la vinculada a
    FACTUSOL) absorbe a `merge_ids`."""
    nif_key: str
    keep_id: str
    keep_name: str
    keep_codcli: str | None
    merge_ids: list[str]
    merge_names: list[str]


@dataclass
class MergeGroupReview:
    """Un grupo AMBIGUO: se enseña pero NO se fusiona (el operador decide)."""
    nif_key: str
    reason: str
    company_ids: list[str]
    names: list[str]
    linked_codclis: list[str]


@dataclass
class MassMergePlan:
    to_merge: list[MergeGroupAction] = dc_field(default_factory=list)
    review: list[MergeGroupReview] = dc_field(default_factory=list)
    #: Fichas NO archivadas examinadas (denominador del informe).
    total_companies: int = 0

    @property
    def companies_to_archive(self) -> int:
        return sum(len(a.merge_ids) for a in self.to_merge)


def _norm_nif(company: Any) -> str | None:
    """NIF normalizado de una ficha: `tax_id` y si no, el `vat` (VAT-IVA)."""
    from app.erp.company_discovery import nif_key  # noqa: PLC0415

    return nif_key(getattr(company, "tax_id", None)) or nif_key(getattr(company, "vat", None))


def plan_duplicate_merges(
    session: Session, *, name_threshold: float = NAME_SIMILARITY_LOW,
    only_key: str | None = None,
) -> MassMergePlan:
    """Agrupa las empresas NO archivadas por NIF/VAT normalizado y decide, por
    grupo, si se fusiona o va a revisión. **Solo lectura** (no escribe nada).

    Superviviente = la ficha vinculada a FACTUSOL. Va «a revisar» (sin fusión
    automática) si:
    - NINGUNA ficha del grupo está vinculada a FACTUSOL, o
    - VARIAS están vinculadas a CODCLI DISTINTOS (no se sabe cuál manda), o
    - algún nombre del grupo es MUY DISPAR del de la superviviente (`name_threshold`).

    Idempotente por diseño: tras `--apply`, las absorbidas quedan `is_archived`
    y salen del universo, así que un grupo ya fusionado se reduce a la
    superviviente sola y deja de ser duplicado."""
    from app.erp.company_discovery import name_similarity, nif_looks_malformed  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415

    companies = list(session.scalars(
        select(Company).where(Company.is_archived.is_(False))
    ))
    groups: dict[str, list[Any]] = {}
    for company in companies:
        key = _norm_nif(company)
        # Sin NIF fiable no hay evidencia de que sean la misma empresa: no se
        # agrupa (igual que el discovery de limpieza).
        if not key or nif_looks_malformed(key):
            continue
        if only_key and key != only_key:
            continue
        groups.setdefault(key, []).append(company)

    plan = MassMergePlan(total_companies=len(companies))
    # Grupos más gordos primero (los que más ensucian la base).
    for key, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(members) < 2:
            continue
        names = [m.name for m in members]
        linked = [m for m in members if str(m.factusol_company_id or "").strip()]
        distinct_codclis = sorted({
            str(m.factusol_company_id).strip() for m in linked
        })
        if not distinct_codclis:
            plan.review.append(MergeGroupReview(
                nif_key=key,
                reason="ninguna ficha está vinculada a FACTUSOL: elige la "
                       "superviviente a mano",
                company_ids=[m.id for m in members], names=names,
                linked_codclis=[]))
            continue
        if len(distinct_codclis) > 1:
            plan.review.append(MergeGroupReview(
                nif_key=key,
                reason="varias fichas vinculadas a CODCLI distintos "
                       f"({', '.join(distinct_codclis)}): revisar a mano",
                company_ids=[m.id for m in members], names=names,
                linked_codclis=distinct_codclis))
            continue

        # Un único CODCLI en el grupo: la superviviente es la vinculada.
        keep = linked[0]
        others = [m for m in members if m.id != keep.id]
        disparate = [
            (o.name, sim) for o in others
            if (sim := name_similarity(keep.name, o.name)) is not None
            and sim < name_threshold
        ]
        if disparate:
            detalle = ", ".join(f"«{n}» ({s})" for n, s in disparate)
            plan.review.append(MergeGroupReview(
                nif_key=key,
                reason=f"nombres muy dispares de «{keep.name}»: {detalle}. "
                       "Revisar a mano",
                company_ids=[m.id for m in members], names=names,
                linked_codclis=distinct_codclis))
            continue

        plan.to_merge.append(MergeGroupAction(
            nif_key=key, keep_id=keep.id, keep_name=keep.name,
            keep_codcli=str(keep.factusol_company_id).strip(),
            merge_ids=[o.id for o in others],
            merge_names=[o.name for o in others]))

    logger.info("fusión masiva: %d a fusionar, %d a revisar (de %d fichas)",
                len(plan.to_merge), len(plan.review), len(companies))
    return plan


def apply_duplicate_merges(
    session: Session, plan: MassMergePlan, *, actor: Any = None,
) -> dict[str, Any]:
    """Ejecuta SOLO los grupos `to_merge` del plan (los «a revisar» se ignoran).
    Una fusión por transacción: un fallo va a `errors` y el resto sigue. Reusa
    `merge_companies_into` (reasigna todo + archiva). Idempotente: una absorbida
    que ya esté archivada o desaparecida se salta sin error."""
    from app.models.crm import Company  # noqa: PLC0415

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for grp in plan.to_merge:
        try:
            _check_all_fks_are_handled()
            keep = session.get(Company, grp.keep_id)
            if keep is None:
                raise ValueError(f"la superviviente {grp.keep_id} ya no existe")
            keep_key = _norm_nif(keep)
            merges = []
            for merge_id in grp.merge_ids:
                other = session.get(Company, merge_id)
                # Idempotencia: ya fusionada/archivada en una corrida anterior.
                if other is None or other.is_archived:
                    continue
                # Guard: no fusionar dos NIF que ya no casan (plan obsoleto).
                if _norm_nif(other) != keep_key:
                    raise ValueError(
                        f"«{other.name}» ya no comparte NIF normalizado con "
                        f"«{keep.name}»: se aborta el grupo")
                merges.append(other)
            if not merges:
                continue  # nada que hacer (todo ya fusionado): idempotente
            outcome = merge_companies_into(session, keep, merges, actor=actor)
            session.commit()
            results.append({
                "nif_key": grp.nif_key, "keep_id": keep.id, "keep_name": keep.name,
                "merged_ids": [m.id for m in merges], "result": "merged",
                **outcome})
        except Exception as exc:  # noqa: BLE001 — un fallo no tumba el lote
            session.rollback()
            logger.warning("fusión masiva: grupo %s KO: %s", grp.nif_key, exc)
            errors.append({"nif_key": grp.nif_key, "keep_id": grp.keep_id,
                           "error": str(exc)[:300]})

    return {
        "merged_groups": len(results),
        "companies_archived": sum(len(r["merged_ids"]) for r in results),
        "contacts_moved": sum(r["contacts_moved"] for r in results),
        "orders_moved": sum(r["orders_moved"] for r in results),
        "tasks_moved": sum(r["tasks_moved"] for r in results),
        "results": results,
        "errors": errors,
    }
