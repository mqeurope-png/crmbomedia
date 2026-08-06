"""Importar al CRM los clientes F_CLI que no tiene nadie (Fase C · C-6).

Después de C-5 (conciliar lo que ya existía en los dos lados) quedan miles de
clientes en `F_CLI` que **nunca** llegaron al CRM: facturación de años que no
entró por WooCommerce, ni por formularios, ni por los imports antiguos. Existen
en la contabilidad y no existen en el CRM.

Este modo los trae. Dos tiempos, como el resto de la pantalla:

1. `dry_run_orphans` — lista los `F_CLI` cuyo CODCLI no está en
   `companies.factusol_company_id`. Solo lee.
2. `apply_import_orphans` — crea la empresa (y su contacto, si hay email) para
   los CODCLI que el operador marque.

### Dónde va la etiqueta «factusol_import»

El spec pedía etiquetar la **empresa**. En este CRM **las etiquetas son de
contacto**: existen `tags` y `contact_tags`, pero no hay tabla de etiquetas de
empresa, y `/api/companies` no tiene filtro por tag — tiene filtro por
`source`. Montar etiquetas de empresa serían migración + API + UI, fuera del
alcance de C-6.

Así que se hacen las dos cosas que sí funcionan hoy:

- `companies.source = "factusol_import"` → filtrable ya con
  `GET /api/companies?source=factusol_import`. Es el filtro operativo.
- El tag literal `factusol_import` se asigna al **contacto** creado, que es
  donde el CRM sabe guardar etiquetas.

Las empresas sin email no tienen contacto, así que solo llevan el `source`. Por
eso el filtro bueno es el de `source`: cubre el 100% del lote.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.factusol.client import FactusolClient
from app.integrations.factusol.customers import CUSTOMER_FIELDS

logger = logging.getLogger(__name__)

#: Marca del backup en el AuditLog.
IMPORT_ORPHANS_ACTION = "erp.factusol_bulk_import_orphan"

#: `companies.source` (String(40)). Es el filtro que funciona hoy:
#: `GET /api/companies?source=factusol_import`.
IMPORT_ORPHANS_SOURCE = "factusol_import"

#: `companies.factusol_sync_source` — la columna es **String(16)**, así que el
#: `bulk_import_orphans` del spec (19 caracteres) no cabe. En MySQL estricto
#: sería un error de escritura, no un truncado silencioso.
IMPORT_ORPHANS_SYNC_SOURCE = "import_orphans"

#: Etiqueta del lote. Se asigna al contacto creado (ver docstring del módulo).
IMPORT_ORPHANS_TAG = "factusol_import"

#: `contacts.first_name` es String(120) y `NOFCLI` puede venir más largo.
CONTACT_NAME_MAX_LENGTH = 120

#: ISO 3166-1 numérico → nombre de país, para `companies.country` (String(120)).
#: Solo los habituales; el resto cae a España, que es la inmensa mayoría.
COUNTRY_BY_CODE = {
    "724": "España", "620": "Portugal", "250": "Francia", "380": "Italia",
    "276": "Alemania", "826": "Reino Unido", "528": "Países Bajos",
    "056": "Bélgica", "840": "Estados Unidos",
}
DEFAULT_COUNTRY = "España"


def _text(row: dict[str, Any], column: str) -> str:
    return str(row.get(column) or "").strip()


def _country(row: dict[str, Any]) -> str:
    """`PAICLI` viene como ISO numérico («724»), a veces con ceros a la
    izquierda perdidos. Lo desconocido cae a España a propósito: el objetivo es
    no dejar el campo vacío, no adivinar."""
    code = _text(row, "PAICLI").lstrip("0") or "0"
    for key, name in COUNTRY_BY_CODE.items():
        if key.lstrip("0") == code:
            return name
    return DEFAULT_COUNTRY


def _orphan_view(row: dict[str, Any]) -> dict[str, Any]:
    """Fila del dry-run: los campos de F_CLI tal cual, en minúscula."""
    out = {k.lower(): row.get(k) for k in CUSTOMER_FIELDS}
    out["codcli"] = str(row.get("CODCLI")) if row.get("CODCLI") is not None else None
    # El nombre de la empresa: el fiscal manda, el comercial es el respaldo.
    out["nofcli"] = _text(row, "NOFCLI") or _text(row, "NOCCLI") or None
    out["will_create_contact"] = bool(_text(row, "EMACLI"))
    return out


def _linked_codclis(session: Session) -> set[str]:
    """CODCLI ya en uso por alguna empresa del CRM.

    Una sola consulta: preguntar por cada F_CLI serían 4 500 SELECTs."""
    from app.models.crm import Company  # noqa: PLC0415

    return {
        str(x) for x in session.scalars(
            select(Company.factusol_company_id)
            .where(Company.factusol_company_id.is_not(None))
        ) if x
    }


def dry_run_orphans(
    session: Session, client: FactusolClient, *, ejercicio: str,
    only_with_email: bool = False,
) -> dict[str, Any]:
    """Lista los clientes de FACTUSOL que no tiene ninguna empresa del CRM.

    **No escribe nada.** `only_with_email` deja fuera los que no traen `EMACLI`:
    de esos solo saldría una empresa sin nadie con quien hablar.
    """
    rows = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
    linked = _linked_codclis(session)

    orphans, with_email = [], 0
    for row in rows:
        codcli = str(row.get("CODCLI")) if row.get("CODCLI") is not None else None
        if not codcli or codcli in linked:
            continue
        has_email = bool(_text(row, "EMACLI"))
        if only_with_email and not has_email:
            continue
        if has_email:
            with_email += 1
        orphans.append(_orphan_view(row))

    logger.info("factusol import-orphans: %d clientes F_CLI, %d ya vinculados, "
                "%d huérfanos (%d con email)",
                len(rows), len(linked), len(orphans), with_email)
    return {
        "total_factusol_clientes": len(rows),
        "linked_already": len(linked),
        "orphans_to_import": len(orphans),
        "with_email": with_email,
        "without_email": len(orphans) - with_email,
        "orphans": orphans,
        "ejercicio": ejercicio,
    }


def apply_import_orphans(
    session: Session, client: FactusolClient, *, ejercicio: str,
    codclis: list[str], create_contacts_if_email: bool = True,
    actor_id: str | None = None,
) -> dict[str, Any]:
    """Crea empresa (y contacto) para los CODCLI marcados.

    Un CODCLI por transacción: en un lote de cientos, abortar todo por un caso
    raro obligaría a repetir la revisión entera.

    Desenlaces, en `results`:

    - `imported_company_and_contact` — empresa + contacto creados.
    - `imported_company_only` — empresa creada sin contacto. `contact_skipped`
      dice por qué: `no_email`, `email_taken` o `disabled`.
    - `skipped_race` — entre el dry-run y el apply alguien vinculó ese CODCLI.
      No se pisa, y no es un error.
    """
    from app.models.crm import Company, Contact  # noqa: PLC0415

    rows = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
    by_codcli = {str(r.get("CODCLI")): r for r in rows}

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for raw in codclis:
        codcli = str(raw or "")
        try:
            row = by_codcli.get(codcli)
            if row is None:
                raise ValueError(f"el cliente FACTUSOL {codcli} no existe")

            # Guard anti-carrera: se relee por cada CODCLI, no del set del
            # dry-run. Un lote largo tarda minutos y el CRM sigue vivo.
            taken = session.scalars(
                select(Company).where(Company.factusol_company_id == codcli)
                .limit(1)
            ).first()
            if taken is not None:
                results.append({
                    "codcli": codcli, "result": "skipped_race",
                    "company_id": taken.id, "contact_id": None,
                    "detail": (f"«{taken.name}» ya está vinculada al cliente "
                               f"{codcli}."),
                })
                continue

            company = _company_from_row(session, codcli, row)
            contact, skipped = _contact_from_row(
                session, company, row,
                enabled=create_contacts_if_email, actor_id=actor_id,
            )
            _log_import(session, codcli, company, contact, actor_id)
            session.commit()
            results.append({
                "codcli": codcli,
                "result": ("imported_company_and_contact" if contact is not None
                           else "imported_company_only"),
                "company_id": company.id,
                "contact_id": contact.id if contact is not None else None,
                **({"contact_skipped": skipped} if skipped else {}),
            })
        except Exception as exc:  # noqa: BLE001 — un fallo no tumba el lote
            session.rollback()
            logger.warning("factusol import-orphans: %s KO: %s", codcli, exc)
            errors.append({"codcli": codcli, "error": str(exc)[:200]})

    def _count(result: str) -> int:
        return sum(1 for r in results if r["result"] == result)

    _ = Contact  # importado por claridad del contrato de la función
    return {
        "imported_company_and_contact": _count("imported_company_and_contact"),
        "imported_company_only": _count("imported_company_only"),
        "skipped_race": _count("skipped_race"),
        "imported": sum(1 for r in results if r["result"].startswith("imported")),
        "results": results,
        "errors": errors,
    }


def _company_from_row(
    session: Session, codcli: str, row: dict[str, Any],
) -> Any:
    """Empresa CRM con los datos limpios de F_CLI. Nace ya vinculada."""
    from app.models.crm import Company  # noqa: PLC0415

    company = Company(
        name=_text(row, "NOFCLI") or _text(row, "NOCCLI") or f"Cliente {codcli}",
        tax_id=_text(row, "NIFCLI") or None,
        address_line=_text(row, "DOMCLI") or None,
        city=_text(row, "POBCLI") or None,
        postal_code=_text(row, "CPOCLI") or None,
        state=_text(row, "PROCLI") or None,
        country=_country(row),
        source=IMPORT_ORPHANS_SOURCE,
        factusol_company_id=codcli,
        factusol_sync_source=IMPORT_ORPHANS_SYNC_SOURCE,
        factusol_synced_at=datetime.now(UTC),
    )
    session.add(company)
    session.flush()
    return company


def _contact_from_row(
    session: Session, company: Any, row: dict[str, Any], *,
    enabled: bool, actor_id: str | None,
) -> tuple[Any | None, str | None]:
    """Contacto de la empresa recién creada. `(contacto | None, motivo)`.

    Sin `EMACLI` no se crea: un contacto con solo el nombre de la empresa no
    aporta nada y ensucia el CRM.

    `contacts.email` es **UNIQUE**. Si ese email ya es de otro contacto no se
    intenta crear —el INSERT reventaría y se llevaría por delante la empresa,
    que sí queremos—, ni se le roba a su empresa actual. Se deja la empresa y se
    dice por qué en `contact_skipped`.
    """
    from app.models.crm import Contact  # noqa: PLC0415
    from app.repositories.crm import (  # noqa: PLC0415
        assign_tag_to_contact,
        upsert_tag,
    )

    if not enabled:
        return None, "disabled"
    email = _text(row, "EMACLI")
    if not email:
        return None, "no_email"
    already = session.scalars(
        select(Contact).where(Contact.email == email).limit(1)
    ).first()
    if already is not None:
        logger.info("factusol import-orphans: %s ya es de otro contacto (%s); "
                    "empresa creada sin contacto", email, already.id)
        return None, "email_taken"

    contact = Contact(
        # Sin apellido a propósito: F_CLI guarda razones sociales, no personas.
        # El operador lo edita después si detrás hay alguien concreto.
        first_name=(_text(row, "NOFCLI") or _text(row, "NOCCLI")
                    or company.name)[:CONTACT_NAME_MAX_LENGTH],
        email=email,
        phone=_text(row, "TELCLI") or None,
        company_id=company.id,
        origin=IMPORT_ORPHANS_SOURCE,
    )
    session.add(contact)
    session.flush()

    tag, _created = upsert_tag(session, name=IMPORT_ORPHANS_TAG,
                               created_by_user_id=actor_id)
    assign_tag_to_contact(session, contact_id=contact.id, tag_id=tag.id,
                          assigned_by_user_id=actor_id,
                          source=IMPORT_ORPHANS_SOURCE)
    return contact, None


def _log_import(
    session: Session, codcli: str, company: Any, contact: Any | None,
    actor_id: str | None,
) -> None:
    """Backup en el AuditLog. Aquí no hay `previous_values`: no existía nada.

    Lo que se guarda son los IDs creados, que es lo que hace falta para
    deshacerlo (ver `docs/erp/factusol-bulk-match.md`)."""
    from app.models.crm import AuditLog  # noqa: PLC0415

    session.add(AuditLog(
        actor_user_id=actor_id,
        action=IMPORT_ORPHANS_ACTION,
        target_type="company",
        target_id=company.id,
        metadata_json=json.dumps({
            "codcli": codcli,
            "created_company_id": company.id,
            "created_contact_id": contact.id if contact is not None else None,
            "company_name": company.name,
            "tag": IMPORT_ORPHANS_TAG if contact is not None else None,
        }, ensure_ascii=False, default=str),
    ))
