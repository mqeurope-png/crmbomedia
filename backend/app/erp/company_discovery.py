"""Discovery (SOLO LECTURA) del estado de las empresas del CRM frente a
FACTUSOL: cuántas existen de verdad en F_CLI, cuántas son ruido y cuáles
tienen negocio vivo. No escribe nada, ni en el CRM ni en FACTUSOL.

Cruce por NIF / VAT normalizado (`nif_key`): mayúsculas, sin espacios, puntos,
guiones ni barras, y sin el prefijo de país si el número empieza por un
código de la UE (como en VIES): `ESB12345678` ≡ `B12345678`, `PT503420506` ≡
`503420506`. Se comparan el CIF (`tax_id`) y el VAT (`vat`) del CRM contra
`NIFCLI` de F_CLI. Ejemplo real: CRM «ONLYGUAY SC» `J70876677` casa con
FACTUSOL 3734 «ONLYGUAY S C» `J70876677` aunque el nombre difiera.

Actividad viva de una empresa (lo que la protege de archivarse):
- pedidos (cualquier estado; se desglosan: abiertos, por facturar,
  facturados, completados, ocultos del seguimiento),
- proformas en FACTUSOL (F_PRE por el CODCLI que casa o el vinculado),
- tareas abiertas (de la empresa o de sus contactos),
- contactos,
- email o actividad (eventos) recientes en sus contactos (`recent_days`).
`alive_any` cuenta todo lo anterior (la lista de Bart); `alive_strict` deja
fuera «solo tiene contactos», para ver cuánto pesa ese criterio.

La salida es un `Report` con los recuentos, una fila por empresa (para el
CSV) y la lista de casos ambiguos a revisar a mano.
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import Order
from app.integrations.factusol.vat_regime import _VAT_PREFIX_TO_ISO2
from app.models.crm import ActivityEvent, Company, Contact, EmailMessage, Task

RECENT_DAYS_DEFAULT = 180
#: Por debajo de esto, el nombre del CRM y el de FACTUSOL se parecen tan poco
#: que conviene mirar el cruce por NIF a mano.
NAME_SIMILARITY_LOW = 0.45
OPEN_TASK_STATUSES = ("pending", "in_progress", "open")

_SEP_RE = re.compile(r"[\s.\-/]")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")


# --- normalización ------------------------------------------------------------------


def nif_key(value: Any) -> str | None:
    """CIF / NIF / VAT comparable: mayúsculas, sin separadores y sin el prefijo
    de país de la UE si el número empieza por él (`ESB12345678` → `B12345678`,
    `PT 503.420.506` → `503420506`). None si queda vacío."""
    raw = _SEP_RE.sub("", str(value or "")).upper()
    if not raw:
        return None
    rest = raw[2:]
    if (
        len(raw) > 3 and raw[:2] in _VAT_PREFIX_TO_ISO2 and rest.isalnum()
        and any(ch.isdigit() for ch in rest)
    ):
        return rest
    return raw


def nif_sql_variants(value: Any) -> list[str]:
    """Formas con las que probar un NIF/NIF-IVA en SQL **por igualdad**.

    `nif_key` es la clave canónica (desnuda, sin prefijo de país), pero SQL no
    puede calcularla: en la columna puede estar guardada la forma desnuda
    (`91523447399`) o la prefijada (`FR91523447399`), y una `IN` con una sola
    de las dos pierde la otra. Se devuelven la desnuda, la que traiga el valor
    y la desnuda con CADA prefijo de país de la UE, de modo que la consulta
    encuentre la fila esté como esté guardada. El filtro FINO se hace después
    en Python con `nif_key` (aquí solo se ensancha el prefiltro).

    Orden estable: primero la forma que trae el valor, luego la desnuda, luego
    las prefijadas — para que un acierto exacto salga antes."""
    bare = nif_key(value)
    if not bare:
        return []
    raw = _SEP_RE.sub("", str(value or "")).upper()
    out: list[str] = []
    for cand in (raw, bare):
        if cand and cand not in out:
            out.append(cand)
    out.extend(
        f"{prefix}{bare}" for prefix in sorted(_VAT_PREFIX_TO_ISO2)
        if f"{prefix}{bare}" not in out
    )
    return out


def nif_looks_malformed(key: str | None) -> bool:
    """Demasiado corto, sin ningún dígito o con caracteres raros: no es un
    identificador fiscal con el que fiarse del cruce."""
    if not key:
        return False
    return len(key) < 5 or not any(ch.isdigit() for ch in key) or not key.isalnum()


def name_similarity(a: Any, b: Any) -> float | None:
    """Parecido entre nombres (0-1) ignorando puntuación, espacios y
    mayúsculas: «ONLYGUAY SC» vs «ONLYGUAY S C» → 1.0."""
    ka = _NON_ALNUM_RE.sub("", str(a or "").upper())
    kb = _NON_ALNUM_RE.sub("", str(b or "").upper())
    if not ka or not kb:
        return None
    return round(SequenceMatcher(None, ka, kb).ratio(), 3)


# --- FACTUSOL (F_CLI) ---------------------------------------------------------------------


@dataclass(frozen=True)
class FactusolCustomer:
    codcli: str
    nif: str
    nif_key: str | None
    nombre: str


def factusol_customers(rows: list[dict[str, Any]]) -> list[FactusolCustomer]:
    out: list[FactusolCustomer] = []
    for row in rows:
        codcli = str(row.get("CODCLI") or "").strip()
        if not codcli:
            continue
        nif = str(row.get("NIFCLI") or "").strip()
        nombre = str(row.get("NOFCLI") or "").strip() or str(row.get("NOCCLI") or "").strip()
        out.append(FactusolCustomer(codcli=codcli, nif=nif, nif_key=nif_key(nif), nombre=nombre))
    return out


def index_by_nif(customers: list[FactusolCustomer]) -> dict[str, list[FactusolCustomer]]:
    index: dict[str, list[FactusolCustomer]] = defaultdict(list)
    for c in customers:
        if c.nif_key:
            index[c.nif_key].append(c)
    return index


# --- filas del informe ------------------------------------------------------------------


@dataclass
class CompanyRow:
    company_id: str
    name: str
    is_active: bool
    tax_id: str | None
    vat: str | None
    keys: list[str] = field(default_factory=list)
    nif_malformed: bool = False
    factusol_codclis: list[str] = field(default_factory=list)
    factusol_nombre: str | None = None
    name_similarity: float | None = None
    linked_codcli: str | None = None
    link_status: str = ""
    crm_duplicate_group: str | None = None
    contacts: int = 0
    orders_total: int = 0
    orders_open: int = 0
    orders_por_facturar: int = 0
    orders_invoiced: int = 0
    orders_completed: int = 0
    orders_hidden: int = 0
    proformas: int = 0
    tasks_open: int = 0
    tasks_total: int = 0
    last_email_at: str | None = None
    last_activity_at: str | None = None
    alive_any: bool = False
    alive_strict: bool = False
    in_factusol: bool = False
    classification: str = ""
    notes: list[str] = field(default_factory=list)


CSV_COLUMNS: tuple[str, ...] = (
    "company_id", "name", "is_active", "tax_id", "vat", "keys", "nif_malformed",
    "factusol_codclis", "factusol_nombre", "name_similarity", "linked_codcli", "link_status",
    "crm_duplicate_group", "contacts", "orders_total", "orders_open", "orders_por_facturar",
    "orders_invoiced", "orders_completed", "orders_hidden", "proformas", "tasks_open",
    "tasks_total", "last_email_at", "last_activity_at", "alive_any", "alive_strict",
    "in_factusol", "classification", "notes",
)


@dataclass
class Report:
    generated_at: str
    recent_days: int
    counts: dict[str, Any]
    rows: list[CompanyRow]
    ambiguous: list[dict[str, Any]]


# --- actividad del CRM (solo lectura) -----------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def load_activity(
    session: Session, *, now: datetime, recent_days: int,
) -> dict[str, dict[str, Any]]:
    """Actividad por empresa en un puñado de queries (todo lectura)."""
    cutoff = now - timedelta(days=recent_days)
    act: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "contacts": 0, "orders_total": 0, "orders_open": 0, "orders_por_facturar": 0,
        "orders_invoiced": 0, "orders_completed": 0, "orders_hidden": 0,
        "tasks_open": 0, "tasks_total": 0, "last_email_at": None, "last_activity_at": None,
    })

    contact_company: dict[str, str] = {}
    for contact_id, company_id in session.execute(
        select(Contact.id, Contact.company_id).where(Contact.company_id.is_not(None))
    ):
        contact_company[contact_id] = company_id
        act[company_id]["contacts"] += 1

    for company_id, approved_at, invoice_status, invoice_number, completed_at, hidden_at in (
        session.execute(select(
            Order.company_id, Order.approved_at, Order.invoice_status,
            Order.factusol_invoice_number, Order.completed_at, Order.seguimiento_excluded_at,
        ).where(Order.company_id.is_not(None)))
    ):
        a = act[company_id]
        a["orders_total"] += 1
        status = getattr(invoice_status, "value", invoice_status)
        invoiced = bool(invoice_number) or status in (
            "invoiced_by_erp", "generated", "already_invoiced_externally",
        )
        if hidden_at is not None:
            a["orders_hidden"] += 1
        if completed_at is not None:
            a["orders_completed"] += 1
        if invoiced:
            a["orders_invoiced"] += 1
        elif completed_at is None and hidden_at is None:
            a["orders_open"] += 1
            if approved_at is not None:
                a["orders_por_facturar"] += 1

    for company_id, contact_id, status in session.execute(
        select(Task.company_id, Task.contact_id, Task.status)
    ):
        target = company_id or contact_company.get(contact_id or "")
        if not target:
            continue
        a = act[target]
        a["tasks_total"] += 1
        if getattr(status, "value", status) in OPEN_TASK_STATUSES:
            a["tasks_open"] += 1

    for contact_id, sent_at in session.execute(
        select(EmailMessage.contact_id, EmailMessage.sent_at)
        .where(EmailMessage.contact_id.is_not(None), EmailMessage.sent_at.is_not(None))
    ):
        target = contact_company.get(contact_id)
        if not target:
            continue
        sent_at = _aware(sent_at)
        a = act[target]
        if a["last_email_at"] is None or sent_at > a["last_email_at"]:
            a["last_email_at"] = sent_at

    for contact_id, occurred_at in session.execute(
        select(ActivityEvent.contact_id, ActivityEvent.occurred_at)
    ):
        target = contact_company.get(contact_id)
        if not target:
            continue
        occurred_at = _aware(occurred_at)
        a = act[target]
        if a["last_activity_at"] is None or occurred_at > a["last_activity_at"]:
            a["last_activity_at"] = occurred_at

    for a in act.values():
        a["email_recent"] = bool(a["last_email_at"] and a["last_email_at"] >= cutoff)
        a["activity_recent"] = bool(a["last_activity_at"] and a["last_activity_at"] >= cutoff)
    return act


# --- el informe -------------------------------------------------------------------------------


def build_report(
    session: Session, fcli_rows: list[dict[str, Any]], *,
    proformas_by_codcli: dict[str, int] | None = None,
    now: datetime | None = None, recent_days: int = RECENT_DAYS_DEFAULT,
) -> Report:
    now = now or datetime.now(UTC)
    customers = factusol_customers(fcli_rows)
    by_nif = index_by_nif(customers)
    by_codcli = {c.codcli: c for c in customers}
    by_codcli_int = {str(int(c.codcli)): c for c in customers if c.codcli.isdigit()}
    proformas = {str(k): int(v) for k, v in (proformas_by_codcli or {}).items()}
    activity = load_activity(session, now=now, recent_days=recent_days)

    companies = list(session.scalars(select(Company).order_by(Company.name.asc())))

    # Duplicados del CRM: misma clave (CIF o VAT normalizado) en varias fichas.
    key_owners: dict[str, list[str]] = defaultdict(list)
    for c in companies:
        for key in {k for k in (nif_key(c.tax_id), nif_key(c.vat)) if k}:
            key_owners[key].append(c.id)
    dup_groups = {key: ids for key, ids in key_owners.items() if len(ids) > 1}
    dup_of: dict[str, str] = {}
    for key, ids in dup_groups.items():
        for cid in ids:
            dup_of.setdefault(cid, key)

    rows: list[CompanyRow] = []
    ambiguous: list[dict[str, Any]] = []
    for c in companies:
        keys = [k for k in dict.fromkeys(nif_key(v) for v in (c.tax_id, c.vat)) if k]
        row = CompanyRow(
            company_id=c.id, name=c.name, is_active=bool(c.is_active),
            tax_id=c.tax_id, vat=c.vat, keys=keys,
            nif_malformed=any(nif_looks_malformed(k) for k in keys),
            crm_duplicate_group=dup_of.get(c.id),
        )
        matches: list[FactusolCustomer] = []
        for key in keys:
            for cust in by_nif.get(key, []):
                if cust not in matches:
                    matches.append(cust)
        row.factusol_codclis = [m.codcli for m in matches]
        if matches:
            row.factusol_nombre = matches[0].nombre
            sims = [s for s in (name_similarity(c.name, m.nombre) for m in matches)
                    if s is not None]
            row.name_similarity = max(sims) if sims else None

        linked = str(c.factusol_company_id or "").strip() or None
        row.linked_codcli = linked
        linked_cust = None
        if linked:
            linked_cust = by_codcli.get(linked) or (
                by_codcli_int.get(str(int(linked))) if linked.isdigit() else None
            )
        match_codclis = {m.codcli for m in matches} | {
            str(int(m.codcli)) for m in matches if m.codcli.isdigit()
        }
        if linked is None:
            if matches:
                row.link_status = "sin_vincular_casa"
            elif keys:
                row.link_status = "sin_vincular_no_casa"
            else:
                row.link_status = "sin_vincular_sin_nif"
        elif linked_cust is None:
            row.link_status = "vinculada_codcli_inexistente"
        elif matches and (linked in match_codclis or (
                linked.isdigit() and str(int(linked)) in match_codclis)):
            row.link_status = "vinculada_ok"
        elif matches:
            row.link_status = "vinculada_otro_codcli"
        else:
            row.link_status = "vinculada_nif_no_casa"
        row.in_factusol = bool(matches) or linked_cust is not None

        a = activity.get(c.id, {})
        for k in ("contacts", "orders_total", "orders_open", "orders_por_facturar",
                  "orders_invoiced", "orders_completed", "orders_hidden", "tasks_open",
                  "tasks_total"):
            setattr(row, k, int(a.get(k, 0)))
        row.last_email_at = _iso(a.get("last_email_at"))
        row.last_activity_at = _iso(a.get("last_activity_at"))
        # Proformas de los CODCLI que casan o del vinculado. Un CODCLI puede
        # aparecer con y sin ceros a la izquierda: no contar dos veces.
        pro_codclis = list(dict.fromkeys(row.factusol_codclis + ([linked] if linked else [])))
        seen: set[str] = set()
        total = 0
        for k in pro_codclis:
            norm = str(int(k)) if k.isdigit() else k
            if norm in seen:
                continue
            seen.add(norm)
            total += proformas.get(k, 0) or proformas.get(norm, 0)
        row.proformas = total

        strict = bool(
            row.orders_total or row.proformas or row.tasks_open
            or a.get("email_recent") or a.get("activity_recent")
        )
        row.alive_strict = strict
        row.alive_any = strict or row.contacts > 0

        if row.in_factusol:
            row.classification = "en_factusol"
        elif not keys:
            row.classification = (
                "sin_nif_con_actividad" if row.alive_any else "sin_nif_sin_actividad"
            )
        elif row.alive_any:
            row.classification = "proteger"
        else:
            row.classification = "candidata_archivar"

        # Ambigüedades a revisar a mano.
        if len(match_codclis) > 1 and len(row.factusol_codclis) > 1:
            row.notes.append(
                f"NIF con varios CODCLI en FACTUSOL: {', '.join(row.factusol_codclis)}"
            )
        low_similarity = (
            row.name_similarity is not None and row.name_similarity < NAME_SIMILARITY_LOW
        )
        if matches and low_similarity:
            row.notes.append(
                f"nombre muy distinto: CRM «{c.name}» vs FACTUSOL «{row.factusol_nombre}» "
                f"({row.name_similarity})"
            )
        if row.nif_malformed:
            row.notes.append(f"NIF mal formado: {', '.join(keys)}")
        if row.link_status == "vinculada_otro_codcli":
            row.notes.append(
                f"vinculada a {linked} pero el NIF casa con {', '.join(row.factusol_codclis)}"
            )
        if row.link_status == "vinculada_nif_no_casa":
            row.notes.append(f"vinculada a {linked} pero su NIF no casa con NIFCLI")
        if row.link_status == "vinculada_codcli_inexistente":
            row.notes.append(f"vinculada a {linked}, que no existe en F_CLI")
        if row.crm_duplicate_group:
            row.notes.append(f"NIF repetido en el CRM ({row.crm_duplicate_group})")
        if row.notes and (matches or linked or row.nif_malformed):
            ambiguous.append({
                "company_id": c.id, "name": c.name, "keys": keys,
                "factusol_codclis": row.factusol_codclis, "linked_codcli": linked,
                "notes": list(row.notes),
            })
        rows.append(row)

    counts = _counts(rows, dup_groups, len(customers))
    return Report(
        generated_at=now.isoformat(), recent_days=recent_days, counts=counts, rows=rows,
        ambiguous=ambiguous,
    )


def _counts(
    rows: list[CompanyRow], dup_groups: dict[str, list[str]], n_fcli: int,
) -> dict[str, Any]:
    c: dict[str, Any] = {}
    c["factusol_clientes"] = n_fcli
    c["total"] = len(rows)
    c["activas"] = sum(r.is_active for r in rows)
    con_nif = [r for r in rows if r.keys]
    c["sin_nif"] = len(rows) - len(con_nif)
    c["casan"] = sum(bool(r.factusol_codclis) for r in con_nif)
    c["no_casan"] = len(con_nif) - c["casan"]
    c["casan_con_varios_codcli"] = sum(len(r.factusol_codclis) > 1 for r in rows)
    c["duplicados_grupos"] = len(dup_groups)
    c["duplicados_fichas"] = sum(len(ids) for ids in dup_groups.values())
    # Actividad.
    c["con_pedidos"] = sum(r.orders_total > 0 for r in rows)
    c["con_pedidos_abiertos"] = sum(r.orders_open > 0 for r in rows)
    c["con_pedidos_por_facturar"] = sum(r.orders_por_facturar > 0 for r in rows)
    c["con_pedidos_facturados"] = sum(r.orders_invoiced > 0 for r in rows)
    c["con_pedidos_completados"] = sum(r.orders_completed > 0 for r in rows)
    c["con_pedidos_ocultos"] = sum(r.orders_hidden > 0 for r in rows)
    c["pedidos_total"] = sum(r.orders_total for r in rows)
    c["pedidos_por_facturar"] = sum(r.orders_por_facturar for r in rows)
    c["con_proformas"] = sum(r.proformas > 0 for r in rows)
    c["con_tareas_abiertas"] = sum(r.tasks_open > 0 for r in rows)
    c["con_contactos"] = sum(r.contacts > 0 for r in rows)
    c["con_actividad_viva"] = sum(r.alive_any for r in rows)
    c["con_actividad_viva_estricta"] = sum(r.alive_strict for r in rows)
    c["solo_contactos"] = sum(r.alive_any and not r.alive_strict for r in rows)
    # Cruce.
    def n(pred) -> int:  # noqa: ANN001
        return sum(1 for r in rows if pred(r))
    c["cruce"] = {
        "en_factusol": n(lambda r: r.in_factusol),
        "en_factusol_con_actividad": n(lambda r: r.in_factusol and r.alive_any),
        "en_factusol_sin_actividad": n(lambda r: r.in_factusol and not r.alive_any),
        "no_factusol_con_actividad": n(lambda r: r.classification == "proteger"),
        "no_factusol_sin_actividad": n(
            lambda r: r.classification == "candidata_archivar"),
        "sin_nif_con_actividad": n(
            lambda r: r.classification == "sin_nif_con_actividad"),
        "sin_nif_sin_actividad": n(
            lambda r: r.classification == "sin_nif_sin_actividad"),
        "pedidos_por_facturar_en_proteger": sum(
            r.orders_por_facturar for r in rows if r.classification == "proteger"),
    }
    c["cruce_estricto"] = {
        "no_factusol_con_actividad": n(
            lambda r: not r.in_factusol and r.keys and r.alive_strict),
        "no_factusol_sin_actividad": n(
            lambda r: not r.in_factusol and r.keys and not r.alive_strict),
    }
    # Vínculo.
    status = Counter(r.link_status for r in rows)
    c["vinculo"] = dict(status)
    c["casan_sin_vincular"] = status.get("sin_vincular_casa", 0)
    c["ambiguas"] = sum(bool(r.notes) for r in rows)
    return c


# --- salida ------------------------------------------------------------------------------------


def write_csv(report: Report, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, delimiter=";")
        writer.writeheader()
        for r in report.rows:
            data = asdict(r)
            data["keys"] = "|".join(r.keys)
            data["factusol_codclis"] = "|".join(r.factusol_codclis)
            data["notes"] = " · ".join(r.notes)
            writer.writerow({k: data[k] for k in CSV_COLUMNS})
    return path


def format_report(report: Report, *, sample: int = 15) -> str:
    c = report.counts
    x = c["cruce"]
    v = c["vinculo"]
    lines = [
        f"Empresas CRM vs FACTUSOL — discovery SOLO LECTURA ({report.generated_at[:19]}, "
        f"actividad reciente = {report.recent_days} días)",
        "",
        f"1. Empresas en el CRM: {c['total']} (activas {c['activas']}) · clientes F_CLI leídos: "
        f"{c['factusol_clientes']}",
        f"2. Casan con FACTUSOL por NIF/VAT normalizado: {c['casan']} · no casan (con NIF): "
        f"{c['no_casan']} · casan con VARIOS CODCLI: {c['casan_con_varios_codcli']}",
        f"3. Sin NIF ni VAT (no se pueden cruzar): {c['sin_nif']}",
        f"4. Duplicados en el CRM (mismo NIF/VAT en varias fichas): {c['duplicados_grupos']} "
        f"grupos · {c['duplicados_fichas']} fichas",
        "5. Actividad viva:",
        f"   con pedidos: {c['con_pedidos']} (abiertos {c['con_pedidos_abiertos']} · por "
        f"facturar {c['con_pedidos_por_facturar']} · facturados {c['con_pedidos_facturados']} · "
        f"completados {c['con_pedidos_completados']} · ocultos {c['con_pedidos_ocultos']}) · "
        f"pedidos en total {c['pedidos_total']}, por facturar {c['pedidos_por_facturar']}",
        f"   con proformas FACTUSOL: {c['con_proformas']} · con tareas abiertas: "
        f"{c['con_tareas_abiertas']} · con contactos: {c['con_contactos']}",
        f"   con actividad viva: {c['con_actividad_viva']} (estricta, sin contar «solo "
        f"contactos»: {c['con_actividad_viva_estricta']}; solo contactos: {c['solo_contactos']})",
        "6. Cruce ¿está en FACTUSOL? × ¿tiene actividad viva?:",
        f"   EN FACTUSOL (se quedan): {x['en_factusol']} (con actividad "
        f"{x['en_factusol_con_actividad']} · sin actividad {x['en_factusol_sin_actividad']})",
        f"   NO en FACTUSOL con actividad (proteger): {x['no_factusol_con_actividad']} "
        f"(llevan {x['pedidos_por_facturar_en_proteger']} pedidos POR FACTURAR)",
        f"   NO en FACTUSOL sin actividad (candidatas a archivar): "
        f"{x['no_factusol_sin_actividad']}",
        f"   sin NIF (no cruzables): con actividad {x['sin_nif_con_actividad']} · sin actividad "
        f"{x['sin_nif_sin_actividad']}",
        f"   (criterio estricto: proteger {c['cruce_estricto']['no_factusol_con_actividad']} · "
        f"candidatas {c['cruce_estricto']['no_factusol_sin_actividad']})",
        f"7. Casan con FACTUSOL y NO están vinculadas (auto-vincular por NIF): "
        f"{c['casan_sin_vincular']}",
        f"   vínculo hoy: ok {v.get('vinculada_ok', 0)} · vinculada a OTRO CODCLI "
        f"{v.get('vinculada_otro_codcli', 0)} · vinculada pero el NIF no casa "
        f"{v.get('vinculada_nif_no_casa', 0)} · vinculada a un CODCLI inexistente "
        f"{v.get('vinculada_codcli_inexistente', 0)} · sin vincular y sin casar "
        f"{v.get('sin_vincular_no_casa', 0)} · sin vincular y sin NIF "
        f"{v.get('sin_vincular_sin_nif', 0)}",
        "",
        f"Casos ambiguos a revisar a mano: {len(report.ambiguous)}",
    ]
    for item in report.ambiguous[:sample]:
        lines.append(
            f"  - {item['name']} [{item['company_id']}] NIF {', '.join(item['keys']) or '—'} → "
            f"FACTUSOL {', '.join(item['factusol_codclis']) or '—'} · "
            f"{'; '.join(item['notes'])}"
        )
    if len(report.ambiguous) > sample:
        lines.append(f"  … y {len(report.ambiguous) - sample} más (ver el CSV, columna notes)")
    return "\n".join(lines)
