"""ERP-F5 — catálogos de FACTUSOL (solo lectura, con cache) y DÓNDE vive cada uno.

Sondeo en producción (2026-09), para no volver a buscar:

    catálogo            tabla   columnas confirmadas                        filas
    ----------------    -----   -----------------------------------------   -----
    formas de pago      F_FPA   CODFPA, DESFPA, VENFPA (nº vencimientos),      77
                                DIA1FPA…DIA6FPA (días de cada vencimiento),
                                PROFPA, CPAFPA, EFEFPA, REMFPA, TIPFPA,
                                BANFPA, DEWFPA
    agentes comerciales F_AGE   CODAGE, NOMAGE                                 17
    transportistas      F_TRN   CODTRN, NOMTRN                                  7
    almacenes           F_ALM   CODALM, NOMALM (+ dirección)                    1  (GEN = SAT)
    familias            F_FAM   CODFAM, DESFAM                                101

⚠ `F_FOP` EXISTE pero está VACÍA (0 filas). El catálogo de formas de pago se
leyó de ahí desde C-2-fix2 hasta este PR: por eso el detalle de factura decía
«Forma de pago: Código 002» en vez de «Transferencia».

⚠ Contrapartidas de cobro (destino del dinero, `CPACOB`/`CPALCO`): la tabla
NO se ha localizado tras sondear ~44 nombres. Es un catálogo CONFIGURABLE en
`/erp/settings` (`app/erp/contrapartidas.py`). NO seguir buscándola a ciegas.

Gotcha nº1 de DELSOL: filtrar por una columna inexistente devuelve `[]` en
silencio. Aquí se carga siempre con `1=1` y se VERIFICA contra una fila real
que la tabla trae la columna de código antes de fiarse de ella.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300  # 5 min: son catálogos estables


@dataclass(frozen=True)
class CatalogSpec:
    name: str
    table: str
    code_col: str
    #: candidatos de columna «nombre», en orden de preferencia
    name_cols: tuple[str, ...]
    #: columnas extra que se exponen tal cual: {clave_salida: COLUMNA}
    extra: dict[str, str] = field(default_factory=dict)
    description: str = ""


CATALOGS: dict[str, CatalogSpec] = {
    "formas_pago": CatalogSpec(
        "formas_pago", "F_FPA", "CODFPA", ("DESFPA",),
        extra={"vencimientos": "VENFPA"},
        description="Formas de pago (FOPFAC/FOPPRE de los documentos). F_FOP está vacía.",
    ),
    "agentes": CatalogSpec(
        "agentes", "F_AGE", "CODAGE", ("NOMAGE",),
        description="Agentes comerciales (BART, ROSA, BRICE, …).",
    ),
    "transportistas": CatalogSpec(
        "transportistas", "F_TRN", "CODTRN", ("NOMTRN",),
        description="Transportistas (MRW, DSV, DHL, MBE, SELF, TNT, UPS).",
    ),
    "almacenes": CatalogSpec(
        "almacenes", "F_ALM", "CODALM", ("NOMALM", "DESALM"),
        extra={"direccion": "DOMALM", "poblacion": "POBALM", "cp": "CPOALM"},
        description="Almacenes (GEN = SAT, Sant Boi de Llobregat).",
    ),
    "familias": CatalogSpec(
        "familias", "F_FAM", "CODFAM", ("DESFAM", "NOMFAM"),
        description="Familias de artículos.",
    ),
}

#: Días de vencimiento de una forma de pago (F_FPA.DIA1FPA … DIA6FPA).
_FPA_DAY_COLS = tuple(f"DIA{i}FPA" for i in range(1, 7))

_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}


def clear_cache() -> None:
    _CACHE.clear()


def normalize_code(value: Any) -> str:
    """Código comparable: `'002'`, `'2'`, `2`, `2.0` → `'2'`; `'000'` → `'0'`.
    En F_FPA los códigos llevan ceros a la izquierda y en los documentos a
    veces no (`'11'` por `'011'`)."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text.upper()


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _first(row: dict[str, Any], *cols: str) -> Any:
    for c in cols:
        v = row.get(c)
        if v not in (None, ""):
            return v
    return None


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def normalize_row(spec: CatalogSpec, row: dict[str, Any]) -> dict[str, Any] | None:
    codigo = _clean(row.get(spec.code_col))
    if codigo is None:
        return None
    if codigo.endswith(".0") and codigo[:-2].isdigit():
        codigo = codigo[:-2]
    nombre = _clean(_first(row, *spec.name_cols)) or codigo
    item: dict[str, Any] = {"codigo": codigo, "nombre": nombre}
    for key, col in spec.extra.items():
        item[key] = _clean(row.get(col))
    if spec.name == "formas_pago":
        n = _int(row.get("VENFPA")) or 0
        item["vencimientos"] = n
        dias = [_int(row.get(c)) for c in _FPA_DAY_COLS[: max(n, 0)]]
        item["dias"] = [d for d in dias if d is not None]
    return item


