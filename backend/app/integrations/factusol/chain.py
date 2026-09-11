"""ERP-E3-B — crear la cadena de documentos FACTUSOL:
presupuesto → albarán → factura (y presupuesto → factura directa).

Generaliza la maquinaria de emisión de facturas de E2 (copia por sufijo +
allowlist + contador por serie + worker serial + compensación) a cualquier
conversión origen→destino, con el ENLACE que el discovery de 2026-08-20
confirmó empíricamente sobre la cadena real `5-000027 → 5-500004 → 5-260063`:

- Las cabeceras NO llevan el enlace (el `PEDALB` del albarán real estaba
  vacío; no existe `PREALB`). **El enlace vive en las LÍNEAS**, con tres
  campos por línea: `DOC*` (tipo de documento origen: 'P' presupuesto,
  'A' albarán, 'C' pedido de cliente), `DTP*` (serie del origen) y `DCO*`
  (número del origen).
- Las líneas usan clave COMPUESTA `(TIP*, COD*)` + `POS*`. Nunca filtrar por
  el número solo: `CODLPS=27` devuelve líneas de 3 presupuestos de series
  distintas.
- Cada TIPO de documento tiene su propio contador por serie: facturas serie
  5 en 260xxx, albaranes serie 5 en 500xxx. `next_doc_code` = `max(COD*
  WHERE TIP*=serie) + 1` con guarda anti-colisión.
- Los estados se propagan por la copia de sufijo (`ESTPRE→ESTALB` — el
  albarán real trazado tiene ESTALB=1 heredado de ESTPRE=1). No se inventa
  ningún valor.

Allowlist de F_ALB (136 columnas) y F_LAL (36): se construye de una fila
REAL cargada en vivo (cacheada 1 h) — el mismo criterio que llevó a
`FAC_COLUMNS`, pero sin transcribir a mano: escribir una columna que la
tabla no tiene tumba el `EscribirRegistro` entero (gotcha nº 13). Si la
tabla está vacía se cae a la lista de referencia del discovery.

Todo pasa por la cola serial `factusol:writes` (worker concurrency=1): los
contadores se leen con MAX+1 justo antes de escribir.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.integrations.factusol.client import FactusolClient, FactusolError
from app.integrations.factusol.documents import DOC_SPECS, DocSpec, visible_number
from app.integrations.factusol.mapper import (
    FAC_COLUMNS,
    LFA_COLUMNS,
    _retag,
    filter_to_real_columns,
)
from app.integrations.factusol.quotes import _int_or_none
from app.integrations.factusol.service import (
    coerce_serie,
    mark_origin_converted,
    serie_of_row,
)

logger = logging.getLogger(__name__)

#: Código de tipo de documento ORIGEN que FACTUSOL escribe en `DOC*` de cada
#: línea del documento hijo. Confirmado en vivo: 'P' presupuesto, 'A'
#: albarán, 'C' pedido de cliente.
ORIGIN_CODES: dict[str, str] = {
    "presupuestos": "P",
    "albaranes": "A",
    "pedidos": "C",
}

#: Conversiones soportadas (origen → destinos válidos). presupuesto→albarán,
#: presupuesto→factura (directa) y albarán→factura — la cadena del ciclo.
ALLOWED_CONVERSIONS: dict[str, tuple[str, ...]] = {
    "presupuestos": ("albaranes", "facturas"),
    "albaranes": ("facturas",),
    # Fase 2: pedido de cliente NO-web → albarán, con la misma maquinaria
    # (`*PCL→*ALB` / `*LPC→*LAL`). El guardarraíl web (un F_PCL que sea un
    # pedido de WooCommerce NO recibe albarán de BoHub) vive en
    # `convert_document`, y no lo salta `force`.
    "pedidos": ("albaranes",),
}

#: Singular legible para mensajes («ya tiene albarán 5-500004»).
SINGULAR: dict[str, str] = {
    "presupuestos": "presupuesto",
    "albaranes": "albarán",
    "facturas": "factura",
    "pedidos": "pedido",
}

#: Prefijos del ORIGEN que NO se copian al destino: auditoría (usuario/hora/
#: fecha de modificación), marca de impreso y «pasado a». FACTUSOL rellena
#: los suyos. EST* SÍ se copia (los estados se propagan por sufijo — el
#: albarán real trazado lo confirma); FEC* la fija el caller (hoy).
EXCLUDED_SOURCE_PREFIXES: tuple[str, ...] = (
    "USU", "USM", "HOR", "FUM", "IMP", "PAS", "FEC", "CID", "PDF",
)

#: Referencia de columnas de F_ALB/F_LAL del discovery (2026-08-20, en vivo).
#: Solo es el FALLBACK de la allowlist cuando la tabla está vacía y no hay
#: fila viva de la que leer; con datos, manda la fila real.
ALB_REFERENCE_COLUMNS: frozenset[str] = frozenset({
    "TIPALB", "CODALB", "FECALB", "ESTALB", "ALMALB", "CLIALB", "CNOALB",
    "CDOALB", "CPOALB", "CCPALB", "CPRALB", "CNIALB", "TELALB", "CEMALB",
    "CPAALB", "TOTALB", "FOPALB", "PRTALB", "FPEALB", "PEDALB", "REFALB",
    "OBRALB", "OB1ALB", "OB2ALB", "TIVALB", "REQALB", "TRZALB",
    "NET1ALB", "NET2ALB", "NET3ALB", "NET4ALB",
    "BAS1ALB", "BAS2ALB", "BAS3ALB", "BAS4ALB",
    "PIVA1ALB", "PIVA2ALB", "PIVA3ALB", "IIVA1ALB", "IIVA2ALB", "IIVA3ALB",
    "TIVA1ALB", "TIVA2ALB", "TIVA3ALB",
    "PREC1ALB", "PREC2ALB", "PREC3ALB", "IREC1ALB", "IREC2ALB", "IREC3ALB",
    "PDTO1ALB", "PDTO2ALB", "PDTO3ALB", "PDTO4ALB",
    "IDTO1ALB", "IDTO2ALB", "IDTO3ALB", "IDTO4ALB",
    "PFIN1ALB", "PFIN2ALB", "PFIN3ALB", "PFIN4ALB",
    "IFIN1ALB", "IFIN2ALB", "IFIN3ALB", "IFIN4ALB",
    "PPOR1ALB", "PPOR2ALB", "PPOR3ALB", "PPOR4ALB",
    "IPOR1ALB", "IPOR2ALB", "IPOR3ALB", "IPOR4ALB",
    "PPPA1ALB", "PPPA2ALB", "PPPA3ALB", "PPPA4ALB",
    "IPPA1ALB", "IPPA2ALB", "IPPA3ALB", "IPPA4ALB",
    "PRET1ALB", "IRET1ALB",
})
LAL_REFERENCE_COLUMNS: frozenset[str] = frozenset({
    "TIPLAL", "CODLAL", "POSLAL", "ARTLAL", "DESLAL", "CANLAL", "PRELAL",
    "TOTLAL", "DOCLAL", "DTPLAL", "DCOLAL", "DT1LAL", "DT2LAL", "DT3LAL",
    "IVALAL", "PIVLAL", "TIVLAL", "EJELAL", "COSLAL", "COMLAL", "MEMLAL",
    "NIMLAL", "IINLAL", "IMALAL", "FONLAL", "SUMLAL", "TCOLAL", "BULLAL",
    "ALTLAL", "ANCLAL", "CE1LAL", "CE2LAL", "FCOLAL", "FFALAL", "FIMLAL",
    "RTILAL",
})

_REFERENCE_FALLBACKS: dict[str, frozenset[str]] = {
    "F_ALB": ALB_REFERENCE_COLUMNS,
    "F_LAL": LAL_REFERENCE_COLUMNS,
    "F_FAC": FAC_COLUMNS,
    "F_LFA": LFA_COLUMNS,
}

#: Estado INICIAL del documento recién creado, por tipo destino (Fase 2). El
#: estado NO se hereda del origen: un presupuesto aceptado (`ESTPRE=1`) creaba
#: un albarán que nacía «Facturado» (`ESTALB=1`, confirmado por el dry-run del
#: 2026-09-11), y un pedido «Enviado» (`ESTPCL=2`) daría un valor que no existe
#: en `ESTALB` (0/1 confirmados en el escritorio). Las facturas nacen
#: «pendiente de cobro» (`ESTFAC=0`, confirmado en F-3): heredar `ESTPRE=1` las
#: mostraba como «cobro parcial». Enteros, como la fila real.
INITIAL_ESTADO_BY_TARGET: dict[str, int] = {"albaranes": 0, "facturas": 0}

#: Guard de esquema estricto (no escribir si no cuadra) por tipo destino. Los
#: albaranes son la Fase 2 (contraste hecho contra filas reales); las facturas
#: siguen el comportamiento de E3-B (aviso en el log, sin bloquear).
STRICT_SCHEMA_TARGETS: frozenset[str] = frozenset({"albaranes"})

#: Cache en proceso de columnas vivas: {tabla: (expira_epoch, columnas)}.
#: TTL largo — el schema de FACTUSOL no cambia entre deploys.
_LIVE_COLUMNS_CACHE: dict[str, tuple[float, frozenset[str]]] = {}
LIVE_COLUMNS_TTL_SECONDS = 3600


def live_columns(
    client: FactusolClient, tabla: str, *, ejercicio: str
) -> frozenset[str]:
    """Columnas REALES de la tabla, leídas de una fila viva (cacheadas).

    Es la allowlist de escritura: lo que no esté aquí no se envía nunca
    (gotcha nº 13). Con la tabla vacía se cae a la lista de referencia del
    discovery — y si tampoco hay, error explícito antes que escribir a
    ciegas."""
    now = time.time()
    cached = _LIVE_COLUMNS_CACHE.get(tabla)
    if cached and cached[0] > now:
        return cached[1]
    rows = client.load_table(tabla, filtro="1=1", ejercicio=ejercicio)
    if rows:
        columns = frozenset(rows[0].keys())
        _LIVE_COLUMNS_CACHE[tabla] = (now + LIVE_COLUMNS_TTL_SECONDS, columns)
        return columns
    fallback = _REFERENCE_FALLBACKS.get(tabla)
    if fallback:
        logger.warning(
            "factusol.chain: %s sin filas; allowlist desde la referencia "
            "del discovery (%d columnas)", tabla, len(fallback),
        )
        return fallback
    raise FactusolError(
        f"No hay fila viva ni referencia de columnas para {tabla}: "
        "no se puede escribir con seguridad."
    )


def type_mismatches(
    payload: dict[str, Any], template: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """Columnas del payload cuyo TIPO JSON no coincide con el de la fila REAL:
    `'5'` vs `5`, `''` vs `0`. Es la trampa que costó los cobros (DELSOL quiere
    de vuelta el mismo tipo que devuelve) y la que cazó `CODALB` como texto en
    el dry-run de la Fase 2. `int` y `float` se consideran equivalentes (la
    API devuelve 5 y 5.0 según la fila); `None` no se juzga."""
    out: list[tuple[str, str, str]] = []
    for col, val in payload.items():
        if col not in template:
            continue
        real = template[col]
        if val is None or real is None:
            continue
        if isinstance(val, bool) or isinstance(real, bool):
            if type(val) is not type(real):
                out.append((col, type(val).__name__, type(real).__name__))
            continue
        if type(val) is type(real):
            continue
        if isinstance(val, int | float) and isinstance(real, int | float):
            continue
        out.append((col, type(val).__name__, type(real).__name__))
    return out


def pick_template_row(
    rows: list[dict[str, Any]], *, tip_col: str, cod_col: str, serie: int,
) -> dict[str, Any] | None:
    """Fila REAL más reciente (mayor código) de la MISMA serie: la plantilla
    contra la que se contrastan los tipos del registro. Sin ninguna de esa
    serie, la más reciente de cualquiera; `None` con la tabla vacía."""
    def cod(r: dict[str, Any]) -> int:
        n = _int_or_none(r.get(cod_col))
        return n if n is not None else -1

    same = [r for r in rows if coerce_serie(r.get(tip_col)) == serie]
    pool = same or rows
    return max(pool, key=cod) if pool else None


def format_record(payload: dict[str, Any]) -> dict[str, tuple[Any, str]]:
    """`{col: (valor, tipo)}` — el registro EXACTO que va a EscribirRegistro,
    para contrastarlo campo a campo con `--alb-row` si DELSOL lo rechaza."""
    return {k: (v, type(v).__name__) for k, v in payload.items()}


def schema_problems(
    client: FactusolClient, *, dst: DocSpec, cabecera: dict[str, Any],
    lineas: list[dict[str, Any]], serie: int, ejercicio: str,
) -> list[str]:
    """El guard del dry-run (Fase 2), antes de escribir: columnas fuera de las
    vivas y tipos distintos a la fila REAL más reciente de la misma serie, en
    cabecera y líneas. Lista vacía = el esquema cuadra."""
    problems: list[str] = []
    allowed_h = live_columns(client, dst.table, ejercicio=ejercicio)
    allowed_l = live_columns(client, dst.lines_table, ejercicio=ejercicio)
    unknown_h = sorted(set(cabecera) - allowed_h)
    if unknown_h:
        problems.append(f"{dst.table}: columnas desconocidas {', '.join(unknown_h)}")
    header_rows = client.load_table(dst.table, filtro="1=1", ejercicio=ejercicio)
    template = pick_template_row(
        header_rows, tip_col=dst.tip, cod_col=dst.cod, serie=serie,
    )
    if template is not None:
        for col, got, real in type_mismatches(cabecera, template):
            problems.append(
                f"{dst.table}.{col}: payload {got} ({cabecera.get(col)!r}), "
                f"fila real {real} ({template.get(col)!r})"
            )
    line_rows = client.load_table(dst.lines_table, filtro="1=1", ejercicio=ejercicio)
    line_template = pick_template_row(
        line_rows, tip_col=dst.line_tip, cod_col=dst.line_fk, serie=serie,
    )
    seen: set[str] = set()
    for linea in lineas:
        for col in sorted(set(linea) - allowed_l):
            if col not in seen:
                seen.add(col)
                problems.append(f"{dst.lines_table}: columna desconocida {col}")
        if line_template is None:
            continue
        for col, got, real in type_mismatches(linea, line_template):
            key = f"L:{col}"
            if key in seen:
                continue
            seen.add(key)
            problems.append(
                f"{dst.lines_table}.{col}: payload {got} ({linea.get(col)!r}), "
                f"fila real {real} ({line_template.get(col)!r})"
            )
    return problems


def next_doc_code(
    client: FactusolClient,
    tabla: str,
    *,
    tip_col: str,
    cod_col: str,
    serie: int,
    ejercicio: str,
) -> str:
    """Contador del documento: `max(COD* WHERE TIP*=serie) + 1`.

    Cada TIPO de documento lleva su propio correlativo por serie (facturas
    serie 5 en 260xxx, albaranes serie 5 en 500xxx). El filtro de serie va
    en Python (gotcha nº 1: no sabemos si TIP* viaja como '5' o 5) y hay
    guarda anti-colisión contra la clave compuesta `(TIP*, COD*)` — como
    `next_codfac` de E2-fix2. Serie sin documentos arranca en 1."""
    rows = client.load_table(
        tabla, filtro=f"1=1 ORDER BY {cod_col} DESC", ejercicio=ejercicio,
    )
    in_series = [
        n for n in (
            _int_or_none(r.get(cod_col))
            for r in rows if coerce_serie(r.get(tip_col)) == serie
        )
        if n is not None
    ]
    if not in_series:
        logger.warning(
            "factusol.chain: %s serie %d sin documentos en %s; el contador "
            "arranca en 1.", tabla, serie, ejercicio,
        )
        return "1"
    ocupados = set(in_series)
    candidato = max(in_series) + 1
    while candidato in ocupados:
        logger.warning(
            "factusol.chain: (%s=%d, %s=%d) ya existe; se avanza.",
            tip_col, serie, cod_col, candidato,
        )
        candidato += 1
    return str(candidato)


def load_source_document(
    client: FactusolClient, spec: DocSpec, *, tip: int, cod: int,
    ejercicio: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Cabecera + líneas del documento ORIGEN por su clave compuesta.

    El filtro SQL va por el número (igualdad numérica, segura); la serie se
    casa en Python — nunca por el número solo: el mismo COD* convive en
    varias series (demostrado en vivo con CODLPS=27)."""
    rows = client.load_table(
        spec.table, filtro=f"{spec.cod}={int(cod)}", ejercicio=ejercicio,
    )
    header = next(
        (r for r in rows if coerce_serie(r.get(spec.tip)) == tip), None,
    )
    if header is None:
        raise FactusolError(
            f"No existe el documento {tip}-{cod} en {spec.table}."
        )
    line_rows = client.load_table(
        spec.lines_table,
        filtro=f"{spec.line_fk}={int(cod)}",
        ejercicio=ejercicio,
    )
    lines = [
        r for r in line_rows
        if coerce_serie(r.get(spec.line_tip)) in (tip, None)
    ]
    return header, lines


