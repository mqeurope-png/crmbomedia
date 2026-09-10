"""ERP-F4-B — REGISTRAR un cobro en FACTUSOL (escritura en `F_COB` + `F_LCO` + `ESTFAC`).

`collections.py` es SOLO LECTURA y así se queda; la escritura vive aquí, aparte,
para que quede claro qué toca la contabilidad.

Modelo (discovery `--collections` en producción + dos intentos fallidos en
producción, PR #383/#384):

- `F_COB` (798 filas) es la CABECERA del cobro de una factura y `F_LCO` (856)
  sus LÍNEAS. `F_LCO` no lleva ninguna columna «CODCOB» porque el enlace es la
  CLAVE DE LA FACTURA: `F_COB.(TFACOB, CFACOB)` ↔ `F_LCO.(TFALCO, CFALCO)`.
  798 cabeceras + 58 líneas extra = las 32 facturas con cobros parciales.
  DELSOL rechaza (`BDEscribirRegistroError`) una línea `F_LCO` cuya cabecera
  `F_COB` no existe — por eso fallaba aunque se mandaran las 23 columnas
  (PR #384): faltaba el PADRE, no una columna. Orden obligatorio:
  **F_COB → F_LCO → ESTFAC=2**.
- Las columnas de `F_COB` siguen la convención DELSOL de sufijo: el mismo campo
  con `LCO` → `COB` (`CPALCO ↔ CPACOB`, confirmado). Se retagan las columnas
  del cobro (`_retag`, el mismo idioma que `F_LPC→F_LFA`) y se aplican SOLO las
  que existen en una fila REAL de `F_COB`; si la fila no trae `TFACOB/CFACOB`
  (convención rota) NO se escribe nada y se devuelve un diagnóstico.
- `LINLCO` es correlativo por factura (1..N). `FALLCO` = vencimiento.
- Ambas escrituras parten de una fila REAL (misma serie/contrapartida) para no
  dejar vacía ninguna columna, respetando el tipo JSON con el que DELSOL
  devuelve cada una (PR #384). Las fechas que FIJAMOS van en `YYYY-MM-DD`, el
  único formato con el que la emisión (`FECFAC`) tiene inserts probados.
- Mismo mecanismo de inserción que la emisión: `client.write_record` →
  `/admin/EscribirRegistro`.

Solo escribe cobros: no toca líneas, totales ni nada más de la factura.
Idempotente: si la factura ya está cobrada (saldo 0 / ESTFAC=2) no escribe.
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
from app.integrations.factusol.mapper import _retag, filter_to_real_columns
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

#: Clave de la factura en la cabecera F_COB (convención DELSOL: TFA/CFA + COB).
COB_KEY_COLUMNS = ("TFACOB", "CFACOB")

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y")


def factusol_datetime(value: Any) -> str:
    """Fecha → `YYYY-MM-DD`, el formato con el que la emisión escribe las
    fechas que fija ella misma (`FECFAC`), el único con inserts probados en
    DELSOL. Acepta ISO (con o sin hora), `dd/mm/yyyy`, `dd-mm-yyyy`, `date`/
    `datetime`. Lanza ValueError si no se entiende: nunca se escribe una fecha
    adivinada en la contabilidad."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    if not text:
        raise ValueError("fecha de cobro vacía")
    head = text.split("T")[0].split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(head, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"fecha de cobro no reconocida: {text!r}")


