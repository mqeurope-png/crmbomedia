"""ERP-F4-A — lectura del extracto bancario (.xlsx / .csv).

Cada banco exporta distinto, así que el mapeo de columnas es configurable
por cuenta y se guarda la primera vez. El formato de Banco Sabadell (el
fichero real de Bomedia) se detecta solo y sirve de valor inicial:

    Cabecera:  Cuenta: ES33 0081 0202 1300 0117 1918 · 337906622 · Boprint
               Divisa: EUR
               Titular: BOMEDIA S.L
    Columnas:  FECHA OPER · CONCEPTO · FECHA VALOR · IMPORTE · SALDO ·
               REFERENCIA 1 · REFERENCIA 2 · FACTURA · PRESUPUESTO · PEDIDO

Importes en formato español («1.314,47») o anglosajón («-1,500.00») según la
exportación — se soportan ambos. Fechas «d/m/yyyy». Una fila que no se puede
interpretar FALLA de forma visible (con su nº de fila), nunca se importa mal.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

#: Mapeo por defecto = formato Sabadell. Claves = campo interno; valores =
#: cabecera de columna en el fichero (comparación case/espacios-insensible).
SABADELL_MAPPING: dict[str, str] = {
    "fecha_oper": "FECHA OPER",
    "concepto": "CONCEPTO",
    "fecha_valor": "FECHA VALOR",
    "importe": "IMPORTE",
    "saldo": "SALDO",
    "referencia1": "REFERENCIA 1",
    "referencia2": "REFERENCIA 2",
    "factura": "FACTURA",
    "presupuesto": "PRESUPUESTO",
    "pedido": "PEDIDO",
}

#: Campos obligatorios para que una fila sea un movimiento.
REQUIRED_FIELDS = ("fecha_oper", "concepto", "importe")

#: Columnas que Bart rellena a mano y que este PR automatiza en la exportación.
FILLED_COLUMNS = ("factura", "presupuesto", "pedido")


class ParseError(ValueError):
    """Fila que no se puede interpretar. Lleva el nº de fila (1-based en el
    fichero) para que el error sea accionable."""

    def __init__(self, row: int | None, message: str):
        self.row = row
        super().__init__(f"fila {row}: {message}" if row else message)


@dataclass
class ParsedStatement:
    """Resultado de leer el fichero: cabecera (cuenta/divisa/titular), las
    columnas tal cual y las filas crudas (dict por columna)."""

    header: dict[str, str] = field(default_factory=dict)
    header_lines: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    #: Fila (1-based) del fichero donde está la cabecera de columnas.
    columns_row: int = 0


# --- números y fechas --------------------------------------------------------


def parse_amount(value: Any) -> float:
    """«1.314,47» → 1314.47 · «-1,500.00» → -1500.0 · «110,11» → 110.11 ·
    «6950.22» → 6950.22 · 2278 → 2278.0. Decide el separador decimal por el
    ÚLTIMO separador presente; con uno solo, «,» siempre es decimal y «.» lo es
    si van 2 dígitos detrás (3 dígitos = millar, «1.314»)."""
    if value is None:
        raise ValueError("importe vacío")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("€", "").replace(" ", "").replace(" ", "")
    if not text:
        raise ValueError("importe vacío")
    negative = text.startswith("-") or text.endswith("-")
    text = text.strip("-+")
    if not re.fullmatch(r"[\d.,]+", text):
        raise ValueError(f"importe no numérico: {value!r}")
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif has_comma:
        text = text.replace(",", ".")
    elif has_dot:
        head, _, tail = text.rpartition(".")
        if len(tail) == 3 and head:  # «1.314» = millar
            text = head.replace(".", "") + tail
        # si no, «6950.22» ya es decimal
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"importe no numérico: {value!r}") from exc
    return -number if negative else number


def parse_date(value: Any) -> date:
    """«3/9/2026» · «25/08/2026» · «2026-09-03» · datetime/date de openpyxl."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("fecha vacía")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"fecha no reconocida: {value!r}")


# --- cabecera / IBAN ---------------------------------------------------------

_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?)\b")


def normalize_iban(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def extract_iban(text: str) -> str | None:
    """IBAN de una línea de cabecera («Cuenta: ES33 0081 0202 1300 0117 1918 ·
    …»). Normalizado sin espacios."""
    match = _IBAN_RE.search(str(text or "").upper())
    return normalize_iban(match.group(1)) if match else None


def _norm_col(name: Any) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().upper())


def detect_mapping(columns: list[str]) -> dict[str, str] | None:
    """Devuelve el mapeo Sabadell si las columnas obligatorias están; None si
    el fichero no encaja (entonces hace falta el mapeo de la cuenta)."""
    present = {_norm_col(c) for c in columns}
    if all(_norm_col(SABADELL_MAPPING[f]) in present for f in REQUIRED_FIELDS):
        return dict(SABADELL_MAPPING)
    return None


# --- lectura del fichero -----------------------------------------------------