def find_existing_children(
    client: FactusolClient, source_type: str, target_type: str,
    *, tip: int, cod: int, ejercicio: str,
) -> list[str]:
    """Números visibles de los documentos DESTINO que ya apuntan al origen
    por `DOC/DTP/DCO` en sus líneas — el anti-duplicado.

    `DCO*` es numérica y está confirmada en vivo (discovery), así que la
    igualdad server-side es segura; DOC*/DTP* se casan en Python."""
    dst = DOC_SPECS[target_type]
    origin_code = ORIGIN_CODES[source_type]
    line_suffix = dst.lines_suffix
    rows = client.load_table(
        dst.lines_table,
        filtro=f"DCO{line_suffix}={int(cod)}",
        ejercicio=ejercicio,
    )
    children: list[str] = []
    for row in rows:
        if str(row.get(f"DOC{line_suffix}") or "").strip().upper() != origin_code:
            continue
        if coerce_serie(row.get(f"DTP{line_suffix}")) != tip:
            continue
        numero = visible_number(row.get(dst.line_tip), row.get(dst.line_fk))
        if numero not in children:
            children.append(numero)
    return children


# --- estado del ciclo (Parte D) ----------------------------------------------

#: Tipo de documento por código de enlace `DOC*` (inverso de ORIGIN_CODES).
TYPE_BY_ORIGIN_CODE: dict[str, str] = {v: k for k, v in ORIGIN_CODES.items()}

