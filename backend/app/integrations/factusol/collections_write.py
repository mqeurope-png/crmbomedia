"""ERP-F4-B — REGISTRAR un cobro en FACTUSOL (escritura en `F_LCO` + `ESTFAC`).

`collections.py` es SOLO LECTURA y así se queda; la escritura vive aquí, aparte,
para que quede claro qué toca la contabilidad.

Modelo, confirmado con una factura REAL cobrada (`--lco-row 5-260001`, 2026-09-10):

- Una factura cobrada tiene SOLO su línea en `F_LCO` (clave compuesta
  `(TFALCO, CFALCO, LINLCO)`, LINLCO 1..N por factura) y NINGUNA fila de `F_COB`
  ligada a ella: `F_COB` (columnas `CODCOB, CPACOB, CPTCOB, FECCOB, IMPCOB,
  OBSCOB, TIPCOB, TRACOB`) no lleva clave de factura — es cartera/tesorería, no
  una cabecera por factura. **No se escribe `F_COB`** (PR #385 lo intentó y era
  incorrecto).
- Por qué fallaba el `F_LCO` de #384 con las 23 columnas: sobrescribíamos DE MÁS
  respecto a la fila real — `FPALCO` (real `''`, nosotros `'002'`), `CPTLCO`
  (real `'COBRO FACTURA Nº: 5 - 260001'`, nosotros con sufijo ` (Transferencia)`)
  y el formato de fecha. Un valor fuera de lo que DELSOL admite en
  `EscribirRegistro` da el `BDEscribirRegistroError` genérico.
- Fix: copiar la fila real de plantilla (misma serie y, si hay, misma
  contrapartida) y sobrescribir SOLO lo imprescindible — `TFALCO`, `CFALCO`,
  `LINLCO`, `FECLCO`, `FALLCO`, `IMPLCO`, `CPALCO`, `CPTLCO` (mismo patrón que la
  fila real, sin sufijo). Todo lo demás (`FPALCO`, `MULLCO`, `TIPLCO`, `UALLCO`,
  `OBSLCO`…) igual que la plantilla. Fechas en el MISMO formato string que la
  fila real (`'2026-08-24T00:00:00'`). Tipos JSON como los devuelve DELSOL.
- Mismo mecanismo de inserción que la emisión: `client.write_record` →
  `/admin/EscribirRegistro`. Después, `ESTFAC=2` con el escritor único de F-3.

Solo escribe cobros: no toca líneas, totales ni nada más de la factura.
Idempotente: si la factura ya está cobrada (saldo 0 / ESTFAC=2) no escribe.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.integrations.factusol.client import FactusolClient
from app.integrations.factusol.collections import (
    invoice_collections,
    load_collections_index,
)
from app.integrations.factusol.mapper import filter_to_real_columns
from app.integrations.factusol.quotes import _num
from app.integrations.factusol.service import (
    _estado_str,
    mark_invoice_payment,
    serie_of_row,
)

logger = logging.getLogger(__name__)

#: Columnas REALES de F_LCO (discovery en producción, 2026-09-10).
LCO_COLUMNS: frozenset[str] = frozenset({
    "ANTLCO", "CAJLCO", "CFALCO", "CPALCO", "CPTLCO", "FALLCO", "FECLCO",
    "FPALCO", "FUMLCO", "IMPLCO", "LINLCO", "MULLCO", "OBSLCO", "PCALCO",
    "PROLCO", "TERLCO", "TFALCO", "TIDLCO", "TIPLCO", "TPVIDLCO", "TRALCO",
    "UALLCO", "UUMLCO",
})

#: Tolerancia para considerar una factura ya cobrada (saldo ≈ 0).
_EPS = 0.005

#: Lo ÚNICO que se sobreescribe sobre la fila real de plantilla. `FPALCO`,
#: `OBSLCO`, `MULLCO`, `TIPLCO`, `UALLCO`… se heredan tal cual: fijarlos de más
#: (p. ej. `FPALCO='002'`) es lo que DELSOL rechazaba.
_OVERRIDE_COLUMNS = (
    "TFALCO", "CFALCO", "LINLCO", "FECLCO", "FALLCO", "IMPLCO", "CPALCO",
    "CPTLCO",
)

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y")


def factusol_datetime(value: Any) -> str:
    """Fecha → el MISMO formato string que trae la fila real de F_LCO
    (`'2026-08-24T00:00:00'`, como devuelve DELSOL FECLCO/FALLCO). Acepta ISO
    (con o sin hora), `dd/mm/yyyy`, `dd-mm-yyyy`, `date`/`datetime`. Lanza
    ValueError si no se entiende: nunca se escribe una fecha adivinada."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT00:00:00")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%dT00:00:00")
    text = str(value or "").strip()
    if not text:
        raise ValueError("fecha de cobro vacía")
    head = text.split("T")[0].split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(head, fmt).strftime("%Y-%m-%dT00:00:00")
        except ValueError:
            continue
    raise ValueError(f"fecha de cobro no reconocida: {text!r}")


