"""ERP-E3-A — explorador de documentos FACTUSOL (solo lectura, en vivo).

Generaliza a los cuatro tipos de documento el patrón de lectura en vivo que
C-4 estrenó con las proformas (`quotes.list_quotes`): nada de cache ni sync —
cada listado es un `CargaTabla` contra la contabilidad real.

| Vista        | Cabecera | Líneas  | FK líneas |
|--------------|----------|---------|-----------|
| pedidos      | F_PCL    | F_LPC   | CODLPC    |
| presupuestos | F_PRE    | F_LPS   | CODLPS    |
| albaranes    | F_ALB    | F_LAL   | CODLAL    |
| facturas     | F_FAC    | F_LFA   | CODLFA    |

Estrategia de filtrado (gotcha nº 1: un filtro sobre una columna inexistente
devuelve `[]` EN SILENCIO):

- A SQL se empuja solo UN predicado trivial y selectivo (cliente o serie —
  comparaciones de igualdad sobre columnas confirmadas/por convención).
- El resto de filtros (fecha, texto, estado…) se aplican **en Python** sobre
  las filas normalizadas — mismo criterio y justificación que
  `quotes.list_quotes`: el dialecto SQL de la API no está documentado y una
  función/formato no soportado caería en la trampa del `[]` silencioso.
- **Red de seguridad**: si el predicado SQL devolvió `[]` sin ser `1=1`, se
  re-consulta con `1=1` y se filtra todo en Python. Así una columna mal
  asumida (p. ej. en F_ALB, cuyas columnas aún no se han volcado en vivo)
  degrada a un fetch completo con warning, nunca a un falso «no hay
  resultados».

Columnas usadas, estado de verificación:
- F_PCL: CODPCL/TIPPCL/CLIPCL/CNOPCL/FECPCL/ESTPCL/REFPCL ✅ (C-4 + E2, en
  vivo) — TOTPCL por convención (las bandas NET1PCL sí están confirmadas).
- F_PRE: CODPRE/TIPPRE/CLIPRE/CNOPRE/FECPRE/ESTPRE/REFPRE/TOTPRE ✅ (C-4).
- F_FAC: TODAS ✅ (lista canónica de 167 volcada en vivo, `mapper.FAC_COLUMNS`).
- F_ALB: por convención de sufijo, SIN confirmar (nadie ha visto una fila
  todavía) — protegida por la red de seguridad; el discovery
  `--trace-quote-chain` de este mismo PR vuelca las columnas reales.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.integrations.factusol.client import FactusolClient
from app.integrations.factusol.quotes import (
    _factusol_date,
    _int_or_none,
    _num,
    _sql_escape,
)
from app.integrations.factusol.service import coerce_serie

logger = logging.getLogger(__name__)

#: Máximo de filas por página del listado (la API no soporta LIMIT: se corta
#: en Python tras ordenar DESC).
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class DocSpec:
    """Nombres de tabla/columnas de un tipo de documento. El sufijo de 3
    letras compone las columnas (`CODPCL`, `CLIPCL`, …) por la convención
    DELSOL verificada en el schema."""

    key: str
    table: str
    suffix: str
    lines_table: str
    lines_suffix: str

    @property
    def cod(self) -> str:
        return f"COD{self.suffix}"

    @property
    def tip(self) -> str:
        return f"TIP{self.suffix}"

    @property
    def cli(self) -> str:
        return f"CLI{self.suffix}"

    @property
    def cno(self) -> str:
        """Nombre del cliente copiado en la cabecera (CNOPCL/CNOPRE/CNOFAC
        confirmados en vivo; CNOALB por convención)."""
        return f"CNO{self.suffix}"

    @property
    def fec(self) -> str:
        return f"FEC{self.suffix}"

    @property
    def est(self) -> str:
        return f"EST{self.suffix}"

    @property
    def ref(self) -> str:
        return f"REF{self.suffix}"

    @property
    def tot(self) -> str:
        return f"TOT{self.suffix}"

    @property
    def line_fk(self) -> str:
        return f"COD{self.lines_suffix}"

    @property
    def line_tip(self) -> str:
        return f"TIP{self.lines_suffix}"


DOC_SPECS: dict[str, DocSpec] = {
    "pedidos": DocSpec("pedidos", "F_PCL", "PCL", "F_LPC", "LPC"),
    "presupuestos": DocSpec("presupuestos", "F_PRE", "PRE", "F_LPS", "LPS"),
    "albaranes": DocSpec("albaranes", "F_ALB", "ALB", "F_LAL", "LAL"),
    "facturas": DocSpec("facturas", "F_FAC", "FAC", "F_LFA", "LFA"),
}

#: Etiquetas de estado CONFIRMADAS. Cualquier valor fuera del mapeo se
#: muestra crudo («Estado N») en vez de adivinar — mismo criterio que con
#: ESTPCL en E2: un código mal interpretado en la contabilidad es peor que
#: uno sin traducir. ESTALB/ESTFAC quedan sin mapear hasta que el discovery
#: los confirme.
ESTADO_LABELS: dict[str, dict[str, str]] = {
    "pedidos": {"0": "Pendiente", "2": "Enviado (facturado)"},  # ✅ E2
    "presupuestos": {"0": "Pendiente", "1": "Aceptado"},  # ✅ gotcha nº 17
    "albaranes": {},
    "facturas": {},
}


def estado_label(doc_type: str, raw: Any) -> str:
    value = str(raw).strip() if raw is not None else ""
    if not value:
        return "—"
    # "2.0" → "2": la API devuelve numéricos con tipos variables.
    if value.endswith(".0") and value[:-2].isdigit():
        value = value[:-2]
    return ESTADO_LABELS.get(doc_type, {}).get(value, f"Estado {value}")


def visible_number(serie: Any, codigo: Any) -> str:
    """`TIPO-CÓDIGO` como lo muestra el escritorio (`5-000005`). El código va
    con padding a 6 si es numérico; la serie sin padding."""
    s = coerce_serie(serie)
    n = _int_or_none(codigo)
    cod_txt = f"{n:06d}" if n is not None else str(codigo or "?")
    return f"{s}-{cod_txt}" if s is not None else cod_txt


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def normalize_header(doc_type: str, row: dict[str, Any]) -> dict[str, Any]:
    """Fila de cabecera → la forma que consume la UI. Tolera columnas
    ausentes (`.get`): en F_ALB los nombres son por convención hasta que el
    discovery los confirme, y un None se pinta como «—»."""
    spec = DOC_SPECS[doc_type]
    serie = coerce_serie(row.get(spec.tip))
    codigo = _int_or_none(row.get(spec.cod))
    estado_raw = _clean(row.get(spec.est))
    return {
        "doc_type": doc_type,
        "codigo": codigo if codigo is not None else _clean(row.get(spec.cod)),
        "serie": serie,
        "numero": visible_number(row.get(spec.tip), row.get(spec.cod)),
        "cliente_codigo": _clean(row.get(spec.cli)),
        "cliente_nombre": _clean(row.get(spec.cno)),
        "fecha": _factusol_date(row.get(spec.fec)),
        "total": _num(row.get(spec.tot)) if row.get(spec.tot) is not None else None,
        "estado": estado_raw,
        "estado_label": estado_label(doc_type, estado_raw),
        "referencia": _clean(row.get(spec.ref)),
    }


def _sql_predicate(
    spec: DocSpec, *, codcli: str | None, serie: int | None
) -> str:
    """El único predicado que se empuja a SQL: cliente (lo más selectivo) o
    serie. Igualdades triviales — nada de funciones ni LIKE server-side."""
    if codcli:
        value = str(codcli).strip()
        if value.isdigit():
            return f"{spec.cli}={int(value)}"
        return f"{spec.cli}='{_sql_escape(value)}'"
    if serie is not None:
        return f"{spec.tip}={int(serie)}"
    return "1=1"


def _matches(
    doc: dict[str, Any],
    *,
    codcli: str | None,
    serie: int | None,
    estado: str | None,
    fecha_desde: str | None,
    fecha_hasta: str | None,
    q: str | None,
) -> bool:
    """Filtros en Python sobre la fila normalizada. Incluye los que ya fueron
    a SQL (idempotente): es lo que hace correcto el fallback a `1=1`."""
    if codcli and (doc["cliente_codigo"] or "") != str(codcli).strip():
        return False
    if serie is not None and doc["serie"] != serie:
        return False
    if estado is not None and (doc["estado"] or "") != str(estado).strip():
        return False
    fecha = doc["fecha"]
    if fecha_desde and (fecha is None or fecha < fecha_desde):
        return False
    if fecha_hasta and (fecha is None or fecha > fecha_hasta):
        return False
    needle = (q or "").strip().casefold()
    if needle:
        haystack = " ".join(
            str(v) for v in (
                doc["numero"], doc["codigo"], doc["referencia"],
                doc["cliente_nombre"], doc["cliente_codigo"],
            ) if v is not None
        ).casefold()
        if needle not in haystack:
            return False
    return True


def list_documents(
    client: FactusolClient,
    doc_type: str,
    *,
    ejercicio: str,
    codcli: str | None = None,
    serie: int | None = None,
    estado: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    q: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    """Listado en vivo de un tipo de documento, filtrado y paginado.

    Devuelve `{"items": [...], "total": N}` — `total` es el nº de documentos
    que casan TODOS los filtros (para la paginación de la UI), `items` la
    página `offset:offset+limit` ordenada por código DESC.
    """
    spec = DOC_SPECS[doc_type]
    predicate = _sql_predicate(spec, codcli=codcli, serie=serie)
    rows = client.load_table(
        spec.table,
        filtro=f"{predicate} ORDER BY {spec.cod} DESC",
        ejercicio=ejercicio,
    )
    if not rows and predicate != "1=1":
        # Red anti-gotcha-1: si el predicado no casó nada puede ser que la
        # columna no exista (F_ALB aún sin volcar). Refetch completo +
        # filtros en Python; si tampoco hay nada, es que de verdad no hay.
        logger.warning(
            "factusol.documents: filtro SQL %r sobre %s devolvió []; "
            "refetch con 1=1 y filtrado en Python (¿columna sin verificar?)",
            predicate, spec.table,
        )
        rows = client.load_table(
            spec.table,
            filtro=f"1=1 ORDER BY {spec.cod} DESC",
            ejercicio=ejercicio,
        )

    docs = [normalize_header(doc_type, r) for r in rows]
    docs = [
        d for d in docs
        if _matches(
            d, codcli=codcli, serie=serie, estado=estado,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta, q=q,
        )
    ]
    # Orden DESC garantizado en Python (el ORDER BY del filtro no está
    # documentado como fiable; re-ordenar filas ya traídas es gratis).
    docs.sort(
        key=lambda d: d["codigo"] if isinstance(d["codigo"], int) else -1,
        reverse=True,
    )
    total = len(docs)
    limit = max(1, min(int(limit or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    offset = max(0, int(offset or 0))
    return {"items": docs[offset:offset + limit], "total": total}


def normalize_line(doc_type: str, row: dict[str, Any]) -> dict[str, Any]:
    """Línea `F_L**` → forma de la UI (misma convención de prefijos que
    F_LPS/F_LPC, verificada; F_LAL por convención)."""
    suffix = DOC_SPECS[doc_type].lines_suffix
    return {
        "position": _int_or_none(row.get(f"POS{suffix}")) or 0,
        "codart": _clean(row.get(f"ART{suffix}")),
        "description": str(row.get(f"DES{suffix}") or "").strip(),
        "quantity": _num(row.get(f"CAN{suffix}")),
        "unit_price": _num(row.get(f"PRE{suffix}")),
        "line_total": _num(row.get(f"TOT{suffix}")),
    }


def get_document(
    client: FactusolClient,
    doc_type: str,
    *,
    serie: int,
    codigo: int,
    ejercicio: str,
) -> dict[str, Any] | None:
    """Cabecera + líneas de un documento por su clave COMPUESTA (serie,
    código) — el código solo es único por serie (ERP-E2-fix2).

    El filtro SQL va por el código (numérico, trivial); la serie se casa en
    Python para no depender de cómo viaje `TIP*` ('5' vs 5)."""
    spec = DOC_SPECS[doc_type]
    rows = client.load_table(
        spec.table, filtro=f"{spec.cod}={int(codigo)}", ejercicio=ejercicio,
    )
    match = next(
        (r for r in rows if coerce_serie(r.get(spec.tip)) == serie), None,
    )
    if match is None and len(rows) == 1 and coerce_serie(
        rows[0].get(spec.tip)
    ) is None:
        # Tabla sin columna TIP* utilizable (no debería pasar; defensivo):
        # si el código es único, se acepta la única fila.
        match = rows[0]
    if match is None:
        return None

    line_rows = client.load_table(
        spec.lines_table,
        filtro=f"{spec.line_fk}={int(codigo)}",
        ejercicio=ejercicio,
    )
    # Join COMPUESTO (tipo, código), documentado en F_LPC↔F_PCL: sin casar la
    # serie se mezclarían las líneas del documento homónimo de otra serie.
    lines = [
        normalize_line(doc_type, r)
        for r in line_rows
        if coerce_serie(r.get(spec.line_tip)) in (serie, None)
    ]
    lines.sort(key=lambda ln: ln["position"])
    header = normalize_header(doc_type, match)
    header["lines"] = lines
    return header