#: Cache corto del índice del ciclo: {ejercicio: (expira, índice)}. 30 s: lo
#: justo para que pestañas + detalle no recarguen F_LAL/F_LFA en cada click,
#: sin esconder mucho rato un documento recién creado.
_CHAIN_INDEX_CACHE: dict[str, tuple[float, ChainIndex]] = {}
CHAIN_INDEX_TTL_SECONDS = 30


@dataclass(frozen=True)
class ChainIndex:
    """Enlaces `DOC/DTP/DCO` de TODAS las líneas de albaranes y facturas,
    indexados en las dos direcciones. Dos cargas (F_LAL, F_LFA) por índice.

    - `children[target]`: (código DOC, serie origen, nº origen) → hijos
      `(serie, código)` de ese tipo destino que apuntan al origen.
    - `origins[target]`: (serie, código) del destino → orígenes
      `(código DOC, serie, nº)` que declaran sus líneas."""

    children: dict[str, dict[tuple[str, int, int], list[tuple[int, int]]]]
    origins: dict[str, dict[tuple[int, int], list[tuple[str, int, int]]]]


def _index_lines(
    rows: list[dict[str, Any]], suffix: str,
) -> tuple[
    dict[tuple[str, int, int], list[tuple[int, int]]],
    dict[tuple[int, int], list[tuple[str, int, int]]],
]:
    children: dict[tuple[str, int, int], list[tuple[int, int]]] = {}
    origins: dict[tuple[int, int], list[tuple[str, int, int]]] = {}
    for row in rows:
        child_serie = coerce_serie(row.get(f"TIP{suffix}"))
        child_cod = _int_or_none(row.get(f"COD{suffix}"))
        doc = str(row.get(f"DOC{suffix}") or "").strip().upper()
        origin_serie = coerce_serie(row.get(f"DTP{suffix}"))
        origin_cod = _int_or_none(row.get(f"DCO{suffix}"))
        if child_serie is None or child_cod is None:
            continue
        if not doc or origin_serie is None or origin_cod is None:
            continue  # línea sin enlace (documento creado suelto)
        child = (child_serie, child_cod)
        origin = (doc, origin_serie, origin_cod)
        bucket = children.setdefault(origin, [])
        if child not in bucket:
            bucket.append(child)
        back = origins.setdefault(child, [])
        if origin not in back:
            back.append(origin)
    return children, origins