def concepto_cobro(serie: int, codigo: int, forma: str | None) -> str:
    """Concepto (`CPTLCO`/`CPTCOB`) con la misma convención que usa FACTUSOL
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


# --- plantillas (filas reales) -------------------------------------------------


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


def _pick_template(
    rows: list[dict[str, Any]], *, serie: int, contrapartida: str,
    tip_col: str, cpa_col: str,
) -> dict[str, Any] | None:
    """Fila REAL que sirve de plantilla: misma serie y misma contrapartida si
    la hay (mismo tipo de cobro), si no misma serie, si no cualquiera."""
    from app.integrations.factusol.catalogs import normalize_code  # noqa: PLC0415
    from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

    def score(r: dict[str, Any]) -> tuple[int, int]:
        same_serie = coerce_serie(r.get(tip_col)) == serie
        same_cpa = normalize_code(r.get(cpa_col)) == normalize_code(contrapartida)
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


def pick_template_row(
    rows: list[dict[str, Any]], *, serie: int, contrapartida: str,
) -> dict[str, Any] | None:
    """Plantilla de LÍNEA (F_LCO). `None` solo si la tabla está vacía."""
    return _pick_template(
        rows, serie=serie, contrapartida=contrapartida,
        tip_col="TFALCO", cpa_col="CPALCO",
    )


def pick_cob_template(
    rows: list[dict[str, Any]], *, serie: int, contrapartida: str,
) -> dict[str, Any] | None:
    """Plantilla de CABECERA (F_COB)."""
    return _pick_template(
        rows, serie=serie, contrapartida=contrapartida,
        tip_col="TFACOB", cpa_col="CPACOB",
    )


def find_cob_header(
    rows: list[dict[str, Any]], *, serie: int, codigo: int,
) -> dict[str, Any] | None:
    """Cabecera F_COB de ESA factura (clave `(TFACOB, CFACOB)`), o None."""
    from app.integrations.factusol.quotes import _int_or_none  # noqa: PLC0415
    from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

    for r in rows:
        if (coerce_serie(r.get("TFACOB")) == serie
                and _int_or_none(r.get("CFACOB")) == int(codigo)):
            return r
    return None


def _overrides(
    *, serie: int, codigo: int, linlco: int, fecha_iso: str, importe: float,
    contrapartida: str, concepto: str, fopfac: str, observaciones: str,
) -> dict[str, Any]:
    return {
        "TFALCO": str(serie), "CFALCO": int(codigo), "LINLCO": int(linlco),
        "FECLCO": fecha_iso, "FALLCO": fecha_iso, "IMPLCO": round(importe, 2),
        "CPALCO": str(contrapartida), "CPTLCO": concepto, "FPALCO": fopfac,
        "OBSLCO": observaciones,
    }


def build_lco_payload(
    *, template: dict[str, Any] | None, serie: int, codigo: int, linlco: int,
    fecha_iso: str, importe: float, contrapartida: str, concepto: str,
    fopfac: str, observaciones: str,
) -> dict[str, Any]:
    """Registro de LÍNEA para `EscribirRegistro` en F_LCO: fila real +
    sobreescritura de las columnas de ESTE cobro, con el tipo de la plantilla.
    Sin plantilla (tabla vacía) se manda el mínimo."""
    tpl = dict(template or {})
    over = _overrides(
        serie=serie, codigo=codigo, linlco=linlco, fecha_iso=fecha_iso,
        importe=importe, contrapartida=contrapartida, concepto=concepto,
        fopfac=fopfac, observaciones=observaciones,
    )
    payload = dict(tpl)
    for col in _OVERRIDE_COLUMNS:
        payload[col] = _like(tpl.get(col), over[col])
    allowed = LCO_COLUMNS | frozenset(k.upper() for k in tpl)
    return filter_to_real_columns(payload, allowed, tabla="F_LCO")


def build_cob_payload(
    *, template: dict[str, Any], serie: int, codigo: int, fecha_iso: str,
    importe: float, contrapartida: str, concepto: str, fopfac: str,
    observaciones: str,
) -> dict[str, Any]:
    """Registro de CABECERA para `EscribirRegistro` en F_COB.

    Parte de una fila REAL de F_COB y sobreescribe las columnas del cobro
    RETAGADAS `LCO→COB` (`TFALCO→TFACOB`, `IMPLCO→IMPCOB`, `CPALCO→CPACOB`…),
    SOLO las que existen en la plantilla (la cabecera no tiene LINLCO). Nunca
    inventa una columna: lo que la fila real no trae, no se manda."""
    over = _overrides(
        serie=serie, codigo=codigo, linlco=1, fecha_iso=fecha_iso,
        importe=importe, contrapartida=contrapartida, concepto=concepto,
        fopfac=fopfac, observaciones=observaciones,
    )
    payload = dict(template)
    for lco_col, value in over.items():
        cob_col = _retag(lco_col, "LCO", "COB")
        if cob_col in payload:
            payload[cob_col] = _like(payload[cob_col], value)
    return filter_to_real_columns(
        payload, frozenset(k.upper() for k in template), tabla="F_COB",
    )


def _fmt_record(payload: dict[str, Any]) -> dict[str, tuple[Any, str]]:
    return {k: (v, type(v).__name__) for k, v in payload.items()}


# --- registro --------------------------------------------------------------------


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
    """Registra UN cobro de la factura en FACTUSOL, en el orden que exige
    DELSOL: cabecera `F_COB` (si la factura aún no la tiene) → línea `F_LCO` →
    `ESTFAC=2` con el escritor único de F-3.

    - `importe` por defecto = SALDO pendiente (= total si no había cobros):
      nunca se sobrepaga una factura con cobro parcial previo.
    - Idempotente: si ya está cobrada (saldo 0 / ESTFAC=2) devuelve
      `status="already"` sin escribir nada.
    - Nunca lanza por un fallo de FACTUSOL: devuelve `registered=False` +
      motivo (el caller decide parar). Si falla la cabecera NO se escribe la
      línea; si falla la línea, la cabecera ya quedó (se informa). Un fallo al
      marcar ESTFAC tras escribir se refleja en `estfac_marked=False`.
    - Si la fila real de `F_COB` no trae `TFACOB/CFACOB` (convención rota) no
      se escribe NADA: `status="cob_schema_unknown"` con las columnas vistas,
      para volcarla con `--lco-row` antes de tocar la contabilidad.
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
    concepto = concepto_cobro(serie, codigo, forma)
    obs = str(observaciones or "").strip()
    cpa = str(contrapartida)

    # --- 1) Cabecera F_COB (el PADRE que DELSOL exige antes de la línea) ------
    cob_rows = client.load_table("F_COB", filtro="1=1", ejercicio=ejercicio)
    cob_written = False
    header = find_cob_header(cob_rows, serie=serie, codigo=codigo)
    if header is None:
        cob_tpl = pick_cob_template(cob_rows, serie=serie, contrapartida=cpa)
        if cob_tpl is None or not all(k in cob_tpl for k in COB_KEY_COLUMNS):
            vistas = sorted(cob_tpl) if cob_tpl else []
            motivo = (
                "F_COB no trae las columnas de clave de factura "
                f"{COB_KEY_COLUMNS} (columnas vistas: {vistas or 'tabla vacía'}); "
                "no se escribe nada. Vuelca una cabecera real con "
                "`--lco-row` para ajustar el mapeo."
            )
            logger.warning("factusol cobro %s-%s: %s", serie, codigo, motivo)
            return {"registered": False, "status": "cob_schema_unknown",
                    "motivo": motivo, **status}
        cob_payload = build_cob_payload(
            template=cob_tpl, serie=serie, codigo=codigo, fecha_iso=fecha_iso,
            importe=amount, contrapartida=cpa, concepto=concepto,
            fopfac=status["fopfac"], observaciones=obs,
        )
        logger.info(
            "factusol cobro %s-%s: EscribirRegistro F_COB ejercicio=%s "
            "plantilla=%s-%s registro=%s",
            serie, codigo, ejercicio, cob_tpl.get("TFACOB"), cob_tpl.get("CFACOB"),
            _fmt_record(cob_payload),
        )
        try:
            client.write_record("F_COB", cob_payload, ejercicio=ejercicio)
        except Exception as exc:  # noqa: BLE001 — se informa, nunca se rompe
            motivo = f"no se pudo escribir la cabecera en F_COB: {str(exc)[:200]}"
            logger.warning("factusol cobro %s-%s: %s", serie, codigo, motivo,
                           exc_info=True)
            return {"registered": False, "status": "cob_write_failed",
                    "motivo": motivo, **status}
        cob_written = True
    else:
        logger.info(
            "factusol cobro %s-%s: F_COB ya tiene cabecera (cobro parcial "
            "previo); solo se añade la línea", serie, codigo,
        )

    # --- 2) Línea F_LCO ----------------------------------------------------------
    template = pick_template_row(
        client.load_table("F_LCO", filtro="1=1", ejercicio=ejercicio),
        serie=serie, contrapartida=cpa,
    )
    payload = build_lco_payload(
        template=template, serie=serie, codigo=codigo,
        linlco=status["next_linlco"], fecha_iso=fecha_iso, importe=amount,
        contrapartida=cpa, concepto=concepto, fopfac=status["fopfac"],
        observaciones=obs,
    )
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
        motivo = (
            f"no se pudo escribir la línea en F_LCO: {str(exc)[:200]}"
            + (" (la cabecera F_COB SÍ quedó escrita)" if cob_written else "")
        )
        logger.warning("factusol cobro %s-%s: %s", serie, codigo, motivo, exc_info=True)
        return {"registered": False, "status": "write_failed", "motivo": motivo,
                "cob_written": cob_written, **status}
    logger.info(
        "factusol cobro %s-%s: F_LCO línea %s, %.2f € → contrapartida %s (%s)%s",
        serie, codigo, payload["LINLCO"], amount, cpa, fecha_iso,
        " + cabecera F_COB" if cob_written else "",
    )

    # --- 3) Flag ESTFAC=2 --------------------------------------------------------
    marked, motivo = mark_invoice_payment(
        client, session, serie=serie, codigo=codigo, paid=True,
        ejercicio=ejercicio, current_estado=status["estfac"],
    )
    return {
        "registered": True, "status": "registered",
        "linlco": payload["LINLCO"], "importe": amount,
        "contrapartida": cpa, "fecha": fecha_iso, "cob_written": cob_written,
        "concepto": payload["CPTLCO"], "fopfac": status["fopfac"],
        "estfac_marked": marked, "motivo": motivo,
        "numero": status["numero"], "cliente": status["cliente"],
        "total": status["total"],
    }