def _rows_from_xlsx(content: bytes) -> list[list[Any]]:
    from openpyxl import load_workbook  # noqa: PLC0415

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _rows_from_csv(content: bytes) -> list[list[Any]]:
    """El separador se decide por la fila de columnas (la que trae IMPORTE):
    las líneas de cabecera («Cuenta: …») no llevan separador y confunden al
    Sniffer, y los importes «1.314,47» meten comas que no son separador."""
    text = content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    probe = next((ln for ln in lines if "IMPORTE" in ln.upper()), lines[0] if lines else "")
    delimiter = max(";,\t|", key=probe.count) if probe else ";"
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def read_statement(content: bytes, filename: str) -> ParsedStatement:
    """Lee .xlsx o .csv. Separa la cabecera (líneas antes de la fila de
    columnas, donde vive el IBAN/divisa/titular) de las filas de datos."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        grid = _rows_from_xlsx(content)
    elif name.endswith((".csv", ".txt")):
        grid = _rows_from_csv(content)
    else:
        raise ParseError(None, "formato no soportado: usa .xlsx o .csv")

    parsed = ParsedStatement()
    columns_idx = -1
    for idx, row in enumerate(grid):
        cells = [c for c in row if c is not None and str(c).strip()]
        normed = {_norm_col(c) for c in cells}
        if (
            _norm_col(SABADELL_MAPPING["fecha_oper"]) in normed
            and _norm_col(SABADELL_MAPPING["importe"]) in normed
        ) or (len(cells) >= 3 and "IMPORTE" in normed and "CONCEPTO" in normed):
            columns_idx = idx
            break
        line = " · ".join(str(c).strip() for c in cells)
        if line:
            parsed.header_lines.append(line)
            low = line.lower()
            if low.startswith("cuenta"):
                iban = extract_iban(line)
                if iban:
                    parsed.header["iban"] = iban
                parsed.header["cuenta"] = line
            elif low.startswith("divisa"):
                parsed.header["divisa"] = line.split(":", 1)[-1].strip()
            elif low.startswith("titular"):
                parsed.header["titular"] = line.split(":", 1)[-1].strip()
    if columns_idx < 0:
        raise ParseError(None, "no se encontró la fila de columnas (FECHA OPER / IMPORTE)")
    parsed.columns_row = columns_idx + 1
    parsed.columns = [str(c).strip() if c is not None else "" for c in grid[columns_idx]]
    for offset, row in enumerate(grid[columns_idx + 1 :], start=columns_idx + 2):
        if not any(c is not None and str(c).strip() for c in row):
            continue
        cells = list(row) + [None] * (len(parsed.columns) - len(row))
        parsed.rows.append(
            {
                "__row__": offset,
                **{col: cells[i] for i, col in enumerate(parsed.columns) if col},
            }
        )
    return parsed


def apply_mapping(
    parsed: ParsedStatement,
    mapping: dict[str, str],
) -> list[dict[str, Any]]:
    """Filas crudas → movimientos normalizados. Falla de forma VISIBLE (con nº
    de fila) si un campo obligatorio no se puede interpretar."""
    col_by_norm = {_norm_col(c): c for c in parsed.columns if c}

    def col(field_name: str) -> str | None:
        wanted = mapping.get(field_name)
        return col_by_norm.get(_norm_col(wanted)) if wanted else None

    out: list[dict[str, Any]] = []
    for row in parsed.rows:
        nrow = row["__row__"]
        raw = {k: v for k, v in row.items() if k != "__row__"}
        try:
            fecha_oper = parse_date(row.get(col("fecha_oper")))
            importe = parse_amount(row.get(col("importe")))
        except ValueError as exc:
            raise ParseError(nrow, str(exc)) from exc
        concepto = str(row.get(col("concepto")) or "").strip()
        if not concepto:
            raise ParseError(nrow, "concepto vacío")
        fecha_valor: date | None = None
        fv = row.get(col("fecha_valor")) if col("fecha_valor") else None
        if fv not in (None, ""):
            try:
                fecha_valor = parse_date(fv)
            except ValueError as exc:
                raise ParseError(nrow, str(exc)) from exc
        saldo: float | None = None
        sv = row.get(col("saldo")) if col("saldo") else None
        if sv not in (None, ""):
            try:
                saldo = parse_amount(sv)
            except ValueError as exc:
                raise ParseError(nrow, str(exc)) from exc

        def text(field_name: str, _row: dict[str, Any] = row) -> str | None:
            # `_row` fija la fila en cada iteración (B023: el cierre no debe
            # capturar la variable del bucle).
            c = col(field_name)
            v = _row.get(c) if c else None
            s = str(v).strip() if v is not None else ""
            return s or None

        out.append(
            {
                "source_row": nrow,
                "fecha_oper": fecha_oper,
                "fecha_valor": fecha_valor,
                "concepto": concepto,
                "importe": round(importe, 2),
                "saldo": round(saldo, 2) if saldo is not None else None,
                "referencia1": text("referencia1"),
                "referencia2": text("referencia2"),
                "factura": text("factura"),
                "presupuesto": text("presupuesto"),
                "pedido": text("pedido"),
                "raw": raw,
            }
        )
    return out


def dedupe_key(
    account_id: str,
    fecha_oper: date,
    importe: float,
    concepto: str,
    saldo: float | None,
) -> str:
    """cuenta + fecha oper + importe + concepto + saldo → hash estable. Un
    rango reimportado (solapado) produce las mismas claves → no duplica."""
    base = "|".join(
        [
            account_id,
            fecha_oper.isoformat(),
            f"{importe:.2f}",
            re.sub(r"\s+", " ", concepto.strip().upper()),
            f"{saldo:.2f}" if saldo is not None else "",
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