def is_cached(name: str, *, ejercicio: str) -> bool:
    cached = _CACHE.get((name, ejercicio))
    return bool(cached and cached[0] > time.time())


def load_catalog(
    client: Any, name: str, *, ejercicio: str, force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Filas normalizadas (`{codigo, nombre, …}`) del catálogo `name`, con
    cache en proceso de 5 min por ejercicio. Verifica contra una fila REAL que
    la tabla trae la columna de código (gotcha nº1); si no, avisa y devuelve
    `[]` sin cachear. Propaga errores de conexión: el caller decide."""
    spec = CATALOGS[name]
    key = (name, ejercicio)
    now = time.time()
    if not force_refresh:
        cached = _CACHE.get(key)
        if cached and cached[0] > now:
            return cached[1]
    rows = client.load_table(spec.table, filtro="1=1", ejercicio=ejercicio)
    if rows and spec.code_col not in rows[0]:
        logger.warning(
            "catálogo %s: la tabla %s no trae la columna %s (columnas: %s); se ignora",
            name, spec.table, spec.code_col, sorted(rows[0].keys())[:12],
        )
        return []
    items = [it for it in (normalize_row(spec, r) for r in rows) if it is not None]
    items.sort(key=lambda it: (normalize_code(it["codigo"]).zfill(6), it["codigo"]))
    if not rows:
        logger.warning("catálogo %s: la tabla %s está vacía (ejercicio %s)", name, spec.table,
                       ejercicio)
    _CACHE[key] = (now + CACHE_TTL_SECONDS, items)
    return items


def names_index(items: list[dict[str, Any]]) -> dict[str, str]:
    """`{codigo → nombre}` indexado por el código tal cual Y normalizado, para
    que `'002'`, `'2'` y `2` resuelvan igual."""
    out: dict[str, str] = {}
    for it in items:
        codigo = str(it.get("codigo") or "").strip()
        nombre = str(it.get("nombre") or "").strip()
        if codigo and nombre:
            out[codigo] = nombre
            out[normalize_code(codigo)] = nombre
    return out


def resolve_name(names: dict[str, str], code: Any) -> str | None:
    raw = str(code).strip() if code is not None else ""
    if not raw:
        return None
    return names.get(raw) or names.get(normalize_code(raw))


def catalog_names(client: Any, name: str, *, ejercicio: str) -> dict[str, str]:
    return names_index(load_catalog(client, name, ejercicio=ejercicio))


def payment_methods(client: Any, *, ejercicio: str) -> list[dict[str, Any]]:
    """Formas de pago desde F_FPA (`CODFPA` → `DESFPA`)."""
    return load_catalog(client, "formas_pago", ejercicio=ejercicio)


def payment_method_names(client: Any, *, ejercicio: str) -> dict[str, str]:
    return catalog_names(client, "formas_pago", ejercicio=ejercicio)


def describe_catalogs() -> list[dict[str, Any]]:
    """Documentación viva: qué catálogo vive en qué tabla."""
    return [
        {
            "name": spec.name,
            "table": spec.table,
            "code_column": spec.code_col,
            "name_columns": list(spec.name_cols),
            "description": spec.description,
        }
        for spec in CATALOGS.values()
    ]
