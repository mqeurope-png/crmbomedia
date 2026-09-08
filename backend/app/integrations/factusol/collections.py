"""ERP-F3-fix1 — cobros de las facturas (tabla F_LCO), SOLO LECTURA.

`F_LCO` son los cobros de FACTUSOL, enlazados a su factura por clave COMPUESTA
`(TFALCO, CFALCO)` — nunca solo por número (la lección que ya nos costó una
factura equivocada). Este módulo lee la tabla UNA vez (~856 filas), la indexa
por esa clave y calcula el saldo pendiente (total − cobrado) de cada factura.

NO escribe nada: registrar un cobro escribiría en `F_LCO`/`F_COB`, tablas
contables que merecen su propio discovery y van con la conciliación bancaria.

Gotcha nº1 de DELSOL: filtrar por una columna inexistente devuelve `[]` en
silencio. Por eso se carga con `1=1` y se indexa en Python — sin filtrar por
ninguna columna de F_LCO. Columnas CONFIRMADAS en producción: TFALCO, CFALCO,
LINLCO, FECLCO, IMPLCO, CPTLCO, CPALCO, FALLCO. No se toca ninguna otra.
"""
from __future__ import annotations

import logging
from typing import Any

from app.integrations.factusol.client import FactusolClient
from app.integrations.factusol.quotes import _factusol_date, _int_or_none, _num
from app.integrations.factusol.service import coerce_serie

logger = logging.getLogger(__name__)

#: Tabla de cobros (localizada en el discovery de F-3-fix1; antes se creía
#: inexistente).
COLLECTIONS_TABLE = "F_LCO"


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def normalize_collection(row: dict[str, Any]) -> dict[str, Any]:
    """Fila de F_LCO → cobro para la UI (solo columnas confirmadas)."""
    return {
        "linea": _int_or_none(row.get("LINLCO")),
        "fecha": (
            _factusol_date(row.get("FECLCO"))
            if row.get("FECLCO") is not None else None
        ),
        "importe": _num(row.get("IMPLCO")),
        # CPALCO = forma de pago (código F_FOP); el caller resuelve su nombre.
        "forma_pago": _clean(row.get("CPALCO")),
        "concepto": _clean(row.get("CPTLCO")),
    }


def load_collections_index(
    client: FactusolClient, *, ejercicio: str,
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """`{(serie, codigo): [cobro, ...]}` leyendo F_LCO UNA sola vez. Se indexa
    por la clave COMPUESTA (TFALCO, CFALCO) — así un cobro de la serie 1 nunca
    se cuela en la factura homónima de la serie 5. Sin N+1."""
    rows = client.load_table(COLLECTIONS_TABLE, filtro="1=1", ejercicio=ejercicio)
    index: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in rows:
        serie = coerce_serie(row.get("TFALCO"))
        codigo = _int_or_none(row.get("CFALCO"))
        if serie is None or codigo is None:
            continue
        index.setdefault((serie, codigo), []).append(normalize_collection(row))
    for cobros in index.values():
        cobros.sort(key=lambda c: (c["linea"] is None, c["linea"] or 0))
    return index


def invoice_collections(
    index: dict[tuple[int, int], list[dict[str, Any]]],
    serie: int,
    codigo: int,
    total: float | None,
) -> dict[str, Any]:
    """Cobros + total cobrado + saldo pendiente de una factura. El saldo es el
    dato que Bart necesita de un vistazo: cuánto le deben."""
    cobros = index.get((serie, codigo), [])
    total_cobrado = round(sum(c["importe"] or 0.0 for c in cobros), 2)
    saldo = (
        round((total or 0.0) - total_cobrado, 2) if total is not None else None
    )
    return {
        "cobros": cobros,
        "total_cobrado": total_cobrado,
        "saldo_pendiente": saldo,
    }


def balance_mismatch(
    estado: Any, saldo: float | None, total: float | None,
) -> str | None:
    """Mensaje de aviso si el saldo NO cuadra con el ESTFAC (saldo 0 debería
    ser «cobrada»=2; saldo = total, «pendiente»=0). NO corrige nada: los datos
    mandan sobre nuestra interpretación. `None` si cuadra o no se puede juzgar
    (p. ej. estado 1 = cobro parcial, que admite cualquier saldo intermedio)."""
    if saldo is None or total is None:
        return None
    est = str(estado).strip() if estado is not None else ""
    if est.endswith(".0") and est[:-2].isdigit():
        est = est[:-2]
    if abs(saldo) < 0.005 and est not in ("2",):
        return f"saldo 0 pero ESTFAC={est or '∅'} (esperado «cobrada»=2)"
    if abs(saldo - total) < 0.005 and est not in ("0", ""):
        return f"saldo = total pero ESTFAC={est} (esperado «pendiente»=0)"
    return None


def payment_annotator(client: FactusolClient, *, ejercicio: str):
    """Anota `total_cobrado`/`saldo_pendiente` en una lista de facturas,
    cargando F_LCO UNA vez (sin N+1). Registra en el log si algún saldo no
    cuadra con el estado, pero enseña SIEMPRE el dato real."""
    index = load_collections_index(client, ejercicio=ejercicio)

    def annotate(docs: list[dict[str, Any]]) -> None:
        for doc in docs:
            if doc.get("serie") is None or not isinstance(doc.get("codigo"), int):
                continue
            summary = invoice_collections(
                index, doc["serie"], doc["codigo"], doc.get("total"),
            )
            doc["total_cobrado"] = summary["total_cobrado"]
            doc["saldo_pendiente"] = summary["saldo_pendiente"]
            warn = balance_mismatch(
                doc.get("estado"), summary["saldo_pendiente"], doc.get("total"),
            )
            if warn:
                logger.warning(
                    "factusol cobros %s: %s", doc.get("numero"), warn,
                )

    return annotate
