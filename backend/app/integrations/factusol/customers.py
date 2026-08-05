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
2. **Deduplica** antes de escribir: consulta `NIFCLI` y, si ya existe, devuelve
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
#: Nombres REALES verificados contra la F_CLI de Bomedia (4533 clientes, 2026):
#: el nombre va en `NOFCLI` (fiscal) y `NOCCLI` (comercial), el NIF en `NIFCLI`,
#: el domicilio en `DOMCLI` y el país en `PAICLI` (ISO 3166-1 numérico).
CUSTOMER_FIELDS = (
    "CODCLI", "NIFCLI", "NOFCLI", "NOCCLI", "DOMCLI", "POBCLI",
    "CPOCLI", "PROCLI", "PAICLI", "EMACLI", "TELCLI",
)

#: ISO 3166-1 numérico de los países habituales; el resto cae a 724 (España).
_COUNTRY_CODES = {
    "ES": "724", "PT": "620", "FR": "250", "IT": "380", "DE": "276",
    "GB": "826", "UK": "826", "NL": "528", "BE": "056", "US": "840",
}


def _country_code(pais: str) -> str:
    """Alfa-2 → numérico ISO para `PAICLI`. Un valor ya numérico pasa tal cual.
    Default 724 (España)."""
    value = (pais or "ES").upper().strip()
    if value.isdigit() and len(value) == 3:
        return value
    return _COUNTRY_CODES.get(value, "724")


def _sql_escape(value: str) -> str:
    """Escapa un literal para el `filtro` de CargaTabla, que es un fragmento
    SQL WHERE crudo. Sin esto, un NIF con comilla rompe (o inyecta) la query."""
    return (value or "").replace("'", "''")


def _row_to_customer(row: dict[str, Any]) -> dict[str, Any]:
    out = {k.lower(): row.get(k) for k in CUSTOMER_FIELDS}
    out["codcli"] = str(out.get("codcli")) if out.get("codcli") is not None else None
    # Alias cómodos para el frontend: el comercial manda sobre el fiscal para
    # mostrar, y `nif` evita arrastrar el nombre de columna por toda la UI.
    out["nombre"] = (
        str(out.get("noccli") or out.get("nofcli") or "").strip() or None
    )
    out["nif"] = out.get("nifcli")
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
        filtro = f"UPPER(NIFCLI)=UPPER('{safe}')"
    elif by == "email":
        filtro = f"UPPER(EMACLI)=UPPER('{safe}')"
    elif by == "name":
        # Fiscal Y comercial: uno de los dos puede estar vacío o diferir.
        filtro = (
            f"UPPER(NOFCLI) LIKE UPPER('%{safe}%') "
            f"OR UPPER(NOCCLI) LIKE UPPER('%{safe}%')"
        )
    else:
        raise ValueError(f"criterio de búsqueda inválido: {by!r}")
    rows = client.load_table("F_CLI", filtro=filtro, ejercicio=ejercicio)
    return [_row_to_customer(r) for r in rows[:SEARCH_NAME_LIMIT]]


#: FACTUSOL guarda hasta 4 direcciones ADICIONALES por cliente, con sufijo
#: numérico: `ACO1CLI`…`ACO4CLI` (nombre), `ADOxCLI` (domicilio), `APOxCLI`
#: (población), `ACPxCLI` (CP), `APRxCLI` (provincia), `APAxCLI` (país). Son las
#: que el escritorio enseña en el botón «Direcciones». Una dirección cuenta como
#: configurada si su `ACOxCLI` no está vacío.
MAX_EXTRA_ADDRESSES = 4
#: `{clave expuesta: prefijo de columna}`. El sufijo `xCLI` se compone con el
#: índice: `ADO` + `2` + `CLI` → `ADO2CLI`.
ADDRESS_COLUMN_PREFIXES = {
    "nombre": "ACO", "direccion": "ADO", "ciudad": "APO",
    "cp": "ACP", "provincia": "APR", "pais": "APA",
}


def customer_addresses(
    client: FactusolClient, codcli: str, *, ejercicio: str,
) -> list[dict[str, Any]]:
    """Direcciones del cliente: la principal + las adicionales configuradas.

    La principal va siempre primero, con `codigo=0`. De las adicionales solo se
    devuelven las que tengan nombre (`ACOxCLI`), que es como FACTUSOL marca que
    esa dirección existe.

    Sirve para que el operador mande la proforma a una delegación distinta de la
    sede sin salir del CRM. **Solo lectura**: elegir entre las que ya existan,
    nunca editarlas (eso se hace en FACTUSOL).
    """
    rows = client.load_table(
        "F_CLI", filtro=f"CODCLI={int(codcli)}", ejercicio=ejercicio,
    ) if str(codcli).strip().isdigit() else []
    if not rows:
        return []
    row = rows[0]

    def _text(column: str) -> str:
        return str(row.get(column) or "").strip()

    out = [{
        "codigo": 0,
        "nombre": "(principal)",
        "direccion": _text("DOMCLI"),
        "ciudad": _text("POBCLI"),
        "cp": _text("CPOCLI"),
        "provincia": _text("PROCLI"),
        "pais": _text("PAICLI"),
    }]
    for i in range(1, MAX_EXTRA_ADDRESSES + 1):
        extra = {
            key: _text(f"{prefix}{i}CLI")
            for key, prefix in ADDRESS_COLUMN_PREFIXES.items()
        }
        if extra["nombre"]:
            out.append({"codigo": i, **extra})
    return out


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
    nombre = (data.get("nombre") or "").strip()
    payload = {
        "CODCLI": codcli,
        "NOFCLI": nombre,          # nombre fiscal (razón social)
        "NOCCLI": nombre,          # comercial: por defecto igual al fiscal
        "NIFCLI": data.get("nif") or "",
        "DOMCLI": data.get("direccion") or "",
        "POBCLI": data.get("ciudad") or "",
        "CPOCLI": data.get("cp") or "",
        "PROCLI": data.get("provincia") or "",
        "PAICLI": _country_code(data.get("pais") or "ES"),
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
    # NOFCLI (fiscal) es lo canónico para comparar con el nombre del CRM.
    ("nombre", "name", "nofcli"),
    ("nif", "tax_id", "nifcli"),
    ("direccion", "address_line", "domcli"),
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
