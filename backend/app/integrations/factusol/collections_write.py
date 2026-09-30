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
Idempotente: si la factura ya está cobrada (lo que suman sus líneas de F_LCO
llega al total) no escribe.

Rev. 30/09/2026 (anular / parciales):
- El estado de cobro sale de lo que SUMAN las líneas de F_LCO, no del ESTFAC
  guardado (un ESTFAC=2 sin líneas —apunte borrado— es «pendiente»).
- Importe editable: nunca más que lo pendiente; si es menos, cobro PARCIAL
  (`ESTFAC=1`) y la factura queda con el resto pendiente; varios parciales
  suman. Con el total, `ESTFAC=2`.
- La línea de BoHub va con `TRALCO=0` (no traspasada a tesorería: así se
  distingue de un cobro hecho a mano en FACTUSOL, que lleva 1) y `MULLCO=0`:
  heredar el `MULLCO` de la plantilla (p. ej. 51) la colgaba del cobro
  múltiple / movimiento de OTRA factura.
- ANULAR (`annul_invoice_collection`): borra EXACTAMENTE la línea que registró
  BoHub (serie + número + LINLCO, y solo si fecha, importe y contrapartida
  siguen siendo los registrados) y deja `ESTFAC` según lo que sumen las líneas
  que quedan (0 pendiente / 1 parcial / 2 cobrada).
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
    invoice_estado_kind,
    mark_invoice_estado,
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
#: `OBSLCO`, `TIPLCO`, `UALLCO`… se heredan tal cual: fijarlos de más (p. ej.
#: `FPALCO='002'`) es lo que DELSOL rechazaba. `TRALCO` y `MULLCO` van a 0:
#: la línea de BoHub no está traspasada a tesorería ni pertenece a ningún
#: cobro múltiple (heredarlos la colgaba del movimiento de otra factura).
_OVERRIDE_COLUMNS = (
    "TFALCO", "CFALCO", "LINLCO", "FECLCO", "FALLCO", "IMPLCO", "CPALCO",
    "CPTLCO", "TRALCO", "MULLCO",
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
    estado_real = invoice_estado_kind(total=total, cobrado=summary["total_cobrado"])
    ya_cobrada = saldo <= _EPS
    # F_LCO ENTERA vacía = lectura rota (o ejercicio recién abierto): las
    # líneas no se pueden comprobar, así que se respeta el ESTFAC=2 guardado
    # (como antes) — nunca se da por pendiente, ni se re-cobra, a ciegas.
    lineas_verificables = bool(index)
    if not lineas_verificables and estfac == "2":
        ya_cobrada, estado_real = True, "cobrada"
    return {
        "serie": serie, "codigo": codigo,
        "numero": f"{serie}-{int(codigo):06d}",
        "cliente": str(fac.get("CNOFAC") or "").strip(),
        "cliente_codigo": str(fac.get("CLIFAC") or "").strip() or None,
        "referencia": str(fac.get("REFFAC") or "").strip(),
        "total": round(total, 2),
        "total_cobrado": summary["total_cobrado"],
        "saldo_pendiente": round(saldo, 2),
        "estfac": estfac,
        "fopfac": str(fac.get("FOPFAC") or "").strip(),
        "cobros": len(summary["cobros"]),
        "lineas": summary["cobros"],
        "next_linlco": (max(lineas) + 1) if lineas else 1,
        # Lo que dicen las LÍNEAS de F_LCO (pendiente / parcial / cobrada):
        # manda sobre el ESTFAC guardado, que puede haberse quedado en 2 tras
        # borrar un apunte (caso BOPRIN-99940, 30/09/2026).
        "estado_real": estado_real,
        "lineas_verificables": lineas_verificables,
        "ya_cobrada": ya_cobrada,
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
        "TRALCO": 0, "MULLCO": 0,
    }
    payload = dict(tpl)
    for col in _OVERRIDE_COLUMNS:
        if col in ("TRALCO", "MULLCO") and col not in tpl:
            continue    # sin plantilla, solo el mínimo: no se inventa nada
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
    (SOLO `F_LCO`; `F_COB` no es la cabecera por factura) y después deja
    `ESTFAC` según lo cobrado: 2 si llega al total, 1 (parcial) si no.

    - `importe` por defecto = SALDO pendiente (= total si no había cobros).
      Nunca más que lo pendiente (`amount_exceeds_pending`, sin escribir); si
      es menos, cobro PARCIAL y la factura queda con el resto pendiente.
    - `forma` y `observaciones` se aceptan por compatibilidad con el script y
      quedan en el resultado/auditoría, pero NO se escriben en la fila: la fila
      real no lleva forma en el concepto ni observaciones, y fijarlas era parte
      de lo que DELSOL rechazaba.
    - Idempotente: si ya está cobrada (sus líneas suman el total) devuelve
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

    saldo = float(status["saldo_pendiente"])
    amount = round(float(importe) if importe is not None else saldo, 2)
    if amount <= 0:
        return {
            "registered": False, "status": "nothing_to_collect", **status,
            "motivo": "El importe a cobrar es 0.",
        }
    if amount > saldo + _EPS:
        return {
            "registered": False, "status": "amount_exceeds_pending", **status,
            "motivo": (
                f"El importe ({amount:.2f} €) es mayor que lo pendiente de la "
                f"factura {status['numero']} en FACTUSOL ({saldo:.2f} €)."
            ),
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
    cobrado = round(float(status["total_cobrado"] or 0.0) + amount, 2)
    saldo_after = round(float(status["total"]) - cobrado, 2)
    kind = invoice_estado_kind(total=float(status["total"]), cobrado=cobrado)
    logger.info(
        "factusol cobro %s-%s: F_LCO línea %s, %.2f € → contrapartida %s (%s); "
        "cobrado %.2f de %.2f (%s)",
        serie, codigo, payload["LINLCO"], amount, cpa, fecha_iso[:10], cobrado,
        status["total"], kind,
    )
    marked, motivo, estfac = mark_invoice_estado(
        client, session, serie=serie, codigo=codigo, kind=kind,
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
        "estfac": estfac if marked else status["estfac"],
        "numero": status["numero"], "cliente": status["cliente"],
        "total": status["total"],
        # Estado DESPUÉS del cobro (parcial: queda saldo pendiente).
        "total_cobrado": cobrado, "saldo_pendiente": max(saldo_after, 0.0),
        "cobros": int(status["cobros"]) + 1,
        "parcial": kind == "parcial", "cobrada": kind == "cobrada",
    }


# --- anular ------------------------------------------------------------------


def _lco_rows_of(
    client: FactusolClient, *, serie: int, codigo: int, ejercicio: str,
) -> list[dict[str, Any]]:
    """Líneas de F_LCO de UNA factura (clave compuesta TFALCO + CFALCO). Se
    lee la tabla entera (`1=1`, gotcha nº1: un filtro por una columna mal
    escrita devuelve [] en silencio) y se filtra aquí. Una tabla vacía es una
    lectura rota: se lanza, nunca se decide nada con ella."""
    from app.integrations.factusol.quotes import _int_or_none  # noqa: PLC0415
    from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

    rows = client.load_table("F_LCO", filtro="1=1", ejercicio=ejercicio)
    if not rows:
        raise RuntimeError("F_LCO vuelve vacía: no se puede comprobar el cobro")
    return [
        r for r in rows
        if coerce_serie(r.get("TFALCO")) == serie
        and _int_or_none(r.get("CFALCO")) == int(codigo)
    ]


def _linea(row: dict[str, Any]) -> int | None:
    from app.integrations.factusol.quotes import _int_or_none  # noqa: PLC0415

    return _int_or_none(row.get("LINLCO"))


def lco_line_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Lo que se compara / enseña de una línea de F_LCO."""
    from app.integrations.factusol.catalogs import normalize_code  # noqa: PLC0415

    return {
        "linlco": _linea(row),
        "fecha": str(row.get("FECLCO") or "")[:10] or None,
        "importe": round(_num(row.get("IMPLCO"), 0.0), 2),
        "contrapartida": normalize_code(row.get("CPALCO")) or None,
        "traspasado": str(row.get("TRALCO") or "0").strip() not in ("", "0", "0.0", "False"),
    }


def _diferencias(found: dict[str, Any], esperado: dict[str, Any]) -> list[str]:
    from app.integrations.factusol.catalogs import normalize_code  # noqa: PLC0415

    out: list[str] = []
    if found["traspasado"]:
        out.append("está traspasada a tesorería (TRALCO≠0: cobro hecho o tocado en FACTUSOL)")
    fecha = str(esperado.get("fecha") or "")[:10]
    if fecha and found["fecha"] != fecha:
        out.append(f"fecha {found['fecha']} (registrada {fecha})")
    if esperado.get("importe") is not None:
        registrado = float(esperado["importe"])
        if abs(float(found["importe"]) - registrado) > _EPS:
            out.append(f"importe {found['importe']:.2f} € (registrado {registrado:.2f} €)")
    cpa = normalize_code(esperado.get("contrapartida"))
    if cpa and found["contrapartida"] != cpa:
        out.append(f"contrapartida {found['contrapartida']} (registrada {cpa})")
    return out


def annul_invoice_collection(
    client: FactusolClient,
    session: Session,
    *,
    serie: int,
    codigo: int,
    linlco: int,
    esperado: dict[str, Any],
    ejercicio: str,
) -> dict[str, Any]:
    """ANULA un cobro que registró BoHub: borra SU línea de `F_LCO` y deja la
    factura con el `ESTFAC` que corresponde a lo que sigue cobrado (0
    pendiente / 1 parcial / 2 cobrada). Deshace exactamente lo que escribió
    `register_invoice_collection` (una línea + ESTFAC); nada más.

    Salvaguardas (sin escribir nada si fallan):
    - la línea se identifica por serie + número + LINLCO (nunca por número
      solo) y su fecha, importe y contrapartida tienen que seguir siendo los
      registrados (`mismatch` si los editaron en FACTUSOL);
    - no se toca una línea traspasada a tesorería (`TRALCO≠0`: cobro hecho a
      mano en FACTUSOL);
    - si la línea YA no está (la borraron en FACTUSOL), no se borra nada y
      solo se corrige `ESTFAC` para que cuadre con las líneas que quedan
      (`status="line_missing"`)."""
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    fac = invoice_row(client, serie=serie, codigo=codigo, ejercicio=ejercicio)
    numero = f"{serie}-{int(codigo):06d}"
    if fac is None:
        return {"annulled": False, "status": "invoice_not_found", "numero": numero,
                "motivo": f"No existe la factura {numero} en FACTUSOL."}
    total = round(_num(fac.get("TOTFAC"), 0.0), 2)
    estfac_antes = _estado_str(fac.get("ESTFAC"))
    try:
        lineas = _lco_rows_of(client, serie=serie, codigo=codigo, ejercicio=ejercicio)
    except (RuntimeError, FactusolError) as exc:
        return {"annulled": False, "status": "read_failed", "numero": numero,
                "motivo": f"No se pudieron leer los cobros de {numero}: {str(exc)[:200]}"}
    objetivo = [r for r in lineas if _linea(r) == int(linlco)]
    borrada: dict[str, Any] | None = None
    status = "annulled"
    if objetivo:
        found = lco_line_summary(objetivo[0])
        diffs = _diferencias(found, esperado)
        if diffs:
            return {
                "annulled": False, "status": "mismatch", "numero": numero,
                "encontrado": found,
                "motivo": (
                    f"La línea {linlco} de cobro de {numero} no coincide con lo que "
                    f"registró BoHub: {'; '.join(diffs)}. No se ha borrado nada."
                ),
            }
        filtro = (
            f"TFALCO='{int(serie)}' AND CFALCO={int(codigo)} AND LINLCO={int(linlco)}"
        )
        logger.info(
            "factusol anular cobro %s: BorrarRegistros F_LCO filtro=%s antes=%s",
            numero, filtro, _fmt_record(objetivo[0]),
        )
        try:
            client.delete_records("F_LCO", filtro, ejercicio=ejercicio)
            lineas = _lco_rows_of(client, serie=serie, codigo=codigo, ejercicio=ejercicio)
        except (RuntimeError, FactusolError) as exc:
            return {"annulled": False, "status": "delete_failed", "numero": numero,
                    "motivo": f"No se pudo borrar la línea de cobro: {str(exc)[:200]}"}
        if any(_linea(r) == int(linlco) for r in lineas):
            return {"annulled": False, "status": "delete_failed", "numero": numero,
                    "motivo": (f"FACTUSOL no borró la línea {linlco} de cobro de "
                               f"{numero}: sigue ahí.")}
        borrada = found
    else:
        status = "line_missing"
        logger.info(
            "factusol anular cobro %s: la línea %s ya no estaba en F_LCO; solo se "
            "ajusta ESTFAC", numero, linlco,
        )
    cobrado = round(sum(_num(r.get("IMPLCO"), 0.0) for r in lineas), 2)
    kind = invoice_estado_kind(total=total, cobrado=cobrado)
    marked, motivo, estfac = mark_invoice_estado(
        client, session, serie=serie, codigo=codigo, kind=kind,
        ejercicio=ejercicio, current_estado=estfac_antes,
    )
    logger.info(
        "factusol anular cobro %s: después líneas=%s cobrado=%.2f de %.2f "
        "ESTFAC %s→%s (%s)",
        numero, [_linea(r) for r in lineas], cobrado, total, estfac_antes,
        estfac if marked else estfac_antes, kind,
    )
    return {
        "annulled": True, "status": status, "numero": numero,
        "linlco": int(linlco), "borrada": borrada,
        "total": total, "total_cobrado": cobrado,
        "saldo_pendiente": round(total - cobrado, 2),
        "cobros": len(lineas), "estado_real": kind,
        "estfac_antes": estfac_antes,
        "estfac": estfac if marked else estfac_antes,
        "estfac_marked": marked, "motivo": motivo,
    }
