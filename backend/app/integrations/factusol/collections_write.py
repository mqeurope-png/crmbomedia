"""ERP-F4-B — REGISTRAR un cobro en FACTUSOL (escritura en `F_LCO` + `ESTFAC`).

`collections.py` es SOLO LECTURA y así se queda; la escritura vive aquí, aparte,
para que quede claro qué toca la contabilidad. Diseño basado en el discovery
`scripts.factusol_discover_invoice_payment --collections` ejecutado en
producción (2026-09-10):

- `F_LCO` (856 filas) son las líneas de cobro de cada factura, con clave
  COMPUESTA `(TFALCO, CFALCO, LINLCO)`. **No referencia a `F_COB`** por ninguna
  columna (798 filas, sin FK) → aquí se escribe SOLO `F_LCO`; `F_COB` no se
  toca (se valida con las 2 facturas de prueba y, si la tesorería la echara
  en falta, se añade entonces — nunca a ciegas).
- `LINLCO` es CORRELATIVO POR FACTURA (1..N): el siguiente es max+1 (o 1).
- `FALLCO` es el VENCIMIENTO (siempre ≥ `FECLCO`). Para un cobro ya recibido
  se pone igual a la fecha del cobro.
- `CPALCO` = CONTRAPARTIDA (la cuenta donde entra el dinero; catálogo
  configurable de F-5). `FPALCO` = forma de pago (código F_FPA): se copia del
  `FOPFAC` de la propia factura en vez de adivinarlo de un texto libre.
- Columnas REALES de F_LCO (23, volcadas en vivo). Todo payload pasa por
  `filter_to_real_columns`: una columna inventada tumba el `EscribirRegistro`
  entero (gotcha nº 13).

Solo escribe cobros: no toca líneas, totales ni nada más de la factura. Es
idempotente: si la factura ya está cobrada (saldo 0 / ESTFAC=2) no escribe.
"""
from __future__ import annotations

import logging
import re
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

#: Columnas que identifican ESTE cobro y se sobreescriben siempre sobre la
#: plantilla; el resto (TIPLCO, TIDLCO, UALLCO, UUMLCO, FUMLCO, ANTLCO, CAJLCO…)
#: se hereda de una fila REAL para no dejar vacía ninguna columna obligatoria.
_OVERRIDE_COLUMNS = (
    "TFALCO", "CFALCO", "LINLCO", "FECLCO", "FALLCO", "IMPLCO", "CPALCO",
    "CPTLCO", "FPALCO", "OBSLCO",
)


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
    fecha_iso: str, importe: float, contrapartida: str, concepto: str,
    fopfac: str, observaciones: str,
) -> dict[str, Any]:
    """Registro para `EscribirRegistro` en F_LCO.

    BUGFIX (BDEscribirRegistroError en producción): mandar solo las 10
    columnas del cobro dejaba vacías las demás y DELSOL rechazaba el insert
    (columna obligatoria vacía). La emisión de facturas nunca falla por esto
    porque COPIA una fila completa (F_LPC→F_LFA). Aquí se hace lo mismo: se
    parte de una fila REAL de F_LCO (misma serie/contrapartida) y solo se
    sobreescriben las columnas de ESTE cobro, respetando el tipo con el que
    DELSOL devuelve cada una. Sin plantilla (tabla vacía) se manda el mínimo."""
    tpl = dict(template or {})
    overrides: dict[str, Any] = {
        "TFALCO": str(serie), "CFALCO": int(codigo), "LINLCO": int(linlco),
        "FECLCO": fecha_iso, "FALLCO": fecha_iso, "IMPLCO": round(importe, 2),
        "CPALCO": str(contrapartida), "CPTLCO": concepto, "FPALCO": fopfac,
        "OBSLCO": observaciones,
    }
    payload = dict(tpl)
    for col in _OVERRIDE_COLUMNS:
        payload[col] = _like(tpl.get(col), overrides[col])
    allowed = LCO_COLUMNS | frozenset(k.upper() for k in tpl)
    return filter_to_real_columns(payload, allowed, tabla="F_LCO")

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y")


