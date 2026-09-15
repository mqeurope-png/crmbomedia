"""Escaneo SOLO LECTURA de vínculos factura↔pedido CRUZADOS (Bloque 1c, #427).

Herencia del CODFAC desnudo + serie compartida: el pedido guarda solo el nº de
factura (`factusol_invoice_number = "260090"`), varias series (TIPFAC 1/2/5)
tienen el mismo nº y boprint/fluxlasers comparten la serie 5. Resultado: hay
pedidos que «apuntan» a una factura cuyo CLIFAC es de OTRA empresa (el pedido
de NEON LED apuntando a la factura de Escola La Muntanyeta, o al revés).

Para cada pedido con nº de factura se localiza su fila de F_FAC:
  1. por serie guardada (`factusol_invoice_serie` o `serie-código`) + nº;
  2. si no consta serie: la fila cuya REFFAC es la referencia común del pedido;
  3. si tampoco: la fila cuyo CLIFAC es la empresa del pedido;
  4. si sigue sin saberse y hay varias filas → «ambiguo».
Y se compara el CLIFAC de la fila elegida con la empresa del pedido
(`Company.factusol_company_id`): distinto → «cruzado».

Categorías: ok · cruzado · ambiguo · sin_fila (nº sin factura en F_FAC del
ejercicio) · sin_empresa (no se puede comparar: pedido sin empresa enlazada).
NO escribe nada: ni en FACTUSOL ni en el CRM (lista para decidir, no corrige).
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.factusol_cobro import parse_invoice_number, serie_of_row
from app.erp.factusol_pdf import order_composed_ref, order_customer_code, order_invoice_serie
from app.erp.models import Order
from app.models.integration_settings import IntegrationAccount


@dataclass
class LinkRow:
    order_id: str
    order_number: str
    tienda: str | None
    factura_guardada: str
    serie_resuelta: int | None
    como: str                 # serie | referencia | cliente | unica | —
    reffac: str
    clifac: str
    cnofac: str
    empresa_codcli: str | None
    referencia_pedido: str
    categoria: str            # ok | cruzado | ambiguo | sin_fila | sin_empresa
    series_disponibles: str


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _index_f_fac(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    by_code: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        code = _int(row.get("CODFAC"))
        if code is not None:
            by_code[code].append(row)
    return by_code


def _pick_row(
    rows: list[dict[str, Any]], *, serie: int | None, ref: str, cli: str | None,
) -> tuple[dict[str, Any] | None, str]:
    """Fila de F_FAC del pedido y cómo se decidió."""
    if serie is not None:
        for row in rows:
            if serie_of_row(row, "TIPFAC") == serie:
                return row, "serie"
        return None, "—"
    if len(rows) == 1:
        return rows[0], "unica"
    if ref:
        by_ref = [r for r in rows if str(r.get("REFFAC") or "").strip().upper() == ref]
        if len(by_ref) == 1:
            return by_ref[0], "referencia"
    if cli:
        by_cli = [r for r in rows if str(r.get("CLIFAC") or "").strip() == cli]
        if len(by_cli) == 1:
            return by_cli[0], "cliente"
    return None, "—"


def scan_invoice_links(
    session: Session, client: Any, *, ejercicio: str,
    f_fac_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recorre los pedidos con nº de factura y clasifica su vínculo.
    `f_fac_rows`: F_FAC ya cargada (evita una segunda lectura)."""
    f_fac = f_fac_rows if f_fac_rows is not None else client.load_table(
        "F_FAC", ejercicio=ejercicio,
    )
    by_code = _index_f_fac(f_fac)
    stores = {
        s.id: s.account_id
        for s in session.scalars(select(IntegrationAccount))
    }
    orders = session.scalars(
        select(Order).where(Order.factusol_invoice_number.isnot(None))
        .order_by(Order.created_at)
    ).all()
    out: list[LinkRow] = []
    for order in orders:
        _, codigo = parse_invoice_number(order.factusol_invoice_number)
        if codigo is None:
            continue
        serie = order_invoice_serie(order)
        ref = order_composed_ref(session, order)
        cli = order_customer_code(session, order)
        rows = by_code.get(codigo, [])
        series = sorted({s for s in (serie_of_row(r, "TIPFAC") for r in rows) if s is not None})
        row, how = _pick_row(rows, serie=serie, ref=ref, cli=cli)
        if row is None:
            categoria = "sin_fila" if not rows else "ambiguo"
        elif not cli:
            categoria = "sin_empresa"
        elif str(row.get("CLIFAC") or "").strip() != cli:
            categoria = "cruzado"
        else:
            categoria = "ok"
        out.append(LinkRow(
            order_id=order.id, order_number=order.order_number,
            tienda=stores.get(order.store_id) if order.store_id else None,
            factura_guardada=str(order.factusol_invoice_number),
            serie_resuelta=serie_of_row(row, "TIPFAC") if row is not None else serie,
            como=how,
            reffac=str(row.get("REFFAC") or "").strip() if row is not None else "",
            clifac=str(row.get("CLIFAC") or "").strip() if row is not None else "",
            cnofac=str(row.get("CNOFAC") or "").strip() if row is not None else "",
            empresa_codcli=cli, referencia_pedido=ref, categoria=categoria,
            series_disponibles=",".join(str(s) for s in series),
        ))
    totals = defaultdict(int)
    for r in out:
        totals[r.categoria] += 1
    return {
        "ejercicio": ejercicio,
        "pedidos_con_factura": len(out),
        "totales": dict(totals),
        "filas": [asdict(r) for r in out],
    }


def format_report(scan: dict[str, Any], *, only: set[str] | None = None) -> str:
    only = only or {"cruzado", "ambiguo"}
    t = scan["totales"]
    lines = [
        f"Ejercicio {scan['ejercicio']} · pedidos con nº de factura: {scan['pedidos_con_factura']}",
        "  ok={ok}  cruzado={cruzado}  ambiguo={ambiguo}  sin_fila={sin_fila}  "
        "sin_empresa={sin_empresa}".format(
            ok=t.get("ok", 0), cruzado=t.get("cruzado", 0), ambiguo=t.get("ambiguo", 0),
            sin_fila=t.get("sin_fila", 0), sin_empresa=t.get("sin_empresa", 0),
        ),
        "",
    ]
    for r in scan["filas"]:
        if r["categoria"] not in only:
            continue
        serie = r["serie_resuelta"] if r["serie_resuelta"] is not None else "?"
        lines.append(
            f"[{r['categoria'].upper():<8}] {r['order_number']:<14} "
            f"tienda={r['tienda'] or '—':<10} factura={r['factura_guardada']} "
            f"serie={serie} ({r['como']}) series F_FAC={r['series_disponibles'] or '—'}"
        )
        lines.append(
            f"           pedido: ref={r['referencia_pedido'] or '—'} "
            f"empresa CODCLI={r['empresa_codcli'] or '—'}  ·  factura: "
            f"REFFAC={r['reffac'] or '—'} CLIFAC={r['clifac'] or '—'} «{r['cnofac']}»"
        )
    if len(lines) == 3:
        lines.append("(sin vínculos cruzados ni ambiguos)")
    return "\n".join(lines)


def to_csv(scan: dict[str, Any]) -> str:
    buf = io.StringIO()
    fields = list(LinkRow.__dataclass_fields__)
    writer = csv.DictWriter(buf, fieldnames=fields, delimiter=";")
    writer.writeheader()
    for r in scan["filas"]:
        writer.writerow(r)
    return buf.getvalue()