def concepto_cobro(serie: int, codigo: int) -> str:
    """Concepto (`CPTLCO`) con EXACTAMENTE el patrón de la fila real
    («COBRO FACTURA Nº: 5 - 260001»), sin sufijo de forma de pago."""
    return f"COBRO FACTURA Nº: {serie} - {codigo}"


def invoice_row(
    client: FactusolClient, *, serie: int, codigo: int, ejercicio: str,
) -> dict[str, Any] | None:
    """Fila de F_FAC por clave COMPUESTA (serie, código) — nunca solo por
    número (la factura homónima de otra serie tiene el mismo CODFAC)."""
    rows = client.load_table(
        "F_FAC", filtro=f"CODFAC={int(codigo)}", ejercicio=ejercicio,
    )
    return next((r for r in rows if serie_of_row(r, "TIPFAC") == serie), None)


def collection_status(
    client: FactusolClient, *, serie: int, codigo: int, ejercicio: str,
    row: dict[str, Any] | None = None,
    index: dict[tuple[int, int], list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    """Estado de cobro EN VIVO de la factura: total, cobrado, saldo, ESTFAC,
    nº de cobros y el siguiente LINLCO. `None` si la factura no existe.
    `row` / `index` ya cargados (fila de F_FAC / índice de F_LCO) evitan
    releer las tablas cuando se comprueban muchas facturas de una vez."""
    fac = row if row is not None else invoice_row(
        client, serie=serie, codigo=codigo, ejercicio=ejercicio,
    )
    if fac is None:
        return None
    total = _num(fac.get("TOTFAC"), 0.0)
    if index is None:
        index = load_collections_index(client, ejercicio=ejercicio)
    summary = invoice_collections(index, serie, codigo, total)
    lineas = [c["linea"] for c in summary["cobros"] if c["linea"] is not None]
    estfac = _estado_str(fac.get("ESTFAC"))
    saldo = summary["saldo_pendiente"] if summary["saldo_pendiente"] is not None else total
    return {
        "serie": serie, "codigo": codigo,
        "numero": f"{serie}-{int(codigo):06d}",
        "cliente": str(fac.get("CNOFAC") or "").strip(),
        "referencia": str(fac.get("REFFAC") or "").strip(),
        "total": round(total, 2),
        "total_cobrado": summary["total_cobrado"],
        "saldo_pendiente": round(saldo, 2),
        "estfac": estfac,
        "fopfac": str(fac.get("FOPFAC") or "").strip(),
        "cobros": len(summary["cobros"]),
        "next_linlco": (max(lineas) + 1) if lineas else 1,
        "ya_cobrada": saldo <= _EPS or estfac == "2",
    }


# --- plantilla (fila real) ---------------------------------------------------


def _like(template_value: Any, new_value: Any) -> Any:
    """Devuelve `new_value` con el MISMO tipo JSON que trae la plantilla
    (DELSOL devuelve `TFALCO` como '5' o 5 según la instalación; se le manda
    de vuelta exactamente como él lo da). Sin plantilla, se deja tal cual."""
    if template_value is None or isinstance(template_value, bool):
        return new_value
    try:
        if isinstance(template_value, int):
            return int(float(str(new_value).strip()))
        if isinstance(template_value, float):
            return round(float(new_value), 2)
        if isinstance(template_value, str):
            return str(new_value)
    except (TypeError, ValueError):
        return new_value
    return new_value


def pick_template_row(
    rows: list[dict[str, Any]], *, serie: int, contrapartida: str,
) -> dict[str, Any] | None:
    """Fila REAL de F_LCO que sirve de plantilla: misma serie y misma
    contrapartida si la hay (mismo tipo de cobro), si no misma serie, si no
    cualquiera. `None` solo si la tabla está vacía."""
    from app.integrations.factusol.catalogs import normalize_code  # noqa: PLC0415
    from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

    def score(r: dict[str, Any]) -> tuple[int, int]:
        same_serie = coerce_serie(r.get("TFALCO")) == serie
        same_cpa = normalize_code(r.get("CPALCO")) == normalize_code(contrapartida)
        return (int(same_serie and same_cpa), int(same_serie))

    best: dict[str, Any] | None = None
    best_score = (-1, -1)
    for r in rows:
        s = score(r)
        if s > best_score:
            best, best_score = r, s
            if s == (1, 1):
                break
    return best


def build_lco_payload(
    *, template: dict[str, Any] | None, serie: int, codigo: int, linlco: int,
    fecha_iso: str, importe: float, contrapartida: str,
) -> dict[str, Any]:
    """Registro para `EscribirRegistro` en F_LCO: la fila real de plantilla
    con SOLO lo imprescindible sobreescrito (clave de esta factura, línea,
    fechas, importe, contrapartida y concepto con el patrón real), cada valor
    con el tipo de la plantilla. Todo lo demás se hereda tal cual. Sin
    plantilla (tabla vacía) se manda solo ese mínimo, sin inventar nada."""
    tpl = dict(template or {})
    over: dict[str, Any] = {
        "TFALCO": str(serie), "CFALCO": int(codigo), "LINLCO": int(linlco),
        "FECLCO": fecha_iso, "FALLCO": fecha_iso, "IMPLCO": round(importe, 2),
        "CPALCO": str(contrapartida), "CPTLCO": concepto_cobro(serie, codigo),
    }
    payload = dict(tpl)
    for col in _OVERRIDE_COLUMNS:
        payload[col] = _like(tpl.get(col), over[col])
    allowed = LCO_COLUMNS | frozenset(k.upper() for k in tpl)
    return filter_to_real_columns(payload, allowed, tabla="F_LCO")


def _fmt_record(payload: dict[str, Any]) -> dict[str, tuple[Any, str]]:
    return {k: (v, type(v).__name__) for k, v in payload.items()}


# --- registro ----------------------------------------------------------------


def register_invoice_collection(
    client: FactusolClient,
    session: Session,
    *,
    serie: int,
    codigo: int,
    contrapartida: str,
    fecha: Any,
    importe: float | None = None,
    forma: str | None = None,
    observaciones: str | None = None,
    ejercicio: str,
) -> dict[str, Any]:
    """Registra UN cobro de la factura en FACTUSOL: inserta la línea en `F_LCO`
    (SOLO `F_LCO`; `F_COB` no es la cabecera por factura) y después marca
    `ESTFAC=2` con el escritor único de F-3.

    - `importe` por defecto = SALDO pendiente (= total si no había cobros):
      nunca se sobrepaga una factura con cobro parcial previo.
    - `forma` y `observaciones` se aceptan por compatibilidad con el script y
      quedan en el resultado/auditoría, pero NO se escriben en la fila: la fila
      real no lleva forma en el concepto ni observaciones, y fijarlas era parte
      de lo que DELSOL rechazaba.
    - Idempotente: si ya está cobrada (saldo 0 / ESTFAC=2) devuelve
      `status="already"` sin escribir nada.
    - Nunca lanza por un fallo de FACTUSOL: devuelve `registered=False` +
      motivo (el caller decide parar). Un fallo al marcar ESTFAC tras haber
      escrito la línea se refleja en `estfac_marked=False` (la línea SÍ quedó).
    """
    fecha_iso = factusol_datetime(fecha)  # ValueError si no se entiende
    status = collection_status(
        client, serie=serie, codigo=codigo, ejercicio=ejercicio,
    )
    if status is None:
        return {
            "registered": False, "status": "invoice_not_found",
            "motivo": f"No existe la factura {serie}-{codigo} en FACTUSOL.",
        }
    if status["ya_cobrada"]:
        return {"registered": False, "status": "already", **status}

    amount = round(
        float(importe) if importe is not None else status["saldo_pendiente"], 2,
    )
    if amount <= 0:
        return {
            "registered": False, "status": "nothing_to_collect", **status,
            "motivo": "El importe a cobrar es 0.",
        }
    cpa = str(contrapartida)
    template = pick_template_row(
        client.load_table("F_LCO", filtro="1=1", ejercicio=ejercicio),
        serie=serie, contrapartida=cpa,
    )
    payload = build_lco_payload(
        template=template, serie=serie, codigo=codigo,
        linlco=status["next_linlco"], fecha_iso=fecha_iso, importe=amount,
        contrapartida=cpa,
    )
    # Diagnóstico: el registro EXACTO que va a EscribirRegistro (nombres,
    # valores y tipos), para contrastar campo a campo con una fila real
    # (`--lco-row 5-260001`) si DELSOL lo rechazara.
    logger.info(
        "factusol cobro %s-%s: EscribirRegistro F_LCO ejercicio=%s plantilla=%s "
        "registro=%s",
        serie, codigo, ejercicio,
        (f"{template.get('TFALCO')}-{template.get('CFALCO')}/L{template.get('LINLCO')}"
         if template else "ninguna (tabla vacía)"),
        _fmt_record(payload),
    )
    try:
        client.write_record("F_LCO", payload, ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001 — se informa, nunca se rompe
        motivo = f"no se pudo escribir el cobro en F_LCO: {str(exc)[:200]}"
        logger.warning("factusol cobro %s-%s: %s", serie, codigo, motivo, exc_info=True)
        return {"registered": False, "status": "write_failed", "motivo": motivo, **status}
    logger.info(
        "factusol cobro %s-%s: F_LCO línea %s, %.2f € → contrapartida %s (%s)",
        serie, codigo, payload["LINLCO"], amount, cpa, fecha_iso[:10],
    )
    marked, motivo = mark_invoice_payment(
        client, session, serie=serie, codigo=codigo, paid=True,
        ejercicio=ejercicio, current_estado=status["estfac"],
    )
    return {
        "registered": True, "status": "registered",
        "linlco": payload["LINLCO"], "importe": amount,
        "contrapartida": cpa, "fecha": fecha_iso[:10],
        "concepto": payload["CPTLCO"],
        "forma": str(forma or "").strip() or None,
        "observaciones": str(observaciones or "").strip() or None,
        "estfac_marked": marked, "motivo": motivo,
        "numero": status["numero"], "cliente": status["cliente"],
        "total": status["total"],
    }