def load_chain_index(
    client: FactusolClient, *, ejercicio: str, force_refresh: bool = False,
) -> ChainIndex:
    """Índice del ciclo del ejercicio, cacheado 30 s por proceso.

    `force_refresh` (E3-B-fix1): salta el cache — lo pide el frontend justo
    después de crear un documento para repintar el badge al momento, sin
    esperar a que expire el TTL."""
    now = time.time()
    cached = _CHAIN_INDEX_CACHE.get(ejercicio)
    if cached and cached[0] > now and not force_refresh:
        return cached[1]
    alb = DOC_SPECS["albaranes"]
    fac = DOC_SPECS["facturas"]
    alb_children, alb_origins = _index_lines(
        client.load_table(alb.lines_table, filtro="1=1", ejercicio=ejercicio),
        alb.lines_suffix,
    )
    fac_children, fac_origins = _index_lines(
        client.load_table(fac.lines_table, filtro="1=1", ejercicio=ejercicio),
        fac.lines_suffix,
    )
    index = ChainIndex(
        children={"albaranes": alb_children, "facturas": fac_children},
        origins={"albaranes": alb_origins, "facturas": fac_origins},
    )
    _CHAIN_INDEX_CACHE[ejercicio] = (now + CHAIN_INDEX_TTL_SECONDS, index)
    return index