def factusol_datetime(value: Any) -> str:
    """Fecha → formato de FACTUSOL (`YYYY-MM-DDT00:00:00`, el mismo con el que
    llegan FECLCO/FALLCO). Acepta ISO, `dd/mm/yyyy`, `dd-mm-yyyy`, `date`/
    `datetime`. Lanza ValueError si no se entiende: nunca se escribe una fecha
    adivinada en la contabilidad."""
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


def concepto_cobro(serie: int, codigo: int, forma: str | None) -> str:
    """Concepto (`CPTLCO`) con la misma convención que usa FACTUSOL
    («COBRO FACTURA Nº: 5 - 260082»), añadiendo la forma de pago del Excel
    si viene («… (Transferencia)»)."""
    base = f"COBRO FACTURA Nº: {serie} - {codigo}"
    forma_txt = re.sub(r"\s+", " ", str(forma or "").strip())
    return f"{base} ({forma_txt})" if forma_txt else base


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
) -> dict[str, Any] | None:
    """Estado de cobro EN VIVO de la factura: total, cobrado, saldo, ESTFAC,
    nº de cobros y el siguiente LINLCO. `None` si la factura no existe."""
    fac = row if row is not None else invoice_row(
        client, serie=serie, codigo=codigo, ejercicio=ejercicio,
    )
    if fac is None:
        return None
    total = _num(fac.get("TOTFAC"), 0.0)
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
    y después marca `ESTFAC=2` con el escritor único de F-3.

    - `importe` por defecto = SALDO pendiente (= total si no había cobros):
      nunca se sobrepaga una factura con cobro parcial previo.
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
    # Plantilla = fila REAL de F_LCO del mismo ejercicio (ver build_lco_payload).
    template = pick_template_row(
        client.load_table("F_LCO", filtro="1=1", ejercicio=ejercicio),
        serie=serie, contrapartida=str(contrapartida),
    )
    payload = build_lco_payload(
        template=template, serie=serie, codigo=codigo,
        linlco=status["next_linlco"], fecha_iso=fecha_iso, importe=amount,
        contrapartida=str(contrapartida),
        concepto=concepto_cobro(serie, codigo, forma),
        fopfac=status["fopfac"],
        observaciones=str(observaciones or "").strip(),
    )
    # Diagnóstico: el registro EXACTO que va a EscribirRegistro (nombres,
    # valores y tipos), para contrastar con una fila real si DELSOL lo rechaza.
    logger.info(
        "factusol cobro %s-%s: EscribirRegistro F_LCO ejercicio=%s plantilla=%s "
        "registro=%s",
        serie, codigo, ejercicio,
        (f"{template.get('TFALCO')}-{template.get('CFALCO')}/L{template.get('LINLCO')}"
         if template else "ninguna (tabla vacía)"),
        {k: (v, type(v).__name__) for k, v in payload.items()},
    )
    try:
        client.write_record("F_LCO", payload, ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001 — se informa, nunca se rompe
        motivo = f"no se pudo escribir el cobro en F_LCO: {str(exc)[:200]}"
        logger.warning("factusol cobro %s-%s: %s", serie, codigo, motivo, exc_info=True)
        return {"registered": False, "status": "write_failed", "motivo": motivo, **status}
    logger.info(
        "factusol cobro %s-%s: F_LCO línea %s, %.2f € → contrapartida %s (%s)",
        serie, codigo, payload["LINLCO"], amount, contrapartida, fecha_iso[:10],
    )
    marked, motivo = mark_invoice_payment(
        client, session, serie=serie, codigo=codigo, paid=True,
        ejercicio=ejercicio, current_estado=status["estfac"],
    )
    return {
        "registered": True, "status": "registered",
        "linlco": payload["LINLCO"], "importe": amount,
        "contrapartida": str(contrapartida), "fecha": fecha_iso[:10],
        "concepto": payload["CPTLCO"], "fopfac": status["fopfac"],
        "estfac_marked": marked, "motivo": motivo,
        "numero": status["numero"], "cliente": status["cliente"],
        "total": status["total"],
    }
