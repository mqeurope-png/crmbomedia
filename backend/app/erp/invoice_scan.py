"""Escaneo SOLO LECTURA de facturas de FACTUSOL con líneas contaminadas —
secuela del bug de emisión #382.

El bug (#382, arreglado el 2026-09-10): al emitir, `emit_invoice` cargaba las
líneas del pedido por `CODLPC` SIN filtrar por serie, así que arrastraba a la
factura las líneas del pedido HOMÓNIMO de otra serie (mismo «Nº de su pedido»).
La CABECERA de F_FAC (base/IVA/total) se copia del pedido concreto, así que
quedó CORRECTA; lo que quedó mal es el DETALLE de `F_LFA`. Detector fiable:

    suma de renglones de F_LFA (por clave compuesta serie+número) ≠ base
    imponible NETA de la cabecera (Σ NET1FAC..NET4FAC).

Importante:
- Se compara contra la base NETA (Σ `NET*FAC`), NO contra `BAS*FAC`: `BAS =
  NET + portes (IPOR)`, y los portes NO son línea de F_LFA (van a la banda de
  portes de la cabecera). Incluir los portes daría un falso positivo en toda
  factura con gastos de envío.
- El detalle de una factura se toma por clave COMPUESTA: se leen las líneas por
  `CODLFA` y se conservan las de `coerce_serie(TIPLFA) in (serie, None)`, igual
  que `documents.get_document` (una línea homónima de otra serie NO cuenta).

Este módulo NO escribe nada — ni en FACTUSOL ni en el CRM. Solo lee (las tablas
que le pasa el script y, para el origen/fecha de emisión, el historial de
estado del CRM) y compone el informe + CSV.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import Order, OrderStatusHistory, StatusDomain
from app.integrations.factusol.documents import visible_number
from app.integrations.factusol.service import coerce_serie

#: Instante del fix de emisión #382 (commit 798613f, 2026-09-10 11:54:35
#: +02:00). Las facturas emitidas ANTES pueden estar contaminadas; a partir de
#: aquí, el detalle se escribe ya con el filtro por serie → limpias.
FIX_382_AT = datetime(2026, 9, 10, 9, 54, 35, tzinfo=timezone.utc)

#: Tolerancia de redondeo (la misma que el resto del código de facturas: 0.005 €).
DEFAULT_TOLERANCE = 0.005

#: `reason` que `emit_invoice` escribe en el historial cuando BoHub EMITE la
#: factura (distinto de la auto-vinculación, que NO escribe líneas).
EMIT_REASON = "Factura emitida en FACTUSOL"

#: REFFAC que pone BoHub al emitir: PREFIJO-NNNNNN (p. ej. `BOP-099866`). Señal
#: FACTUSOL-side de origen BoHub cuando no llega el enlace del CRM.
_REFFAC_BOHUB = re.compile(r"^[A-Za-z]{2,5}-\d{4,}$")

CSV_COLUMNS = [
    "numero", "serie", "codigo", "cliente", "fecha_emision", "fecha_origen",
    "base_neta", "suma_lineas", "diferencia", "num_lineas", "origen",
    "order_number", "antes_del_fix",
]


def _num(value: Any) -> float:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _int(value: Any) -> int | None:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _s(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _aware(value: datetime | None) -> datetime | None:
    """Fecha con tz (asume UTC si viene naive) para comparar con `FIX_382_AT`."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def header_base(fac_row: dict[str, Any]) -> float:
    """Base imponible NETA de la cabecera = Σ NET1FAC..NET4FAC (sin portes).

    NO usar `BAS*FAC` (= NET + portes): los portes no son línea de F_LFA."""
    return round(sum(_num(fac_row.get(f"NET{i}FAC")) for i in range(1, 5)), 2)