def _doc_ref(doc_type: str, serie: int, codigo: int) -> dict[str, Any]:
    return {
        "doc_type": doc_type, "serie": serie, "codigo": codigo,
        "numero": visible_number(serie, codigo),
    }


def cycle_of(
    index: ChainIndex, doc_type: str, serie: int | None, codigo: int | None,
) -> dict[str, Any] | None:
    """Posición del documento en el ciclo PRE→ALB→FAC, según el índice.

    - presupuestos/pedidos: hijos albarán y factura (directa O a través del
      albarán) + `estado` ∈ {pendiente, con_albaran, facturado}.
    - albaranes: origen (por sus propias líneas) + facturas hijas + `estado`
      ∈ {pendiente, facturado}.
    - facturas: solo el origen; sin `estado` (una factura no tiene «siguiente
      paso» en el ciclo)."""
    if serie is None or codigo is None:
        return None
    if doc_type in ("presupuestos", "pedidos"):
        origin_code = ORIGIN_CODES[doc_type]
        albaranes = index.children["albaranes"].get(
            (origin_code, serie, codigo), [],
        )
        facturas = list(index.children["facturas"].get(
            (origin_code, serie, codigo), [],
        ))
        for alb_serie, alb_cod in albaranes:
            for hija in index.children["facturas"].get(
                ("A", alb_serie, alb_cod), [],
            ):
                if hija not in facturas:
                    facturas.append(hija)
        estado = (
            "facturado" if facturas
            else "con_albaran" if albaranes else "pendiente"
        )
        return {
            "albaranes": [_doc_ref("albaranes", s, c) for s, c in albaranes],
            "facturas": [_doc_ref("facturas", s, c) for s, c in facturas],
            "origen": [],
            "estado": estado,
        }
    if doc_type == "albaranes":
        facturas = index.children["facturas"].get(("A", serie, codigo), [])
        return {
            "albaranes": [],
            "facturas": [_doc_ref("facturas", s, c) for s, c in facturas],
            "origen": [
                _doc_ref(TYPE_BY_ORIGIN_CODE[d], s, c)
                for d, s, c in index.origins["albaranes"].get(
                    (serie, codigo), [],
                )
                if d in TYPE_BY_ORIGIN_CODE
            ],
            "estado": "facturado" if facturas else "pendiente",
        }
    if doc_type == "facturas":
        return {
            "albaranes": [],
            "facturas": [],
            "origen": [
                _doc_ref(TYPE_BY_ORIGIN_CODE[d], s, c)
                for d, s, c in index.origins["facturas"].get(
                    (serie, codigo), [],
                )
                if d in TYPE_BY_ORIGIN_CODE
            ],
            "estado": None,
        }
    return None


