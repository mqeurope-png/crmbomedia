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

#: Tope de filas por dry-run. Es una **red de seguridad**, no una página: por
#: defecto se procesa TODO.
#:
#: C-5-fix2: con el tope en 200 el dry-run por email cortaba el bucle a los 200
#: matches, así que de 20 282 contactos solo se miraban ~4 000 y el operador
#: nunca veía los otros 16 000. Peor aún, `no_match_count` contaba solo lo
#: iterado, así que los totales del resumen no cuadraban.
#:
#: Procesarlos todos es barato: `F_CLI` se lee **una vez** (optimización de
#: C-5) y el resto es comparar strings en Python. Además el número de matches
#: está acotado por los emails que haya en F_CLI (~4 500), no por el número de
#: contactos.
MAX_BATCH_SIZE = 100_000
#: Tope del modo por empresa, que sí pagina de verdad (`LIMIT` en SQL).
DEFAULT_BATCH_SIZE = 200

#: Marca del backup en el AuditLog.
BULK_SYNC_ACTION = "erp.factusol_bulk_sync"
#: Valor de `companies.factusol_sync_source` (la columna es String(16)).
BULK_SYNC_SOURCE = "bulk_match"

#: Modo «contactos por email» (C-5-fix1). Acción y origen propios para poder
#: distinguirlos del modo por NIF/nombre al auditar o revertir.
BULK_SYNC_BY_EMAIL_ACTION = "erp.factusol_bulk_sync_by_email"
#: Acción propia para la empresa CREADA desde cero (C-5-fix2): no hay valores
#: previos que restaurar, se deshace borrando la empresa.
BULK_SYNC_BY_EMAIL_CREATE_ACTION = "erp.factusol_bulk_sync_by_email_create_company"
#: Reasignación de contacto (C-5-fix5). A diferencia de las otras tres, esta
#: acción audita un **contacto**, no una empresa: lo que cambia es
#: `contacts.company_id`. Se deshace devolviéndolo a `old_company_id`.
BULK_SYNC_BY_EMAIL_REASSIGN_ACTION = "erp.factusol_bulk_sync_by_email_reassign"
#: Cabe en String(16) justo — 15 caracteres.
BULK_SYNC_BY_EMAIL_SOURCE = "bulk_by_email"


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
    *, action: str = BULK_SYNC_ACTION, extra: dict[str, Any] | None = None,
) -> None:
    """Guarda los valores previos en el AuditLog.

    `companies` no tiene columna `metadata_json`, así que el backup va al
    AuditLog — que además es su sitio natural: queda fechado, atribuido a quien
    lo hizo y es consultable sin ensuciar el modelo de dominio. Es lo que se
    lee para revertir a mano (ver `docs/erp/factusol-bulk-match.md`).

    `action` distingue el modo (por NIF/nombre vs. por email de contacto) para
    poder revertir un lote sin arrastrar el otro.
    """
    from app.models.crm import AuditLog  # noqa: PLC0415

    session.add(AuditLog(
        actor_user_id=actor_id,
        action=action,
        target_type="company",
        target_id=company.id,
        metadata_json=json.dumps({
            "factusol_codcli": codcli,
            "applied_fields": fields,
            "previous_values": previous,
            **(extra or {}),
        }, ensure_ascii=False, default=str),
    ))


# --- modo «contactos por email» (C-5-fix1) -----------------------------------
#
# El modo por NIF/nombre da mucho ruido en la práctica: la mayoría de las
# empresas del CRM llegaron de imports masivos SIN NIF, y el match difuso por
# nombre produce falsos positivos («4d Factory» ↔ «FACTORY»).
#
# El email es un identificador de verdad: o coincide exacto o no coincide. Se
# itera por CONTACTOS —que sí tienen email— y se actualiza la empresa a la que
# pertenecen. Menos cobertura, pero lo que propone es fiable.


def _contact_name(contact: Any) -> str:
    return " ".join(
        x for x in (contact.first_name, contact.last_name) if x
    ).strip()


