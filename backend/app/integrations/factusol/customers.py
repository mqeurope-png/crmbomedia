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

### Tipo de documento, régimen de IVA y país (Tarea C · Parte 2)

Al crear el cliente BoHub fija también `IFICLI` / `IVACLI` / `TIVCLI` según el
régimen (nacional / intracomunitario / exportación, decidido por el país del
CRM y el NIF-IVA — ver `vat_regime.py`, mapeo confirmado con volcados reales)
y `PAICLI` con el ISO numérico REAL del país (tabla ISO completa, no 10
países con default España). Guard antes de escribir: las columnas de régimen
tienen que existir en la fila viva de F_CLI con el mismo tipo (entero); si el
esquema no cuadra, **no se escribe nada**. Para un cliente que YA existe,
`update_customer_regime` corrige SOLO esas columnas con `ActualizarRegistro`
(la clave + lo que cambia, nada más), previa vista previa.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.language import country_numeric, normalize_country
from app.integrations.factusol.client import FactusolClient, FactusolError
from app.integrations.factusol.vat_regime import (
    FCLI_REGIME_COLUMN_NAMES,
    REGIME_LABELS,
    fcli_changes,
    proposed_fcli_values,
    regime_columns,
    regime_for,
    regime_from_fcli_row,
    regime_reason,
)

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
    # Tarea C: tipo de documento / aplicar IVA / tipo impositivo (confirmadas
    # con volcado real; ver `vat_regime.py`).
    *FCLI_REGIME_COLUMN_NAMES,
)

#: `PAICLI` cuando el país no viene o no se reconoce: España, que es lo que
#: FACTUSOL pone por defecto en el escritorio. Solo para el ALTA sin país; un
#: país reconocido va SIEMPRE con su código real (tabla ISO completa).
DEFAULT_PAICLI = "724"


def _country_code(pais: str) -> str:
    """Cualquier valor de país (ISO2, nombre, numérico) → ISO 3166-1 numérico
    para `PAICLI`, con la tabla ISO COMPLETA (`language.country_numeric`). Un
    valor ya numérico de 3 cifras pasa tal cual. Solo lo vacío o lo que no se
    reconoce cae a 724 (con aviso en el log): antes caían ahí Noruega,
    Austria, Suiza… y el escritorio los enseñaba como España."""
    value = (pais or "").strip()
    if value.isdigit() and len(value) == 3:
        return value
    numeric = country_numeric(value) if value else None
    if numeric is not None:
        return numeric
    if value:
        logger.warning("factusol: país %r no reconocido; PAICLI=%s (España) por defecto",
                       value, DEFAULT_PAICLI)
    return DEFAULT_PAICLI


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
    # País en ISO2 (PAICLI es ISO 3166-1 numérico), con el normalizador de
    # F1-fix2: listo para el CRM y para el formulario del pedido. None si no se
    # reconoce (nunca España por defecto).
    out["pais_iso2"] = normalize_country(out.get("paicli"))
    # Régimen que codifica la ficha (IVACLI 0/2/3), o None si no es ninguno de
    # los confirmados: lo consumen la ficha de empresa y el albarán manual.
    regime = regime_from_fcli_row(row)
    out["regime"] = regime
    out["regime_label"] = REGIME_LABELS.get(regime) if regime else None
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
    elif by == "codcli":
        # Lectura por CÓDIGO (el vínculo CRM ↔ FACTUSOL): igualdad trivial,
        # columna confirmada. Un código no numérico no puede existir → [].
        if not q.isdigit():
            return []
        filtro = f"CODCLI={int(q)}"
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


def latest_customer_row(client: FactusolClient, ejercicio: str) -> dict[str, Any] | None:
    """La fila REAL de F_CLI con mayor CODCLI (la API no soporta LIMIT: se pide
    ordenado DESC y se toma la primera). Sirve de contador (`next_codcli`) y
    de PLANTILLA para el guard de esquema del alta. None con la tabla vacía."""
    rows = client.load_table(
        "F_CLI", filtro="1=1 ORDER BY CODCLI DESC", ejercicio=ejercicio,
    )
    return rows[0] if rows else None


def next_codcli(client: FactusolClient, ejercicio: str) -> str:
    """Siguiente CODCLI = max + 1. Misma estrategia que `next_codfac`: la API
    no soporta LIMIT, así que se pide ordenado DESC y se toma la primera fila.
    La race la evita el worker serializado (cola `factusol:writes`)."""
    return _next_codcli_from(latest_customer_row(client, ejercicio))


def _next_codcli_from(latest: dict[str, Any] | None) -> str:
    if latest is None:
        return "1"
    try:
        last = int(str(latest.get("CODCLI")).strip())
    except (TypeError, ValueError):
        last = 0
    return str(last + 1)


def customer_regime(data: dict[str, Any]) -> str:
    """Régimen del cliente a partir de los datos del alta: el explícito
    (`regime`) o el que sale del país + NIF-IVA (`pais`, `vat`, `nif`)."""
    explicit = str(data.get("regime") or "").strip()
    if explicit:
        return explicit
    return regime_for(
        normalize_country(data.get("pais")), vat=data.get("vat"), nif=data.get("nif"),
    )


