"""Sync de clientes CRM ↔ FACTUSOL (Fase C · C-3).

**Solo vínculo, nunca pisamos datos.** El CRM y FACTUSOL pueden tener versiones
distintas de la dirección o el teléfono de un cliente; ambas pueden ser
legítimas. C-3 guarda el vínculo (`companies.factusol_company_id` /
`contacts.factusol_contact_id`) y muestra las diferencias, pero no
auto-sincroniza nada.

### Quién crea el cliente en FACTUSOL (contexto del bug de C-2-fix1)

Una **app externa WooCommerce→FACTUSOL** replica cada pedido Woo como Pedido de
Cliente (F_PCL) **creando ella el cliente**. El CRM históricamente intentaba
crearlo también (`ensure_customer_in_factusol`, C-1) y reventaba con
`BDEscribirRegistroError` al chocar con el que ya existía; se retiró en
C-2-fix1. Por eso `create_customer`:

1. **Rechaza** crear clientes de pedidos de origen WooCommerce — ahí manda la
   app externa; el CRM solo vincula.
2. **Deduplica** antes de escribir: consulta `CIFCLI` y, si ya existe, devuelve
   el CODCLI existente en vez de crear un duplicado.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.factusol.client import FactusolClient, FactusolError

logger = logging.getLogger(__name__)

#: Máximo de resultados devueltos en la búsqueda por nombre. La API DELSOL NO
#: soporta LIMIT en el filtro, así que se recorta en Python.
SEARCH_NAME_LIMIT = 50

#: Campos de F_CLI que exponemos (los mínimos para identificar y crear).
CUSTOMER_FIELDS = (
    "CODCLI", "NOMCLI", "CIFCLI", "DIRCLI", "POBCLI",
    "CPOCLI", "PROCLI", "NACCLI", "EMACLI", "TELCLI",
)


def _sql_escape(value: str) -> str:
    """Escapa un literal para el `filtro` de CargaTabla, que es un fragmento
    SQL WHERE crudo. Sin esto, un NIF con comilla rompe (o inyecta) la query."""
    return (value or "").replace("'", "''")


def _row_to_customer(row: dict[str, Any]) -> dict[str, Any]:
    out = {k.lower(): row.get(k) for k in CUSTOMER_FIELDS}
    out["codcli"] = str(out.get("codcli")) if out.get("codcli") is not None else None
    return out


def search_customers(
    client: FactusolClient, query: str, *, by: str = "nif", ejercicio: str,
) -> list[dict[str, Any]]:
    """Busca en F_CLI por NIF (exacto), email (exacto) o nombre (LIKE).

    Devuelve la lista de clientes normalizados (claves en minúscula). La
    búsqueda por nombre se recorta a `SEARCH_NAME_LIMIT` en Python."""
    q = (query or "").strip()
    if not q:
        return []
    safe = _sql_escape(q)
    if by == "nif":
        filtro = f"UPPER(CIFCLI)=UPPER('{safe}')"
    elif by == "email":
        filtro = f"UPPER(EMACLI)=UPPER('{safe}')"
    elif by == "name":
        filtro = f"UPPER(NOMCLI) LIKE UPPER('%{safe}%')"
    else:
        raise ValueError(f"criterio de búsqueda inválido: {by!r}")
    rows = client.load_table("F_CLI", filtro=filtro, ejercicio=ejercicio)
    return [_row_to_customer(r) for r in rows[:SEARCH_NAME_LIMIT]]


def crm_links_for(session: Session, codclis: list[str]) -> dict[str, dict[str, str]]:
    """`{codcli: {"type": "company"|"contact", "id": …, "name": …}}` para los
    CODCLI que ya están vinculados en el CRM (cross-check de la búsqueda)."""
    from app.models.crm import Company, Contact  # noqa: PLC0415

    if not codclis:
        return {}
    out: dict[str, dict[str, str]] = {}
    for c in session.scalars(
        select(Company).where(Company.factusol_company_id.in_(codclis))
    ):
        out[str(c.factusol_company_id)] = {
            "type": "company", "id": c.id, "name": c.name,
        }
    for c in session.scalars(
        select(Contact).where(Contact.factusol_contact_id.in_(codclis))
    ):
        # Una empresa vinculada gana sobre un contacto con el mismo CODCLI.
        out.setdefault(str(c.factusol_contact_id), {
            "type": "contact", "id": c.id,
            "name": " ".join(x for x in (c.first_name, c.last_name) if x).strip(),
        })
    return out


def next_codcli(client: FactusolClient, ejercicio: str) -> str:
    """Siguiente CODCLI = max + 1. Misma estrategia que `next_codfac`: la API
    no soporta LIMIT, así que se pide ordenado DESC y se toma la primera fila.
    La race la evita el worker serializado (cola `factusol:writes`)."""
    rows = client.load_table(
        "F_CLI", filtro="1=1 ORDER BY CODCLI DESC", ejercicio=ejercicio,
    )
    if not rows:
        return "1"
    try:
        last = int(str(rows[0].get("CODCLI")).strip())
    except (TypeError, ValueError):
        last = 0
    return str(last + 1)


def build_customer_payload(data: dict[str, Any], codcli: str) -> dict[str, Any]:
    """Datos mínimos → registro F_CLI. El resto de columnas las deja FACTUSOL
    con sus defaults (no inventamos valores)."""
    payload = {
        "CODCLI": codcli,
        "NOMCLI": data.get("nombre") or "",
        "CIFCLI": data.get("nif") or "",
        "DIRCLI": data.get("direccion") or "",
        "POBCLI": data.get("ciudad") or "",
        "CPOCLI": data.get("cp") or "",
        "PROCLI": data.get("provincia") or "",
        "NACCLI": data.get("pais") or "ES",
    }
    if data.get("email"):
        payload["EMACLI"] = data["email"]
    if data.get("telefono"):
        payload["TELCLI"] = data["telefono"]
    return payload


def find_by_nif(
    client: FactusolClient, nif: str, *, ejercicio: str,
) -> dict[str, Any] | None:
    """Cliente F_CLI con ese NIF, o None. Es el guard anti-duplicado."""
    hits = search_customers(client, nif, by="nif", ejercicio=ejercicio)
    return hits[0] if hits else None


def create_customer(
    client: FactusolClient, data: dict[str, Any], *, ejercicio: str,
) -> tuple[str, bool]:
    """Crea el cliente en F_CLI si no existe ya con ese NIF.

    Devuelve `(codcli, created)`. `created=False` significa que ya existía y se
    devuelve el CODCLI existente para vincularlo — esto es lo que evita el
    `BDEscribirRegistroError` de C-2-fix1 (la app externa Woo→FACTUSOL pudo
    haberlo creado antes que nosotros).
    """
    nif = (data.get("nif") or "").strip()
    if nif:
        existing = find_by_nif(client, nif, ejercicio=ejercicio)
        if existing and existing.get("codcli"):
            logger.info("factusol: cliente con NIF %s ya existe (CODCLI %s)",
                        nif, existing["codcli"])
            return str(existing["codcli"]), False
    codcli = next_codcli(client, ejercicio)
    client.write_record("F_CLI", build_customer_payload(data, codcli),
                        ejercicio=ejercicio)
    logger.info("factusol: cliente creado CODCLI %s (NIF %s)", codcli, nif or "—")
    return codcli, True


#: Campos comparables CRM ↔ FACTUSOL para el detector de divergencias.
DIFF_FIELDS = (
    ("nombre", "name", "nomcli"),
    ("nif", "tax_id", "cifcli"),
    ("direccion", "address_line", "dircli"),
    ("ciudad", "city", "pobcli"),
    ("cp", "postal_code", "cpocli"),
    ("provincia", "state", "procli"),
)


def diff_company(company: Any, customer: dict[str, Any]) -> list[dict[str, Any]]:
    """Diferencias campo a campo entre la empresa CRM y el cliente FACTUSOL.
    Solo informa — la decisión de sincronizar es del operador."""
    out = []
    for label, crm_attr, fac_key in DIFF_FIELDS:
        crm_value = (getattr(company, crm_attr, None) or "").strip()
        fac_value = str(customer.get(fac_key) or "").strip()
        if crm_value.casefold() != fac_value.casefold():
            out.append({"field": label, "crm": crm_value, "factusol": fac_value})
    return out


def link_to_crm(
    session: Session, *, crm_type: str, crm_id: str, codcli: str,
) -> Any:
    """Vincula un CODCLI a una empresa o contacto del CRM. Lanza
    `FactusolError` si ese CODCLI ya está vinculado a OTRO registro."""
    from app.models.crm import Company, Contact  # noqa: PLC0415

    codcli = str(codcli).strip()
    taken = crm_links_for(session, [codcli]).get(codcli)
    if taken and not (taken["type"] == crm_type and taken["id"] == crm_id):
        raise FactusolError(
            f"El cliente FACTUSOL {codcli} ya está vinculado a "
            f"{taken['type']} «{taken['name']}»."
        )
    if crm_type == "company":
        row = session.get(Company, crm_id)
        if row is None:
            raise FactusolError(f"Empresa {crm_id!r} no existe")
        row.factusol_company_id = codcli
        row.factusol_sync_source = "erp_link"
    elif crm_type == "contact":
        row = session.get(Contact, crm_id)
        if row is None:
            raise FactusolError(f"Contacto {crm_id!r} no existe")
        row.factusol_contact_id = codcli
    else:
        raise FactusolError(f"crm_type inválido: {crm_type!r}")
    return row