def dry_run_by_contact_email(
    session: Session, client: FactusolClient, *, ejercicio: str,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Propone parejas contacto→cliente por **email exacto**. Solo lectura.

    Sin fuzzy de ningún tipo: o el email coincide o no hay match. Lo que se
    actualizaría es la **empresa del contacto**, no el contacto.

    Procesa **todos** los contactos con email (C-5-fix2). `batch_size` es una
    red de seguridad opcional, no una página: si se pasa, la respuesta avisa con
    `truncated: true` en vez de callarse los que faltan.
    """
    from app.models.crm import Company, Contact  # noqa: PLC0415

    limit = min(int(batch_size), MAX_BATCH_SIZE) if batch_size else MAX_BATCH_SIZE
    contacts = list(session.scalars(
        select(Contact)
        .where(Contact.is_active.is_(True), Contact.email.is_not(None),
               Contact.email != "")
        .order_by(Contact.first_name)
    ))

    rows = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
    by_email: dict[str, list[dict]] = {}
    for row in rows:
        email = _norm(row.get("EMACLI"))
        if email:
            by_email.setdefault(email, []).append(row)
    logger.info("factusol bulk-match by-email: %d contactos con email contra "
                "%d clientes F_CLI", len(contacts), len(rows))

    # Las empresas se cargan de golpe: pedirlas una a una dentro del bucle
    # sería un N+1 sobre miles de contactos.
    company_ids = {c.company_id for c in contacts if c.company_id}
    companies = {
        c.id: c for c in session.scalars(
            select(Company).where(Company.id.in_(company_ids))
        )
    } if company_ids else {}

    matches, no_match_count, without_company = [], 0, 0
    truncated = False
    for contact in contacts:
        if len(matches) >= limit:
            # Solo se llega aquí con una base descomunal. Se avisa en la
            # respuesta: cortar en silencio haría que el resumen no cuadrase y
            # el operador daría por revisados contactos que nadie miró.
            truncated = True
            logger.warning(
                "factusol bulk-match by-email: cortado en %d matches de %d "
                "contactos; el resto no se ha evaluado", limit, len(contacts),
            )
            break
        hits = by_email.get(_norm(contact.email), [])
        if not hits:
            no_match_count += 1
            continue
        company = companies.get(contact.company_id) if contact.company_id else None
        if company is None:
            without_company += 1
        matches.append({
            "contact_id": contact.id,
            "contact_name": _contact_name(contact),
            "contact_email": contact.email,
            "company_id": company.id if company else None,
            "company_name": company.name if company else None,
            "company_factusol_id": company.factusol_company_id if company else None,
            # Varios F_CLI con el mismo EMACLI es raro pero posible: se
            # devuelven todos y elige el operador, como en el modo por NIF.
            "candidates": [
                _candidate(row, company) if company
                else _candidate_without_company(row)
                for row in hits
            ],
        })

    return {
        "total_contacts_with_email": len(contacts),
        "matches": matches,
        "no_match_count": no_match_count,
        # OJO: es un subconjunto de `matches`, NO una tercera categoría.
        # `len(matches) + no_match_count == total_contacts_with_email`.
        "matches_without_company": without_company,
        "truncated": truncated,
        "ejercicio": ejercicio,
    }


def _candidate_without_company(row: dict[str, Any]) -> dict[str, Any]:
    """Candidato de un contacto sin empresa: no hay con qué comparar, así que
    se muestran los valores de FACTUSOL como «lo que habría»."""
    out = {f"factusol_{k.lower()}": row.get(k) for k in CUSTOMER_FIELDS}
    out["factusol_codcli"] = (
        str(row.get("CODCLI")) if row.get("CODCLI") is not None else None
    )
    out["differences"] = [
        {"field": field, "crm": "", "factusol": str(row.get(column) or "").strip(),
         "differs": bool(str(row.get(column) or "").strip())}
        for field, column in SYNCABLE_FIELDS
    ]
    out["differing_fields"] = sum(1 for d in out["differences"] if d["differs"])
    return out


def apply_by_contact_email(
    session: Session, client: FactusolClient, *, ejercicio: str,
    operations: list[dict[str, Any]], actor_id: str | None = None,
) -> dict[str, Any]:
    """Concilia la empresa de cada contacto con su cliente F_CLI.

    Desenlaces posibles, todos en `results`:

    - `refreshed` — el contacto tenía empresa y se le han traído los datos
      limpios de FACTUSOL.
    - `created_new_company` — el contacto **no** tenía empresa: se crea una con
      los datos de F_CLI, se vincula al CODCLI y se le asigna al contacto.
    - `linked_existing_company` — el contacto no tenía empresa, pero **otra
      empresa del CRM ya está vinculada a ese CODCLI**: se le asigna esa en vez
      de crear una nueva. Crearla dejaría dos empresas CRM apuntando al mismo
      cliente de FACTUSOL, que es justo la duplicidad que arregló C-3-fix3.
    - `reassigned_to_existing_company` / `reassigned_to_new_company` (C-5-fix5)
      — la empresa del contacto está vinculada a **otro** CODCLI: el contacto
      está mal agrupado y se mueve a la empresa que le corresponde. La empresa
      original **no se toca**.
    - `skipped_already_linked_other` — desenlace de C-5-fix1, hoy prácticamente
      extinto: ese caso ahora se reasigna. Se mantiene en el contrato porque hay
      historial auditado con él y el frontend lo sigue reconociendo.
    """
    from app.models.crm import Contact  # noqa: PLC0415

    rows = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
    by_codcli = {str(r.get("CODCLI")): r for r in rows}
    columns = dict(SYNCABLE_FIELDS)

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for op in operations:
        contact_id = str(op.get("contact_id") or "")
        codcli = str(op.get("factusol_codcli") or "")
        fields = [f for f in (op.get("fields_to_sync") or []) if f in columns]
        try:
            contact = session.get(Contact, contact_id)
            if contact is None:
                raise ValueError("el contacto no existe en el CRM")
            row = by_codcli.get(codcli)
            if row is None:
                raise ValueError(f"el cliente FACTUSOL {codcli} no existe")

            if contact.company_id:
                outcome = _refresh_company_of_contact(
                    session, contact, codcli, row, fields, columns, actor_id,
                )
            else:
                outcome = _company_for_orphan_contact(
                    session, contact, codcli, row, columns, actor_id,
                )
            session.commit()
            results.append({"contact_id": contact_id, **outcome})
        except Exception as exc:  # noqa: BLE001 — un fallo no tumba el lote
            session.rollback()
            logger.warning("factusol bulk-match by-email: %s KO: %s",
                           contact_id, exc)
            errors.append({"contact_id": contact_id, "error": str(exc)[:200]})

    def _count(result: str) -> int:
        return sum(1 for r in results if r["result"] == result)

    reassigned_existing = _count("reassigned_to_existing_company")
    reassigned_new = _count("reassigned_to_new_company")
    return {
        "applied": sum(1 for r in results
                       if r["result"] != "skipped_already_linked_other"),
        "results": results,
        "refreshed": _count("refreshed"),
        "created_new_company": _count("created_new_company"),
        "linked_existing_company": _count("linked_existing_company"),
        "reassigned_to_existing_company": reassigned_existing,
        "reassigned_to_new_company": reassigned_new,
        #: Total de reasignaciones, para el titular del resumen.
        "reassigned": reassigned_existing + reassigned_new,
        "skipped_already_linked_other": _count("skipped_already_linked_other"),
        "errors": errors,
    }


def _refresh_company_of_contact(
    session: Session, contact: Any, codcli: str, row: dict[str, Any],
    fields: list[str], columns: dict[str, str], actor_id: str | None,
) -> dict[str, Any]:
    """El contacto ya tiene empresa: se le traen los datos limpios."""
    from app.models.crm import Company  # noqa: PLC0415

    company = session.get(Company, contact.company_id)
    if company is None:
        raise ValueError("la empresa del contacto no existe")
    if company.factusol_company_id and company.factusol_company_id != codcli:
        # C-5-fix5: el contacto está mal agrupado, no hay conflicto real. Se
        # mueve a la empresa que le corresponde en vez de saltarlo.
        return _reassign_contact_to_correct_company(
            session, contact, company, codcli, row, columns, actor_id,
        )

    previous = {f: getattr(company, f, None) for f in fields}
    for field in fields:
        value = str(row.get(columns[field]) or "").strip()
        # Un valor vacío en FACTUSOL no pisa el del CRM: limpiar no es borrar.
        if value:
            setattr(company, field, value)
    company.factusol_company_id = codcli
    company.factusol_sync_source = BULK_SYNC_BY_EMAIL_SOURCE
    company.factusol_synced_at = datetime.now(UTC)
    _log_backup(session, company, codcli, fields, previous, actor_id,
                action=BULK_SYNC_BY_EMAIL_ACTION,
                extra={"contact_id": contact.id,
                       "contact_email": contact.email})
    return {"result": "refreshed", "company_id": company.id}


def _company_for_orphan_contact(
    session: Session, contact: Any, codcli: str, row: dict[str, Any],
    columns: dict[str, str], actor_id: str | None,
) -> dict[str, Any]:
    """El contacto no tiene empresa: se le da una.

    Si ya hay una empresa CRM vinculada a ese CODCLI se reutiliza. Crear otra
    dejaría dos empresas apuntando al mismo cliente de FACTUSOL — la duplicidad
    que costó C-3-fix3.
    """
    from app.models.crm import Company  # noqa: PLC0415

    existing = session.scalars(
        select(Company).where(Company.factusol_company_id == codcli).limit(1)
    ).first()
    if existing is not None:
        contact.company_id = existing.id
        logger.info("factusol bulk-match by-email: contacto %s asignado a la "
                    "empresa ya vinculada %s", contact.id, existing.id)
        return {"result": "linked_existing_company", "company_id": existing.id,
                "detail": f"Asignado a «{existing.name}», ya vinculada al "
                          f"cliente {codcli}."}

    # Aquí NO hay `previous_values` que guardar: la empresa nace de cero. El
    # backup registra igualmente qué se creó, para poder deshacerlo.
    company = _new_company_from_factusol(session, codcli, row, columns)
    contact.company_id = company.id
    _log_backup(
        session, company, codcli, [], {}, actor_id,
        action=BULK_SYNC_BY_EMAIL_CREATE_ACTION,
        extra={"contact_id": contact.id, "contact_email": contact.email,
               "created_company": True, "company_name": company.name},
    )
    return {"result": "created_new_company", "company_id": company.id,
            "detail": f"Empresa «{company.name}» creada y vinculada."}


def _new_company_from_factusol(
    session: Session, codcli: str, row: dict[str, Any], columns: dict[str, str],
) -> Any:
    """Crea (y flushea) una empresa CRM con **todos** los datos de F_CLI.

    Nace completa, no solo con los campos marcados: no hay nada previo que
    preservar. Compartido por la creación para un contacto huérfano y por la
    reasignación, para que las dos no puedan divergir."""
    from app.models.crm import Company  # noqa: PLC0415

    company = Company(
        name=str(row.get(columns["name"]) or "").strip() or f"Cliente {codcli}",
        tax_id=str(row.get(columns["tax_id"]) or "").strip() or None,
        address_line=str(row.get(columns["address_line"]) or "").strip() or None,
        city=str(row.get(columns["city"]) or "").strip() or None,
        postal_code=str(row.get(columns["postal_code"]) or "").strip() or None,
        state=str(row.get(columns["state"]) or "").strip() or None,
        country="España",
        source="factusol",
        factusol_company_id=codcli,
        factusol_sync_source=BULK_SYNC_BY_EMAIL_SOURCE,
        factusol_synced_at=datetime.now(UTC),
    )
    session.add(company)
    session.flush()
    return company


def _reassign_contact_to_correct_company(
    session: Session, contact: Any, old_company: Any, codcli: str,
    row: dict[str, Any], columns: dict[str, str], actor_id: str | None,
) -> dict[str, Any]:
    """El contacto está en una empresa vinculada a **otro** CODCLI: se mueve.

    C-5-fix1 saltaba este caso por prudencia, y en el primer apply de
    producción resultó ser el 90% de las 128 omisiones: decenas de contactos
    colgaban de un único «Institut Vilatzara» (codcli 3960) cuando sus emails
    `@xtec.cat` son de escuelas distintas, cada una con su propio F_CLI. No era
    un vínculo en conflicto, era una **agrupación mal hecha en el CRM**.

    Se mueve el contacto a la empresa que le corresponde —la que ya existe con
    ese CODCLI, o una nueva creada desde F_CLI—. La empresa original **no se
    toca**: conserva su vínculo y los demás contactos, que pueden ser legítimos.
    Por eso el backup se audita sobre el *contacto*: lo único que cambia es su
    `company_id`, y revertirlo es devolverlo a `old_company_id`.
    """
    from app.models.crm import Company  # noqa: PLC0415

    target = session.scalars(
        select(Company).where(Company.factusol_company_id == codcli).limit(1)
    ).first()
    if target is not None:
        reassign_type, result = "existing", "reassigned_to_existing_company"
        detail = (f"Movido de «{old_company.name}» a «{target.name}», ya "
                  f"vinculada al cliente {codcli}.")
    else:
        target = _new_company_from_factusol(session, codcli, row, columns)
        reassign_type, result = "new_created", "reassigned_to_new_company"
        detail = (f"Movido de «{old_company.name}» a «{target.name}», creada "
                  f"desde el cliente {codcli}.")

    _log_reassign(session, contact, old_company, target, codcli,
                  reassign_type, actor_id)
    contact.company_id = target.id
    logger.info("factusol bulk-match by-email: contacto %s reasignado de %s "
                "(codcli %s) a %s (codcli %s)", contact.id, old_company.id,
                old_company.factusol_company_id, target.id, codcli)
    return {"result": result, "company_id": target.id,
            "old_company_id": old_company.id, "detail": detail}


def _log_reassign(
    session: Session, contact: Any, old_company: Any, new_company: Any,
    codcli: str, reassign_type: str, actor_id: str | None,
) -> None:
    """Backup de una reasignación.

    A diferencia de los otros tres backups, este audita un **contacto**: lo que
    cambia es `contacts.company_id`, no una empresa. Así la consulta de rollback
    es directa por `target_id = <contact_id>` (ver
    `docs/erp/factusol-bulk-match.md`)."""
    from app.models.crm import AuditLog  # noqa: PLC0415

    session.add(AuditLog(
        actor_user_id=actor_id,
        action=BULK_SYNC_BY_EMAIL_REASSIGN_ACTION,
        target_type="contact",
        target_id=contact.id,
        metadata_json=json.dumps({
            "contact_id": contact.id,
            "contact_email": contact.email,
            "old_company_id": old_company.id,
            "old_company_factusol_id": old_company.factusol_company_id,
            "new_company_id": new_company.id,
            "new_company_factusol_id": codcli,
            "reassign_type": reassign_type,
        }, ensure_ascii=False, default=str),
    ))