def build_customer_payload(data: dict[str, Any], codcli: str) -> dict[str, Any]:
    """Datos mínimos → registro F_CLI. El resto de columnas las deja FACTUSOL
    con sus defaults (no inventamos valores).

    Tarea C: además del país REAL (`PAICLI`, tabla ISO completa) fija el tipo
    de documento y el régimen de IVA (`IFICLI`/`IVACLI`/`TIVCLI`) según el
    régimen del cliente — antes se quedaban en el default de FACTUSOL y el
    escritorio enseñaba «N.I.F.» + IVA nacional para un belga con NIF-IVA."""
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
        "PAICLI": _country_code(data.get("pais") or ""),
    }
    if data.get("email"):
        payload["EMACLI"] = data["email"]
    if data.get("telefono"):
        payload["TELCLI"] = data["telefono"]
    payload.update(regime_columns(customer_regime(data)))
    return payload


def regime_schema_problems(
    payload: dict[str, Any], template: dict[str, Any] | None,
) -> list[str]:
    """Guard del alta (Tarea C): cada columna de régimen del payload tiene que
    EXISTIR en la fila viva de F_CLI y ser del mismo tipo JSON (entero, como
    en los volcados reales). Una columna inexistente tumba el registro ENTERO
    en `EscribirRegistro` (gotcha nº 13) y un tipo distinto lo rechaza DELSOL:
    en ambos casos no se escribe nada. Sin fila viva no hay contra qué
    contrastar → se informa como problema (nada a ciegas)."""
    problems: list[str] = []
    wanted = [c for c in FCLI_REGIME_COLUMN_NAMES if c in payload]
    if not wanted:
        return problems
    if not template:
        return ["F_CLI sin fila viva: no se puede contrastar el esquema de "
                + ", ".join(wanted)]
    for column in wanted:
        if column not in template:
            problems.append(f"F_CLI: columna desconocida {column}")
            continue
        real = template[column]
        if real is None:
            continue
        if isinstance(real, bool) or not isinstance(real, int | float):
            problems.append(
                f"F_CLI.{column}: payload int ({payload[column]!r}), fila real "
                f"{type(real).__name__} ({real!r})"
            )
    return problems


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
    # La fila real más reciente es a la vez el contador y la plantilla del
    # guard (una sola lectura de la tabla).
    template = latest_customer_row(client, ejercicio)
    codcli = _next_codcli_from(template)
    payload = build_customer_payload(data, codcli)
    problems = regime_schema_problems(payload, template)
    if problems:
        detail = (
            f"El esquema real de F_CLI no cuadra con el registro del cliente "
            f"{payload.get('NOFCLI')!r} ({', '.join(problems)})"
        )
        logger.error("factusol cliente: NO se escribe nada — %s", detail)
        raise FactusolError(detail + ". No se ha escrito nada.")
    logger.info(
        "factusol cliente: EscribirRegistro F_CLI CODCLI=%s régimen=%s "
        "ejercicio=%s registro=%s",
        codcli, customer_regime(data), ejercicio,
        {k: (v, type(v).__name__) for k, v in payload.items()},
    )
    client.write_record("F_CLI", payload, ejercicio=ejercicio)
    logger.info("factusol: cliente creado CODCLI %s (NIF %s)", codcli, nif or "—")
    return codcli, True


def customer_row(
    client: FactusolClient, codcli: Any, *, ejercicio: str,
) -> dict[str, Any] | None:
    """Fila REAL (sin normalizar) de F_CLI por CODCLI, o None. Re-filtra en
    Python por si la API ignorase el filtro en silencio (gotcha nº 1)."""
    code = str(codcli or "").strip()
    if not code.isdigit():
        return None
    rows = client.load_table("F_CLI", filtro=f"CODCLI={int(code)}", ejercicio=ejercicio)
    for row in rows:
        if str(row.get("CODCLI") or "").strip() == code:
            return row
    return None


def regime_preview(
    row: dict[str, Any], *, country_iso2: str | None, vat: Any = None,
    nif: Any = None,
) -> dict[str, Any]:
    """Qué régimen le corresponde al cliente (por el país del CRM + NIF-IVA),
    qué codifica hoy su ficha F_CLI y qué columnas cambiarían. No escribe."""
    regime, proposed = proposed_fcli_values(country_iso2, vat=vat, nif=nif)
    current_regime = regime_from_fcli_row(row)
    changes = fcli_changes(row, proposed)
    return {
        "codcli": str(row.get("CODCLI")),
        "country_iso2": normalize_country(country_iso2) if country_iso2 else None,
        "regime": regime,
        "regime_label": REGIME_LABELS[regime],
        "reason": regime_reason(country_iso2, vat=vat, nif=nif),
        "current": {
            **{c: row.get(c) for c in (*FCLI_REGIME_COLUMN_NAMES, "PAICLI")},
            "regime": current_regime,
            "regime_label": REGIME_LABELS.get(current_regime) if current_regime else None,
        },
        "proposed": proposed,
        "changes": changes,
        "coherent": not changes,
    }