def index_lines_by_codigo(
    lfa_rows: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """`{CODLFA:int -> [líneas]}` en una pasada. El scoping por serie se aplica
    luego con `coerce_serie(TIPLFA)`, como `documents.get_document`."""
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in lfa_rows:
        cod = _int(row.get("CODLFA"))
        if cod is None:
            continue
        out[cod].append(row)
    return out


def lines_for_invoice(
    index: dict[int, list[dict[str, Any]]], serie: int | None, codigo: int,
) -> list[dict[str, Any]]:
    """Líneas de F_LFA de la factura `(serie, codigo)` por clave compuesta: las
    de `coerce_serie(TIPLFA) in (serie, None)` — una homónima de otra serie NO
    entra (mismo criterio que `get_document`)."""
    return [
        row for row in index.get(codigo, [])
        if coerce_serie(row.get("TIPLFA")) in (serie, None)
    ]


# --- origen BoHub (historial de emisión del CRM) -----------------------------


@dataclass(frozen=True)
class EmitInfo:
    order_number: str
    emitted_at: datetime | None


def bohub_emit_map(session: Session) -> dict[tuple[int | None, int], EmitInfo]:
    """`{(serie, codfac) -> EmitInfo}` de las facturas que BoHub EMITIÓ (no las
    auto-vinculadas). Se lee del historial de estado: `domain=INVOICE`,
    `reason='Factura emitida en FACTUSOL'`, con `metadata.factusol_serie` /
    `factusol_codfac`; `emitted_at = changed_at`. SOLO LECTURA.

    La auto-vinculación (`reason='… vinculada automáticamente'`) NO escribe
    líneas, así que NO puede contaminar: queda fuera por el filtro de `reason`."""
    rows = session.execute(
        select(OrderStatusHistory, Order.order_number)
        .join(Order, Order.id == OrderStatusHistory.order_id)
        .where(
            OrderStatusHistory.domain == StatusDomain.INVOICE,
            OrderStatusHistory.reason == EMIT_REASON,
        )
    ).all()
    out: dict[tuple[int | None, int], EmitInfo] = {}
    for hist, order_number in rows:
        try:
            meta = json.loads(hist.metadata_json or "{}")
        except (TypeError, ValueError):
            meta = {}
        codfac = _int(meta.get("factusol_codfac"))
        if codfac is None:
            continue
        serie = coerce_serie(meta.get("factusol_serie"))
        # Si hay varias emisiones para la misma factura, la más reciente manda.
        prev = out.get((serie, codfac))
        emitted = _aware(hist.changed_at)
        if prev is None or (
            emitted is not None and (prev.emitted_at is None or emitted > prev.emitted_at)
        ):
            out[(serie, codfac)] = EmitInfo(order_number=order_number, emitted_at=emitted)
    return out


def _lookup_emit(
    emit_map: dict[tuple[int | None, int], EmitInfo], serie: int | None, codigo: int,
) -> EmitInfo | None:
    """(serie, codfac) exacto; si no, la entrada con serie desconocida; si no,
    la única entrada con ese codfac (evita adivinar entre homónimas)."""
    hit = emit_map.get((serie, codigo)) or emit_map.get((None, codigo))
    if hit is not None:
        return hit
    same_cod = [info for (s, c), info in emit_map.items() if c == codigo]
    return same_cod[0] if len(same_cod) == 1 else None


def _looks_bohub(fac_row: dict[str, Any]) -> bool:
    """Señal FACTUSOL-side de origen BoHub: `PEDFAC` (=CODPCL, lo inyecta solo
    la emisión de BoHub) o un `REFFAC` con el patrón `PREFIJO-NNNNNN`. Las
    facturas hechas a mano en el escritorio dejan ambos vacíos/distintos."""
    if _s(fac_row.get("PEDFAC")):
        return True
    return bool(_REFFAC_BOHUB.match(_s(fac_row.get("REFFAC"))))


# --- informe ------------------------------------------------------------------


@dataclass
class InvoiceScanRow:
    serie: int | None
    codigo: int
    numero: str
    cliente: str
    fecha_emision: str | None
    fecha_origen: str          # "emision_crm" (historial) | "fecfac" (cabecera)
    base_neta: float
    suma_lineas: float
    diferencia: float
    num_lineas: int
    origen: str                # "bohub" | "manual"
    order_number: str | None
    contaminada: bool
    antes_del_fix: bool | None  # None cuando no hay fecha


@dataclass
class InvoiceScanReport:
    generated_at: str
    ejercicio: str
    fix_at: str
    tolerance: float
    total_facturas: int
    rows: list[InvoiceScanRow] = field(default_factory=list)

    @property
    def bohub(self) -> list[InvoiceScanRow]:
        return [r for r in self.rows if r.origen == "bohub"]

    @property
    def manual(self) -> list[InvoiceScanRow]:
        return [r for r in self.rows if r.origen == "manual"]

    @property
    def contaminadas(self) -> list[InvoiceScanRow]:
        return [r for r in self.rows if r.contaminada]


def _fecfac_before_fix(fecfac: str) -> bool | None:
    head = fecfac[:10]
    if not head:
        return None
    try:
        d = datetime.strptime(head, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return d < FIX_382_AT


def build_report(
    fac_rows: list[dict[str, Any]],
    lfa_rows: list[dict[str, Any]],
    *,
    ejercicio: str,
    bohub_map: dict[tuple[int | None, int], EmitInfo] | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
    include_manual: bool = True,
) -> InvoiceScanReport:
    """Compara, factura a factura, la suma de las líneas de F_LFA (por clave
    compuesta) con la base neta de la cabecera; marca contaminada la que no
    cuadra (|diff| > tolerancia). Clasifica el origen (BoHub vs manual) y, para
    las BoHub, si la emisión fue anterior o posterior al fix #382."""
    emit_map = bohub_map or {}
    index = index_lines_by_codigo(lfa_rows)
    rows: list[InvoiceScanRow] = []
    for fac in fac_rows:
        codigo = _int(fac.get("CODFAC"))
        if codigo is None:
            continue
        serie = coerce_serie(fac.get("TIPFAC"))
        emit = _lookup_emit(emit_map, serie, codigo)
        origen = "bohub" if (emit is not None or _looks_bohub(fac)) else "manual"
        if origen == "manual" and not include_manual:
            continue
        lines = lines_for_invoice(index, serie, codigo)
        suma = round(sum(_num(r.get("TOTLFA")) for r in lines), 2)
        base = header_base(fac)
        diff = round(suma - base, 2)
        if emit is not None and emit.emitted_at is not None:
            fecha_emision: str | None = emit.emitted_at.isoformat()
            fecha_origen = "emision_crm"
            antes = emit.emitted_at < FIX_382_AT
        else:
            fecfac = _s(fac.get("FECFAC"))
            fecha_emision = fecfac[:10] or None
            fecha_origen = "fecfac"
            antes = _fecfac_before_fix(fecfac) if origen == "bohub" else None
        rows.append(InvoiceScanRow(
            serie=serie, codigo=codigo,
            numero=visible_number(fac.get("TIPFAC"), fac.get("CODFAC")),
            cliente=_s(fac.get("CNOFAC")),
            fecha_emision=fecha_emision, fecha_origen=fecha_origen,
            base_neta=base, suma_lineas=suma, diferencia=diff,
            num_lineas=len(lines), origen=origen,
            order_number=emit.order_number if emit else None,
            contaminada=abs(diff) > tolerance,
            antes_del_fix=antes,
        ))
    # Orden por fecha de emisión (las sin fecha, al final).
    rows.sort(key=lambda r: (r.fecha_emision or "9999", r.numero))
    return InvoiceScanReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ejercicio=str(ejercicio), fix_at=FIX_382_AT.isoformat(),
        tolerance=tolerance, total_facturas=len(fac_rows), rows=rows,
    )


def _eur(value: float) -> str:
    return f"{value:.2f}"


def format_report(report: InvoiceScanReport, *, sample: int = 0) -> str:
    bohub = report.bohub
    manual = report.manual
    cont_bohub = [r for r in bohub if r.contaminada]
    cont_manual = [r for r in manual if r.contaminada]
    antes = [r for r in cont_bohub if r.antes_del_fix is True]
    despues = [r for r in cont_bohub if r.antes_del_fix is False]
    sin_fecha = [r for r in cont_bohub if r.antes_del_fix is None]
    lines = [
        f"Facturas FACTUSOL con líneas contaminadas — escaneo SOLO LECTURA "
        f"({report.generated_at[:19]}, ejercicio {report.ejercicio})",
        f"Detector: Σ líneas F_LFA (clave compuesta) ≠ base neta cabecera "
        f"(Σ NET*FAC), tolerancia {report.tolerance:.3f} €.",
        f"Fix del bug #382: {report.fix_at} (emitidas antes = sospechosas).",
        "",
        f"1. Facturas leídas de F_FAC: {report.total_facturas}",
        f"2. Emitidas por BoHub (revisadas): {len(bohub)} · creadas a mano "
        f"(FACTUSOL): {len(manual)}",
        f"3. BoHub CONTAMINADAS: {len(cont_bohub)} · limpias: "
        f"{len(bohub) - len(cont_bohub)}",
        f"   - anteriores al fix #382: {len(antes)}",
        f"   - POSTERIORES al fix #382: {len(despues)}"
        + ("  ⚠ REVISAR: no debería haber ninguna nueva" if despues else "  ✓ ninguna nueva"),
    ]
    if sin_fecha:
        lines.append(f"   - sin fecha fiable de emisión: {len(sin_fecha)}")
    lines.append(
        f"4. Creadas a mano contaminadas: {len(cont_manual)}"
        + ("  ⚠ inesperado (el bug era de la emisión de BoHub)" if cont_manual else "")
    )
    if cont_bohub:
        lines += ["", "Contaminadas (BoHub), por fecha de emisión:",
                  f"  {'NÚMERO':<12} {'FECHA':<11} {'BASE':>10} {'Σ LÍNEAS':>10} "
                  f"{'DIFERENCIA':>11} {'FIX':<7} CLIENTE / PEDIDO"]
        for r in cont_bohub:
            fix = "antes" if r.antes_del_fix else ("después" if r.antes_del_fix is False else "?")
            ped = f" · {r.order_number}" if r.order_number else ""
            lines.append(
                f"  {r.numero:<12} {(r.fecha_emision or '—')[:10]:<11} "
                f"{r.base_neta:>10.2f} {r.suma_lineas:>10.2f} "
                f"{r.diferencia:>+11.2f} {fix:<7} {r.cliente}{ped}"
            )
    if cont_manual:
        lines += ["", "Contaminadas creadas a mano (inesperado):"]
        for r in cont_manual:
            lines.append(
                f"  {r.numero:<12} {(r.fecha_emision or '—')[:10]:<11} "
                f"base {_eur(r.base_neta)} · Σ líneas {_eur(r.suma_lineas)} · "
                f"dif {_eur(r.diferencia)} · {r.cliente}"
            )
    lines += ["", "SOLO LECTURA — no se ha escrito nada en FACTUSOL ni en el CRM."]
    return "\n".join(lines)


def write_csv(report: InvoiceScanReport, path: str | Path) -> Path:
    """CSV con UNA fila por factura contaminada (BoHub y, si las hubiera, a
    mano), ordenadas por fecha de emisión. Separador ';'."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, delimiter=";")
        writer.writeheader()
        for r in report.contaminadas:
            data = asdict(r)
            writer.writerow({k: data[k] for k in CSV_COLUMNS})
    return path