def cycle_annotator(
    client: FactusolClient, doc_type: str, *, ejercicio: str,
    force_refresh: bool = False,
):
    """Callable para `list_documents(annotate=…)`: añade `ciclo` a cada doc.

    Se inyecta desde el endpoint (documents no importa chain — evita el
    import circular). El índice se carga UNA vez por llamada, cacheado."""
    index = load_chain_index(
        client, ejercicio=ejercicio, force_refresh=force_refresh,
    )

    def annotate(docs: list[dict[str, Any]]) -> None:
        for doc in docs:
            codigo = doc.get("codigo")
            doc["ciclo"] = cycle_of(
                index, doc_type, doc.get("serie"),
                codigo if isinstance(codigo, int) else None,
            )

    return annotate


def build_target_header(
    source_row: dict[str, Any],
    *,
    src: DocSpec,
    dst: DocSpec,
    serie: int,
    codigo: str,
    fecha: str,
    allowed: frozenset[str],
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cabecera destino: copia por sufijo `src→dst` (arrastra cliente,
    dirección, bandas de IVA, totales y forma de pago), menos la auditoría; e
    inyecta la clave `(TIP, COD)` + fecha + el estado INICIAL del tipo
    destino (Fase 2: el estado NO se hereda — ver `INITIAL_ESTADO_BY_TARGET`).
    `COD*` va como ENTERO, como en la fila real (dry-run 2026-09-11: DELSOL
    quiere de vuelta el tipo que devuelve). `overrides` (p. ej. `FOPALB` del
    paso de pago) pisa lo copiado. Todo filtrado por la allowlist viva."""
    payload: dict[str, Any] = {}
    for col, val in source_row.items():
        if not col.endswith(src.suffix):
            continue
        prefix = col[: -len(src.suffix)]
        if any(prefix.startswith(p) for p in EXCLUDED_SOURCE_PREFIXES):
            continue
        payload[_retag(col, src.suffix, dst.suffix)] = val
    payload[dst.tip] = str(serie)
    payload[dst.cod] = int(codigo)
    payload[f"FEC{dst.suffix}"] = fecha
    if dst.key in INITIAL_ESTADO_BY_TARGET:
        payload[dst.est] = INITIAL_ESTADO_BY_TARGET[dst.key]
    for col, val in (overrides or {}).items():
        if val is not None:
            payload[col] = val
    return filter_to_real_columns(payload, allowed, tabla=dst.table)


def build_target_line(
    source_line: dict[str, Any],
    *,
    src: DocSpec,
    dst: DocSpec,
    serie: int,
    codigo: str,
    posicion: int,
    origin_code: str,
    origin_tip: int,
    origin_cod: int,
    allowed: frozenset[str],
) -> dict[str, Any]:
    """Línea destino: copia por sufijo + clave compuesta `(TIP, COD, POS)` +
    el ENLACE al origen (`DOC/DTP/DCO`) — que es donde vive la trazabilidad
    de la cadena (las cabeceras no la llevan). Las líneas de texto libre
    (`ART*=''`) pasan tal cual."""
    payload: dict[str, Any] = {}
    for col, val in source_line.items():
        if not col.endswith(src.lines_suffix):
            continue
        prefix = col[: -len(src.lines_suffix)]
        if any(prefix.startswith(p) for p in EXCLUDED_SOURCE_PREFIXES):
            continue
        payload[_retag(col, src.lines_suffix, dst.lines_suffix)] = val
    suffix = dst.lines_suffix
    payload[dst.line_tip] = str(serie)
    payload[dst.line_fk] = int(codigo)   # entero, como CODLAL en la fila real
    payload[f"POS{suffix}"] = posicion
    payload[f"DOC{suffix}"] = origin_code
    payload[f"DTP{suffix}"] = str(origin_tip)
    payload[f"DCO{suffix}"] = origin_cod
    return filter_to_real_columns(payload, allowed, tabla=dst.lines_table)


def convert_document(
    session: Session,
    client: FactusolClient,
    *,
    source_type: str,
    target_type: str,
    tip: int,
    cod: int,
    ejercicio: str,
    serie_override: int | None = None,
    fecha: str | None = None,
    force: bool = False,
    header_overrides: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Crea el documento DESTINO a partir del ORIGEN, enlazado por línea.

    Fase 2: (a) guardarraíl web para `pedidos` (no lo salta `force`); (b) guard
    de esquema del dry-run ANTES de escribir — si las columnas vivas o los
    tipos de la fila real no cuadran, no se escribe nada (estricto para
    albaranes, aviso para facturas); (c) el registro EXACTO de cabecera y
    líneas va al log; (d) `header_overrides` (forma de pago elegida) pisa la
    cabecera; (e) al crear una FACTURA se vincula al pedido de BoHub que haya
    detrás y se registra su cobro apuntado (`on_invoice_created`).

    Atómico con compensación: si falla una línea se borra lo escrito
    filtrando por la clave COMPUESTA `(TIP, COD)` — borrar por número a
    secas se llevaría el documento homónimo de otra serie (lección E2).

    Corre SIEMPRE dentro del worker `factusol:writes` (concurrency 1): el
    contador MAX+1 se lee justo antes de escribir."""
    if target_type not in ALLOWED_CONVERSIONS.get(source_type, ()):
        raise FactusolError(
            f"Conversión no soportada: {source_type} → {target_type}."
        )
    src = DOC_SPECS[source_type]
    dst = DOC_SPECS[target_type]
    origin_code = ORIGIN_CODES[source_type]

    header, lines = load_source_document(
        client, src, tip=tip, cod=cod, ejercicio=ejercicio,
    )

    # Guardarraíl web (Fase 2): el albarán de un pedido WEB lo crea WooCommerce
    # (mu-plugin Fase D). BoHub no lo duplica, ni con `force`.
    if source_type == "pedidos":
        from app.erp.factusol_albaran import web_pedido_reason  # noqa: PLC0415

        web_reason = web_pedido_reason(session, header)
        if web_reason:
            raise FactusolError(web_reason)

    # Anti-duplicado (re-chequeado aquí, dentro del worker serial, además del
    # aviso previo del endpoint — cubre la carrera). `force` = el operador vio
    # el aviso y quiere el segundo documento igualmente (p. ej. dos albaranes
    # del mismo presupuesto).
    if not force:
        existing = find_existing_children(
            client, source_type, target_type, tip=tip, cod=cod,
            ejercicio=ejercicio,
        )
        if existing:
            raise FactusolError(
                f"El {SINGULAR[source_type]} {visible_number(tip, cod)} ya "
                f"tiene {SINGULAR[target_type]} {', '.join(existing)} en "
                "FACTUSOL."
            )

    # Serie heredada del origen (E2-fix2: nunca un default fijo); el modal
    # puede forzar otra.
    serie = serie_override if serie_override is not None else (
        serie_of_row(header, src.tip) or tip
    )
    allowed_header = live_columns(client, dst.table, ejercicio=ejercicio)
    allowed_lines = live_columns(client, dst.lines_table, ejercicio=ejercicio)
    codigo = next_doc_code(
        client, dst.table, tip_col=dst.tip, cod_col=dst.cod,
        serie=serie, ejercicio=ejercicio,
    )
    fecha_doc = fecha or datetime.now(UTC).date().isoformat()

    cabecera = build_target_header(
        header, src=src, dst=dst, serie=serie, codigo=codigo,
        fecha=fecha_doc, allowed=allowed_header, overrides=header_overrides,
    )
    lineas = [
        build_target_line(
            row, src=src, dst=dst, serie=serie, codigo=codigo,
            posicion=i + 1, origin_code=origin_code, origin_tip=tip,
            origin_cod=cod, allowed=allowed_lines,
        )
        for i, row in enumerate(lines)
    ]

    # Guard de esquema (el del dry-run de la Fase 2), ANTES de escribir nada:
    # columnas fuera de las vivas o tipos distintos a la fila real → con los
    # albaranes no se escribe y se avisa; con las facturas, aviso en el log.
    problems = schema_problems(
        client, dst=dst, cabecera=cabecera, lineas=lineas, serie=serie,
        ejercicio=ejercicio,
    )
    if problems:
        detail = (
            f"El esquema real de {dst.table}/{dst.lines_table} no cuadra con el "
            f"registro de {SINGULAR[target_type]} {visible_number(serie, codigo)}: "
            + "; ".join(problems)
        )
        if target_type in STRICT_SCHEMA_TARGETS:
            logger.error("factusol.chain: NO se escribe nada — %s", detail)
            raise FactusolError(detail + ". No se ha escrito nada.")
        logger.warning("factusol.chain: %s (se escribe igualmente)", detail)

    # Diagnóstico: el registro EXACTO (nombres, valores y tipos) que va a
    # EscribirRegistro, para contrastarlo con `--alb-row` si DELSOL lo rechaza.
    logger.info(
        "factusol.chain: EscribirRegistro %s %s ejercicio=%s registro=%s",
        dst.table, visible_number(serie, codigo), ejercicio, format_record(cabecera),
    )
    for linea in lineas:
        logger.info(
            "factusol.chain: EscribirRegistro %s %s POS=%s registro=%s",
            dst.lines_table, visible_number(serie, codigo),
            linea.get(f"POS{dst.lines_suffix}"), format_record(linea),
        )

    client.write_record(dst.table, cabecera, ejercicio=ejercicio)
    try:
        for linea in lineas:
            client.write_record(dst.lines_table, linea, ejercicio=ejercicio)
    except FactusolError:
        try:
            client.delete_records(
                dst.lines_table,
                f"{dst.line_tip}='{serie}' AND {dst.line_fk}='{codigo}'",
                ejercicio=ejercicio,
            )
            client.delete_records(
                dst.table,
                f"{dst.tip}='{serie}' AND {dst.cod}='{codigo}'",
                ejercicio=ejercicio,
            )
        except FactusolError:
            logger.warning(
                "factusol.chain: no se pudo limpiar %s %s-%s a medias",
                dst.table, serie, codigo, exc_info=True,
            )
        raise

    numero = visible_number(serie, codigo)

    # E3-B-fix3 — marcar el documento de ORIGEN como consumido (lo que hace
    # el escritorio al convertir): ESTPRE→«Aceptado», ESTALB→«Facturado»,
    # ESTPCL→«Enviado». Va DESPUÉS de escribir el hijo y NUNCA lo pone en
    # riesgo: si falla, el hijo persiste (nada de compensación) y el fallo
    # viaja como AVISO legible en el resultado, no como error del job.
    if source_type == "pedidos" and target_type == "albaranes":
        # El valor de ESTPCL «con albarán / servido» NO está confirmado (solo
        # `estpcl_invoiced` = «Enviado», que es facturado): no se inventa.
        origin_marked, mark_reason = False, (
            "el valor de ESTPCL «con albarán» no está confirmado; el pedido de "
            "cliente se queda sin marcar (se marcará al facturar)"
        )
    else:
        origin_marked, mark_reason = mark_origin_converted(
            client, session, source_type=source_type, serie=tip, codigo=cod,
            ejercicio=ejercicio, current_estado=header.get(src.est),
        )
    origin_mark_warning = None
    if not origin_marked:
        origin_mark_warning = (
            f"El {SINGULAR[target_type]} {numero} se creó, pero el "
            f"{SINGULAR[source_type]} {visible_number(tip, cod)} no quedó "
            f"marcado como convertido"
            + (f": {mark_reason}" if mark_reason else ".")
        )

    # Fase 2 (opción B): si acaba de nacer una FACTURA y detrás hay un pedido
    # de BoHub (por su albarán o por su documento origen), se vincula y se
    # registra el cobro apuntado al convertir. Nunca pone en riesgo la factura.
    order_link: dict[str, Any] | None = None
    if target_type == "facturas":
        try:
            from app.erp.factusol_albaran import on_invoice_created  # noqa: PLC0415

            order_link = on_invoice_created(
                session, client, source_type=source_type, source_serie=tip,
                source_codigo=cod, serie=serie, codigo=int(codigo),
                ejercicio=ejercicio, actor_user_id=actor_user_id,
            )
        except Exception as exc:  # noqa: BLE001 — la factura ya existe
            logger.warning(
                "factusol.chain: factura %s creada, pero no se pudo vincular al "
                "pedido / registrar su cobro: %s", numero, exc, exc_info=True,
            )
            order_link = {"error": str(exc)[:300]}

    log_chain_sync(
        session,
        message=(
            f"{src.table} {tip}-{cod:06d} → {dst.table} {numero} "
            f"({len(lineas)} líneas, enlace DOC='{origin_code}')"
            + ("" if origin_marked else f"; AVISO: {origin_mark_warning}")
        ),
    )
    session.commit()
    logger.info(
        "factusol.chain: creado %s %s desde %s %d-%06d",
        dst.table, numero, src.table, tip, cod,
    )
    return {
        "target_type": target_type,
        "serie": serie,
        "codigo": int(codigo),
        "numero": numero,
        "lines": len(lineas),
        "source": {"doc_type": source_type, "serie": tip, "codigo": cod},
        "origin_marked": origin_marked,
        "origin_mark_warning": origin_mark_warning,
        "order": order_link,
    }


def log_chain_sync(
    session: Session, *, message: str, success: bool = True,
) -> None:
    """SyncLog de la conversión — éxito o fallo (lo consulta la bandeja de
    sincronización). El fallo lo registra el job para que el error no muera
    solo en el log del worker (patrón E2-fix2)."""
    from app.models.crm import (  # noqa: PLC0415
        ExternalSystem,
        SyncLog,
        SyncStatus,
        SyncTrigger,
    )

    now = datetime.now(UTC)
    session.add(SyncLog(
        system=ExternalSystem.FACTUSOL,
        account_id=None,
        operation="factusol_create_document",
        status=(SyncStatus.SUCCESS if success else SyncStatus.FAILED).value,
        started_at=now, finished_at=now,
        records_processed=1 if success else 0,
        triggered_by=SyncTrigger.MANUAL.value,
        message=message,
    ))