def update_customer_regime(
    client: FactusolClient, *, codcli: Any, ejercicio: str,
    country_iso2: str | None, vat: Any = None, nif: Any = None,
) -> dict[str, Any]:
    """Corrige en F_CLI el tipo de documento / régimen de IVA / país del
    cliente: lee la fila REAL, calcula lo propuesto y escribe con
    `ActualizarRegistro` SOLO la clave y las columnas que cambian (patrón
    «sobrescribir lo mínimo» de los cobros). Guard: cada columna a escribir
    existe en la fila real con el mismo tipo; si no, `FactusolError` y no se
    escribe nada. Sin cambios → `changed=False` sin escribir."""
    row = customer_row(client, codcli, ejercicio=ejercicio)
    if row is None:
        raise FactusolError(
            f"El cliente FACTUSOL nº {codcli} no existe (ejercicio {ejercicio})."
        )
    preview = regime_preview(row, country_iso2=country_iso2, vat=vat, nif=nif)
    if not preview["changes"]:
        return {**preview, "changed": False, "written": {}}
    payload: dict[str, Any] = {"CODCLI": row["CODCLI"]}
    problems: list[str] = []
    for change in preview["changes"]:
        column, value = change["column"], change["proposed"]
        if column not in row:
            problems.append(f"F_CLI: columna desconocida {column}")
            continue
        real = row[column]
        if real is not None and not isinstance(real, bool) and (
            isinstance(value, str) != isinstance(real, str)
        ):
            problems.append(
                f"F_CLI.{column}: payload {type(value).__name__} ({value!r}), "
                f"fila real {type(real).__name__} ({real!r})"
            )
            continue
        payload[column] = value
    if problems:
        detail = (
            f"El esquema real de F_CLI no cuadra con la corrección del cliente "
            f"{row.get('CODCLI')} ({', '.join(problems)})"
        )
        logger.error("factusol cliente: NO se escribe nada — %s", detail)
        raise FactusolError(detail + ". No se ha escrito nada.")
    logger.info(
        "factusol cliente %s: ActualizarRegistro F_CLI régimen=%s ejercicio=%s "
        "registro=%s", row.get("CODCLI"), preview["regime"], ejercicio,
        {k: (v, type(v).__name__) for k, v in payload.items()},
    )
    client.update_record("F_CLI", payload, ejercicio=ejercicio)
    written = {k: v for k, v in payload.items() if k != "CODCLI"}
    logger.info("factusol cliente %s: régimen corregido a %s (%s)",
                row.get("CODCLI"), preview["regime"], written)
    return {**preview, "changed": True, "written": written}


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


def get_customer(
    client: FactusolClient, codcli: Any, *, ejercicio: str,
) -> dict[str, Any] | None:
    """Cliente F_CLI por su CODCLI (el vínculo), normalizado. None si no existe."""
    hits = search_customers(
        client, str(codcli or "").strip(), by="codcli", ejercicio=ejercicio,
    )
    return hits[0] if hits else None


#: «Traer datos de FACTUSOL» (ficha de empresa): los campos de la diff más el
#: país (PAICLI → ISO2 con el normalizador de F1-fix2). FACTUSOL es la fuente
#: de verdad (decisión de Bart): se pisa TODO el mapping, no solo lo vacío. El
#: nombre cae a NOCCLI si NOFCLI viene vacío (la columna del CRM es NOT NULL).
PULL_FIELDS = (*DIFF_FIELDS, ("pais", "country", "pais_iso2"))
PULL_LABELS = {
    "nombre": "Nombre", "nif": "NIF", "direccion": "Dirección", "ciudad": "Ciudad",
    "cp": "CP", "provincia": "Provincia", "pais": "País",
}


def _pull_value(customer: dict[str, Any], label: str, fac_key: str) -> str:
    value = str(customer.get(fac_key) or "").strip()
    if label == "nombre" and not value:
        value = str(customer.get("noccli") or "").strip()
    return value


def pull_changes(company: Any, customer: dict[str, Any]) -> list[dict[str, Any]]:
    """Qué cambiaría «Traer datos de FACTUSOL», campo a campo (previsualización
    para la confirmación). Sin nombre en FACTUSOL, el nombre no se toca."""
    out: list[dict[str, Any]] = []
    for label, crm_attr, fac_key in PULL_FIELDS:
        crm_value = str(getattr(company, crm_attr, None) or "").strip()
        fac_value = _pull_value(customer, label, fac_key)
        if label == "nombre" and not fac_value:
            continue
        if crm_value != fac_value:
            out.append({"field": label, "label": PULL_LABELS[label],
                        "crm": crm_value, "factusol": fac_value})
    return out


def apply_pull(company: Any, customer: dict[str, Any]) -> list[dict[str, Any]]:
    """Sobrescribe la empresa CRM con los datos del cliente FACTUSOL y devuelve
    los cambios aplicados. Solo toca el objeto CRM: NUNCA escribe en FACTUSOL."""
    changes = pull_changes(company, customer)
    attrs = {label: crm_attr for label, crm_attr, _ in PULL_FIELDS}
    for change in changes:
        setattr(company, attrs[change["field"]], change["factusol"] or None)
    return changes


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
