"""Descubre albaranes (F_ALB), la trazabilidad PRE→ALB→FAC y el estado real
del pipeline de facturación. **Solo lectura**: no escribe nada en FACTUSOL.

Preparatorio de **ERP-E2/E3/E4** y diagnóstico del «la generación de facturas
no funciona» que reporta Bart. Mismo patrón que
`scripts/factusol_discover_quotes.py` (PR #308): CC no alcanza
`api.sdelsol.com`, así que el script lo ejecuta Bart en el VPS y pega la
salida.

Por qué descubrir en vez de asumir: en la API DELSOL **un filtro sobre una
columna inexistente devuelve `[]` sin error** (trampa nº 1 de
`docs/erp/reference_delsol_gotchas.md`) — pero **en `EscribirRegistro` una
columna inexistente revienta el registro ENTERO** (trampa nº 13). O sea: leer
miente en silencio y escribir explota en producción. Nada de nombres deducidos
por convención.

Uso (desde el VPS):

    # 1. Discovery completo (estructura + numeración + sondeo de impresión)
    docker exec crmbo-api-1 python -m scripts.factusol_discover_albaranes

    # 2. Trazabilidad: ANTES, convertir en el FACTUSOL de escritorio una
    #    proforma de prueba → albarán → factura, y pasar su CODPRE:
    docker exec crmbo-api-1 python -m scripts.factusol_discover_albaranes \\
        --trace-codpre 574

    # 3. Diagnóstico del pipeline de facturas (dry-run, no escribe):
    docker exec crmbo-api-1 python -m scripts.factusol_discover_albaranes \\
        --check-invoice-pipeline

    # tablas candidatas extra / otro ejercicio:
    ... --ejercicio 2026 F_LAL F_LIA

Pega las salidas en el PR de ERP-E1 y en
`docs/erp/factusol-albaranes-discovery.md`.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------------------
# Candidatas
# --------------------------------------------------------------------------

#: `F_ALB` (388 filas) y `F_LAL` (1493) YA aparecieron en el discovery de C-4
#: (ver `factusol-schema.md`), pero nadie volcó sus columnas. Se sondean igual
#: junto a las otras candidatas de líneas: una tabla ausente es indistinguible
#: de una vacía (trampa nº 11), así que confirmamos con filas de muestra.
CANDIDATE_TABLES: dict[str, tuple[str, ...]] = {
    "albaranes (cabecera)": ("F_ALB",),
    "albaranes (líneas)": ("F_LAL", "F_LALB", "F_LIA", "F_LPA", "F_LAB"),
    # Control positivo + las otras patas del ciclo comercial.
    "proformas (control)": ("F_PRE", "F_LPS"),
    "facturas (control)": ("F_FAC", "F_LFA"),
    "pedidos cliente (control)": ("F_PCL", "F_LPC"),
}

#: Dónde guarda FACTUSOL los diseños de documento. Ninguna está confirmada —
#: son candidatas a sondear para ERP-E4 (PDFs con los modelos de Bart).
PRINT_MODEL_TABLES: tuple[str, ...] = (
    "F_MOD", "F_PLA", "F_IMP", "F_FOR", "F_DIS", "F_INF", "F_REP", "F_DOC",
)

#: Endpoints de impresión/PDF NO documentados que se sondean a ciegas. Todos
#: son de lectura por naturaleza (generar un PDF no muta datos); aun así el
#: guard `is_safe_probe_path` bloquea cualquier ruta con verbo de escritura.
PRINT_ENDPOINT_CANDIDATES: tuple[str, ...] = (
    "/admin/ImprimirDocumento",
    "/admin/Imprimir",
    "/admin/GenerarPDF",
    "/admin/ObtenerPDF",
    "/admin/PDF",
    "/admin/Informe",
    "/admin/Reporte",
    "/admin/Documento",
)

#: Verbos que delatan una ruta de escritura. Si aparecen, NO se sondea.
UNSAFE_PATH_TOKENS: tuple[str, ...] = (
    "escribir", "actualizar", "borrar", "eliminar", "insertar", "guardar",
    "write", "update", "delete", "insert", "save", "crear",
)

#: Fragmentos que delatan una columna de referencia cruzada entre documentos.
REFERENCE_HINTS: tuple[str, ...] = (
    "PRE", "ALB", "FAC", "PCL", "PED", "ORI", "REF", "DOC", "PAS", "SER",
)

SAMPLE_ROWS = 3
#: Máximo de filas a escanear buscando una referencia (F_ALB tiene ~388).
TRACE_SCAN_LIMIT = 4000


# --------------------------------------------------------------------------
# Helpers puros (testeados en tests/test_factusol_discover_albaranes.py)
# --------------------------------------------------------------------------


def is_safe_probe_path(path: str) -> bool:
    """`True` si la ruta puede sondearse sin riesgo de escribir.

    El sondeo de A4 va a ciegas contra rutas no documentadas; este guard evita
    que una candidata mal escrita (o añadida en el futuro sin pensar) acabe
    llamando a `EscribirRegistro` / `BorrarRegistros`."""
    lowered = path.lower()
    return not any(token in lowered for token in UNSAFE_PATH_TOKENS)


def normalize(value: Any) -> str:
    """Valor de celda → string comparable. `574`, `'574'`, `574.0` y `' 574 '`
    tienen que casar: la API devuelve el mismo dato con tipos distintos según
    la columna."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        text = text[:-2]
    return text


def find_matching_columns(
    row: dict[str, Any], needle: Any
) -> list[tuple[str, Any]]:
    """Columnas de la fila cuyo valor es exactamente `needle`.

    Es el método EMPÍRICO de A2: en vez de adivinar si el campo se llama
    `PREALB`, `ORIALB` o `DOCALB`, Bart convierte una proforma en el escritorio
    y buscamos el CODPRE por TODA la fila del albarán. Lo que aparezca, con su
    nombre de columna, es la referencia real."""
    target = normalize(needle)
    if not target:
        return []
    return [
        (col, val) for col, val in row.items() if normalize(val) == target
    ]


def looks_like_reference(column: str) -> bool:
    """Heurística de nombre para ordenar los hallazgos: una coincidencia en
    `PREALB` pesa más que en `TOTALB` (que puede casar por casualidad si el
    total coincide con el número de documento)."""
    upper = column.upper()
    return any(hint in upper for hint in REFERENCE_HINTS)


def payload_column_diff(
    payload: dict[str, Any], real_columns: list[str]
) -> tuple[list[str], list[str]]:
    """`(desconocidas, no_usadas)` comparando el payload que el CRM enviaría
    con las columnas REALES de la tabla.

    **Este es el corazón del diagnóstico de facturas.** Cualquier columna en
    `desconocidas` hace fallar el `EscribirRegistro` entero con
    `BDEscribirRegistroError` (trampa nº 13) — es decir, basta UNA para que
    «la generación de facturas no funcione», y el error no dice cuál es."""
    real = {col.upper() for col in real_columns}
    sent = {col.upper() for col in payload}
    return sorted(sent - real), sorted(real - sent)


def summarize_numbering(rows: list[dict[str, Any]], pk: str) -> dict[str, Any]:
    """Estadística de la numeración de un documento: min/max/nº de filas y si
    la secuencia tiene huecos. Determina si `next_codalb` puede seguir la misma
    estrategia `MAX+1` que `next_codfac` (cola serializada, concurrency=1)."""
    numbers: list[int] = []
    for row in rows:
        raw = normalize(row.get(pk))
        if raw.lstrip("-").isdigit():
            numbers.append(int(raw))
    if not numbers:
        return {"count": len(rows), "numeric": False}
    numbers.sort()
    return {
        "count": len(rows),
        "numeric": True,
        "min": numbers[0],
        "max": numbers[-1],
        "distintos": len(set(numbers)),
        "huecos": (numbers[-1] - numbers[0] + 1) - len(set(numbers)),
        "ultimos": numbers[-5:],
    }


def distinct_values(
    rows: list[dict[str, Any]], column: str, limit: int = 12
) -> list[str]:
    """Valores distintos de una columna (para cerrar preguntas abiertas tipo
    «¿`ESTALB` es 0/1 o un código?»)."""
    seen: list[str] = []
    for row in rows:
        val = normalize(row.get(column))
        if val not in seen:
            seen.append(val)
        if len(seen) >= limit:
            break
    return seen


# --------------------------------------------------------------------------
# Sondeo de tablas
# --------------------------------------------------------------------------


def probe_table(client: Any, tabla: str, ejercicio: str) -> dict[str, Any]:
    """Lee la tabla sin filtro. No propaga: queremos el motivo del fallo."""
    try:
        rows = client.load_table(tabla, filtro="1=1", ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001
        return {"tabla": tabla, "ok": False, "error": str(exc)[:300]}
    if not rows:
        # Ojo: «vacía en este ejercicio» y «no existe» son indistinguibles.
        return {"tabla": tabla, "ok": True, "rows": 0, "columns": []}
    return {
        "tabla": tabla,
        "ok": True,
        "rows": len(rows),
        "columns": list(rows[0].keys()),
        "sample": rows[:SAMPLE_ROWS],
        "all_rows": rows,
    }


def print_table_result(res: dict[str, Any], *, verbose: bool = True) -> None:
    tabla = res["tabla"]
    if not res["ok"]:
        print(f"  {tabla:<10} ERROR: {res['error']}")
        return
    if res["rows"] == 0:
        print(f"  {tabla:<10} 0 filas (¿vacía en este ejercicio, o no existe?)")
        return
    print(f"  {tabla:<10} ✅ {res['rows']} filas")
    print(f"      columnas ({len(res['columns'])}): {', '.join(res['columns'])}")
    if not verbose:
        return
    for i, row in enumerate(res.get("sample", []), 1):
        preview = {
            k: (v if not isinstance(v, str) or len(v) <= 40 else v[:40] + "…")
            for k, v in list(row.items())[:18]
        }
        print(f"      muestra {i}: {preview}")


# --------------------------------------------------------------------------
# A1 + A3 — estructura y numeración de albaranes
# --------------------------------------------------------------------------


def discover_structure(
    client: Any, ejercicio: str, extra: list[str]
) -> dict[str, dict[str, Any]]:
    print("=" * 74)
    print("A1 · ESTRUCTURA — cabeceras y líneas")
    print("=" * 74)
    groups = dict(CANDIDATE_TABLES)
    if extra:
        groups["extra (CLI)"] = tuple(extra)
    results: dict[str, dict[str, Any]] = {}
    for concepto, tablas in groups.items():
        print(f"\n{concepto}:")
        for tabla in tablas:
            res = probe_table(client, tabla, ejercicio)
            print_table_result(res)
            results[tabla] = res
    return results


def discover_line_link(
    client: Any, ejercicio: str, results: dict[str, dict[str, Any]]
) -> None:
    """Confirma EMPÍRICAMENTE la relación cabecera↔líneas del albarán, como
    `F_LPS.CODLPS → F_PRE.CODPRE` en proformas: coge un CODALB real y busca
    sus líneas por cada candidata de FK."""
    print("\n" + "=" * 74)
    print("A1b · RELACIÓN cabecera ↔ líneas (verificación empírica)")
    print("=" * 74)
    alb = results.get("F_ALB") or {}
    if not alb.get("rows"):
        print("  F_ALB sin filas: no se puede verificar.")
        return
    header = alb["sample"][0]
    pk_col = next(
        (c for c in alb["columns"] if c.upper().startswith("COD")), None
    )
    codalb = header.get(pk_col) if pk_col else None
    print(f"  Albarán de prueba: {pk_col}={codalb!r}")
    line_tables = [
        t for t in ("F_LAL", "F_LALB", "F_LIA", "F_LPA", "F_LAB")
        if (results.get(t) or {}).get("rows")
    ]
    if not line_tables:
        print("  Ninguna candidata de líneas devolvió filas.")
        return
    for tabla in line_tables:
        cols = results[tabla]["columns"]
        fks = [c for c in cols if c.upper().startswith("COD")]
        print(f"\n  {tabla} — candidatas a FK: {', '.join(fks) or '(ninguna)'}")
        for fk in fks:
            try:
                rows = client.load_table(
                    tabla, filtro=f"{fk}={codalb}", ejercicio=ejercicio
                )
            except Exception as exc:  # noqa: BLE001
                print(f"      {fk}={codalb} → ERROR {str(exc)[:120]}")
                continue
            verdict = "✅ ES LA FK" if rows else "no casa (o filtro inválido)"
            print(f"      {fk}={codalb} → {len(rows)} líneas · {verdict}")
            if rows:
                print(f"         muestra: {dict(list(rows[0].items())[:12])}")


def discover_numbering(results: dict[str, dict[str, Any]]) -> None:
    print("\n" + "=" * 74)
    print("A3 · NUMERACIÓN de albaranes (¿MAX+1 como CODFAC?)")
    print("=" * 74)
    alb = results.get("F_ALB") or {}
    if not alb.get("rows"):
        print("  F_ALB sin filas.")
        return
    cols = alb["columns"]
    rows = alb.get("all_rows") or []
    pk_col = next((c for c in cols if c.upper().startswith("COD")), None)
    if pk_col:
        stats = summarize_numbering(rows, pk_col)
        print(f"  {pk_col}: {stats}")
        if stats.get("numeric"):
            print(
                f"  → next_codalb = MAX+1 = {stats['max'] + 1} "
                "(misma estrategia que next_codfac, cola serializada)"
            )
    # Serie / tipo / estado: columnas que condicionan la numeración.
    for hint in ("SER", "TIP", "EST", "EJE"):
        for col in cols:
            if col.upper().startswith(hint):
                print(f"  {col}: valores distintos → {distinct_values(rows, col)}")


# --------------------------------------------------------------------------
# A2 — trazabilidad PRE → ALB → FAC
# --------------------------------------------------------------------------


def trace_chain(client: Any, ejercicio: str, codpre: str) -> None:
    """Sigue la cadena de una proforma concreta que Bart acaba de convertir en
    el escritorio. Busca el código por TODAS las columnas en vez de adivinar
    nombres (`PREALB`, `ORIALB`, …)."""
    print("=" * 74)
    print(f"A2 · TRAZABILIDAD de la proforma CODPRE={codpre}")
    print("=" * 74)
    print(
        "  Requisito: la proforma tiene que estar YA convertida en el FACTUSOL\n"
        "  de escritorio a albarán y a factura. Si no, esto no encuentra nada."
    )

    pre_rows = client.load_table(
        "F_PRE", filtro=f"CODPRE={codpre}", ejercicio=ejercicio
    )
    if not pre_rows:
        print(f"\n  ❌ No existe F_PRE con CODPRE={codpre} en {ejercicio}.")
        return
    pre = pre_rows[0]
    print(f"\n  F_PRE completo ({len(pre)} columnas):")
    for col, val in pre.items():
        if normalize(val):
            print(f"      {col:<12} = {val!r}")
    print(
        "\n  → Compara ESTPRE con el valor previo a la conversión: si cambió, "
        "\n    ahí está el marcador de «proforma ya convertida» que hoy falta "
        "\n    (gotcha nº 17)."
    )

    # PRE → ALB
    codalb = _scan_for_reference(
        client, ejercicio, tabla="F_ALB", needle=codpre,
        origen=f"CODPRE={codpre}",
    )
    if codalb is None:
        print(
            "\n  Sin coincidencias en F_ALB. Puede que FACTUSOL no guarde la\n"
            "  referencia al origen (la conversión copiaría los datos sin\n"
            "  enlazar) — en ese caso ERP-E2 tendrá que mantener el vínculo en\n"
            "  el CRM, no en FACTUSOL."
        )
        return

    # ALB → FAC
    _scan_for_reference(
        client, ejercicio, tabla="F_FAC", needle=codalb,
        origen=f"CODALB={codalb}",
    )


def trace_order(client: Any, ejercicio: str, ref: str) -> None:
    """ERP-E2-fix1 — cómo se codifica la SERIE y dónde vive su contador.

    El escritorio muestra los documentos como `<serie>-<número>`: el pedido
    `BOP-099917` es el `5-000005` y hay facturas `2-526082`. Bajo el modelo de
    ERP-E2 (serie = rango del número) ninguna de las dos cuadra, así que la
    serie tiene que ser una COLUMNA. La hipótesis es `TIPPCL` / `TIPFAC`; esto
    lo confirma o lo tumba con filas reales."""
    print("=" * 74)
    print(f"A1/A2/A3 · SERIE y CONTADOR — pedido {ref}")
    print("=" * 74)

    pcl_rows = client.load_table(
        "F_PCL", filtro=f"REFPCL='{ref}'", ejercicio=ejercicio
    )
    if not pcl_rows:
        print(f"\n  ❌ No hay F_PCL con REFPCL='{ref}' en {ejercicio}.")
        print("     Comprueba la referencia (formato BOP-099917).")
        return
    pcl = pcl_rows[0]
    print(f"\n  F_PCL completo de {ref} ({len(pcl)} columnas, no vacías):")
    for col, val in pcl.items():
        if normalize(val):
            print(f"      {col:<12} = {val!r}")
    tip = normalize(pcl.get("TIPPCL"))
    cod = normalize(pcl.get("CODPCL"))
    print(
        f"\n  → El escritorio lo muestra como «{tip}-{int(cod):06d}»?"
        if cod.isdigit() else f"\n  → TIPPCL={tip!r} CODPCL={cod!r}"
    )
    print(
        "     Si TIPPCL coincide con la serie del display, LA SERIE ES EL\n"
        "     «TIPO» y el número es correlativo por serie."
    )

    # A4 — ¿qué valor de ESTPCL marca «facturado»?
    print("\n  A4 · ESTPCL — reparto de estados en los pedidos del ejercicio:")
    todos = client.load_table("F_PCL", filtro="1=1", ejercicio=ejercicio)
    conteo: dict[str, int] = {}
    for row in todos:
        key = normalize(row.get("ESTPCL"))
        conteo[key] = conteo.get(key, 0) + 1
    for estado, n in sorted(conteo.items(), key=lambda kv: -kv[1]):
        marca = "  ← el de ESTE pedido" if estado == normalize(
            pcl.get("ESTPCL")
        ) else ""
        print(f"      ESTPCL={estado!r:<8} {n:>5} pedidos{marca}")
    print(
        "     Abre en el escritorio un pedido «Enviado» (facturado) y otro\n"
        "     «Pendiente» y localiza sus ESTPCL arriba: el del «Enviado» es el\n"
        "     valor que hay que poner en /erp/settings → estpcl_invoiced."
    )

    # A2 — contador por serie en F_FAC y F_PCL.
    for tabla, tipo_col, cod_col in (
        ("F_FAC", "TIPFAC", "CODFAC"), ("F_PCL", "TIPPCL", "CODPCL"),
    ):
        print(f"\n  A2 · Contador por serie en {tabla}:")
        try:
            rows = client.load_table(tabla, filtro="1=1", ejercicio=ejercicio)
        except Exception as exc:  # noqa: BLE001
            print(f"      ERROR: {str(exc)[:200]}")
            continue
        por_serie: dict[str, list[int]] = {}
        for row in rows:
            serie = normalize(row.get(tipo_col))
            num = normalize(row.get(cod_col))
            if num.lstrip("-").isdigit():
                por_serie.setdefault(serie, []).append(int(num))
        if not por_serie:
            print(f"      {tabla} sin filas numéricas.")
            continue
        for serie, nums in sorted(por_serie.items()):
            print(
                f"      {tipo_col}={serie!r:<6} {len(nums):>5} docs · "
                f"min={min(nums)} max={max(nums)} → siguiente={max(nums) + 1}"
            )
        print(
            "      Si cada serie tiene su propio rango correlativo, el\n"
            "      contador es MAX(código de esa serie)+1 y no hace falta\n"
            "      tabla de contadores."
        )

    # ¿Existe una tabla de contadores aparte?
    print("\n  A2b · ¿Tabla de contadores / configuración de empresa?")
    for tabla in ("F_CON", "F_CONTA", "F_EMP", "F_SER", "F_NUM", "F_PAR"):
        res = probe_table(client, tabla, ejercicio)
        print_table_result(res, verbose=res.get("rows", 0) < 20)

    # A3 — ¿tiene ya albarán o factura este pedido?
    print(f"\n  A3 · ¿El pedido {cod} ya tiene albarán/factura?")
    for tabla, campo in (("F_ALB", "PEDALB"), ("F_FAC", "PEDFAC")):
        try:
            rows = client.load_table(tabla, filtro="1=1", ejercicio=ejercicio)
        except Exception as exc:  # noqa: BLE001
            print(f"      {tabla}: ERROR {str(exc)[:120]}")
            continue
        hits = [r for r in rows if normalize(r.get(campo)) == cod and cod]
        if hits:
            for row in hits:
                print(f"      ✅ {tabla}.{campo}={cod} → {dict(list(row.items())[:8])}")
        else:
            print(f"      {tabla}.{campo}: sin coincidencias con CODPCL={cod}")
    print(
        "\n  (Si PEDFAC está vacío en las facturas del escritorio, FACTUSOL no\n"
        "   enlaza factura→pedido por ahí y la trazabilidad tendrá que vivir\n"
        "   en el CRM.)"
    )


def _scan_for_reference(
    client: Any, ejercicio: str, *, tabla: str, needle: Any, origen: str
) -> str | None:
    """Escanea `tabla` buscando `needle` en cualquier columna. Devuelve el PK
    de la primera fila que casa en una columna con pinta de referencia."""
    print(f"\n  Buscando {origen} dentro de {tabla}…")
    try:
        rows = client.load_table(tabla, filtro="1=1", ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001
        print(f"      ERROR leyendo {tabla}: {str(exc)[:200]}")
        return None
    if not rows:
        print(f"      {tabla} sin filas en {ejercicio}.")
        return None

    pk_col = next(
        (c for c in rows[0] if c.upper().startswith("COD")), None
    )
    hits: list[tuple[str, str, Any]] = []  # (pk, columna, valor)
    for row in rows[:TRACE_SCAN_LIMIT]:
        for col, val in find_matching_columns(row, needle):
            if pk_col and col == pk_col:
                continue  # su propio PK no es una referencia
            hits.append((normalize(row.get(pk_col)), col, val))
    if not hits:
        print(f"      Sin coincidencias en {len(rows)} filas de {tabla}.")
        return None

    strong = [h for h in hits if looks_like_reference(h[1])]
    weak = [h for h in hits if not looks_like_reference(h[1])]
    print(f"      {len(hits)} coincidencia(s) en {len(rows)} filas:")
    for pk, col, val in strong:
        print(f"        ⭐ {tabla}.{col} = {val!r}  (fila {pk_col}={pk})")
    for pk, col, val in weak[:10]:
        print(f"           {tabla}.{col} = {val!r}  (fila {pk_col}={pk}) "
              "— nombre sin pinta de referencia, probablemente casualidad")
    if strong:
        print(
            f"      → CANDIDATA A REFERENCIA: {tabla}.{strong[0][1]} "
            f"apunta al documento origen."
        )
        return strong[0][0]
    return weak[0][0] if weak else None


# --------------------------------------------------------------------------
# A4 — sondeo de impresión
# --------------------------------------------------------------------------


def probe_print_endpoints(client: Any, ejercicio: str) -> None:
    print("\n" + "=" * 74)
    print("A4 · SONDEO de endpoints de impresión/PDF (no documentados)")
    print("=" * 74)
    print("  Un 404 = no existe. Un 400/500 con mensaje = existe y le falta algo.")
    # El token se reutiliza; `_raw_request` no lanza, devuelve la Response.
    client._ensure_token()  # noqa: SLF001 — script de discovery
    body = {"ejercicio": ejercicio, "tipo": "FAC", "codigo": 1, "serie": ""}
    for path in PRINT_ENDPOINT_CANDIDATES:
        if not is_safe_probe_path(path):
            print(f"  {path:<28} SALTADA (ruta con verbo de escritura)")
            continue
        for method in ("GET", "POST"):
            try:
                resp = client._raw_request(  # noqa: SLF001
                    method, path, json=(body if method == "POST" else None),
                )
                snippet = (resp.text or "")[:160].replace("\n", " ")
                print(f"  {method:<4} {path:<28} → {resp.status_code} {snippet}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {method:<4} {path:<28} → EXCEPCIÓN {str(exc)[:120]}")


def probe_print_tables(client: Any, ejercicio: str) -> None:
    print("\n" + "=" * 74)
    print("A4b · TABLAS de modelos de impresión (¿dónde viven los diseños?)")
    print("=" * 74)
    for tabla in PRINT_MODEL_TABLES:
        res = probe_table(client, tabla, ejercicio)
        print_table_result(res, verbose=False)


# --------------------------------------------------------------------------
# A5 — diagnóstico del pipeline de facturación
# --------------------------------------------------------------------------


def check_invoice_pipeline(client: Any, ejercicio: str) -> None:
    """Verifica el pipeline de emisión SIN escribir: token, ajustes, columnas
    reales de F_FAC/F_LFA y **diff contra el payload que enviaría el mapper**."""
    print("=" * 74)
    print("A5 · DIAGNÓSTICO del pipeline de facturación (dry-run, no escribe)")
    print("=" * 74)

    # 1. Token
    print("\n1) Autenticación")
    try:
        client.authenticate()
        claims = client.token_claims()
        print(f"   ✅ login OK · token válido {client.token_valid_seconds()}s")
        print(f"   claims: { {k: claims[k] for k in list(claims)[:6]} }")
    except Exception as exc:  # noqa: BLE001
        print(f"   ❌ LOGIN FALLA: {str(exc)[:300]}")
        print("   → Causa #1 candidata del bug. Revisa FACTUSOL_PASSWORD_ENCRYPTED.")
        return

    # 2. Ajustes del CRM
    print("\n2) Ajustes del ERP en la BD del CRM")
    try:
        from sqlalchemy.orm import Session as _Session  # noqa: PLC0415

        from app.db.session import get_engine  # noqa: PLC0415
        from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings  # noqa: PLC0415

        with _Session(get_engine()) as session:
            cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
            if cfg is None:
                print("   ⚠️  No hay fila ErpSettings — se usan los defaults.")
            else:
                print(f"   factusol_live           = {cfg.factusol_live}")
                print(f"   factusol_default_ejercicio = "
                      f"{cfg.factusol_default_ejercicio!r}")
                print(f"   factusol_series_json    = {cfg.factusol_series_json!r}")
                if not cfg.factusol_live:
                    print("   ❌ factusol_live APAGADO → la UI ni siquiera "
                          "consulta FACTUSOL. Causa candidata del bug.")
    except Exception as exc:  # noqa: BLE001
        print(f"   ⚠️  No se pudieron leer los ajustes: {str(exc)[:200]}")

    # 3. Lectura de F_FAC
    print(f"\n3) Lectura de F_FAC (ejercicio {ejercicio})")
    fac = probe_table(client, "F_FAC", ejercicio)
    print_table_result(fac, verbose=False)
    if not fac.get("rows"):
        print("   ❌ Sin filas: ejercicio equivocado o base sin datos.")
        return
    last = summarize_numbering(fac.get("all_rows") or [], "CODFAC")
    print(f"   numeración CODFAC: {last}")

    lfa = probe_table(client, "F_LFA", ejercicio)
    print_table_result(lfa, verbose=False)

    # 4. EL CHEQUEO CLAVE — columnas que el CRM enviaría vs columnas reales
    print("\n4) Payload del mapper vs columnas REALES (dry-run, sin escribir)")
    pcl = probe_table(client, "F_PCL", ejercicio)
    if not pcl.get("rows"):
        print("   ⚠️  Sin F_PCL de muestra: no se puede construir el payload.")
        return
    pcl_row = pcl["sample"][0]
    codpcl = pcl_row.get("CODPCL")
    try:
        lpc_rows = client.load_table(
            "F_LPC", filtro=f"CODLPC={codpcl}", ejercicio=ejercicio
        )
    except Exception:  # noqa: BLE001
        lpc_rows = []

    from app.integrations.factusol.mapper import (  # noqa: PLC0415
        FacturaOptions,
        lpc_row_to_lfa_payload,
        pcl_row_to_fac_payload,
    )

    cabecera = pcl_row_to_fac_payload(
        pcl_row,
        codfac="999999",
        ejercicio=ejercicio,
        fecha_emision=datetime.now(UTC).date().isoformat(),
        options=FacturaOptions(serfac="A"),
    )
    unknown, unused = payload_column_diff(cabecera, fac["columns"])
    print(f"   F_PCL de muestra: CODPCL={codpcl} · {len(lpc_rows)} líneas")
    print(f"   cabecera: {len(cabecera)} columnas enviadas")
    if unknown:
        print(f"   ❌ COLUMNAS QUE NO EXISTEN EN F_FAC: {', '.join(unknown)}")
        print("      Cada una de estas hace fallar el EscribirRegistro ENTERO")
        print("      con BDEscribirRegistroError (gotcha nº 13). ES EL BUG.")
    else:
        print("   ✅ Todas las columnas de la cabecera existen en F_FAC.")
    print(f"   (informativo) columnas de F_FAC que no enviamos: {len(unused)}")

    if lpc_rows:
        linea = lpc_row_to_lfa_payload(lpc_rows[0], "999999", 1, ejercicio)
        l_unknown, _ = payload_column_diff(linea, lfa.get("columns") or [])
        if l_unknown:
            print(f"   ❌ COLUMNAS QUE NO EXISTEN EN F_LFA: {', '.join(l_unknown)}")
        else:
            print("   ✅ Todas las columnas de la línea existen en F_LFA.")

    print("\n5) Logs del worker (ejecútalo aparte y pega la salida):")
    print("   docker logs crmbo-worker-factusol-1 --since 240h 2>&1 \\")
    print("     | grep -iE '(error|fail|exception|KO)' | tail -50")


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extra", nargs="*", help="tablas candidatas adicionales")
    parser.add_argument("--ejercicio", default=None,
                        help="ejercicio a consultar (default: el de settings)")
    parser.add_argument("--trace-codpre", default=None,
                        help="CODPRE de la proforma convertida a mano en el "
                             "escritorio, para seguir la cadena PRE→ALB→FAC")
    parser.add_argument("--trace-order", default=None,
                        help="REFPCL del pedido (ej. BOP-099917): descubre "
                             "cómo se codifica la serie, el contador por "
                             "serie y el ESTPCL que marca facturado")
    parser.add_argument("--check-invoice-pipeline", action="store_true",
                        help="diagnóstico del bug de emisión de facturas")
    parser.add_argument("--skip-print-probe", action="store_true",
                        help="omite el sondeo A4 de endpoints de impresión")
    args = parser.parse_args(argv)

    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415

    client = FactusolClient.from_settings()
    ejercicio = args.ejercicio or client.default_ejercicio
    print(f"FACTUSOL — discovery de albaranes (ERP-E1) · ejercicio {ejercicio}")
    print("SOLO LECTURA: este script no escribe nada en FACTUSOL.\n")

    if args.check_invoice_pipeline:
        check_invoice_pipeline(client, ejercicio)
        return 0

    if args.trace_order:
        trace_order(client, ejercicio, args.trace_order)
        return 0

    if args.trace_codpre:
        trace_chain(client, ejercicio, args.trace_codpre)
        return 0

    results = discover_structure(client, ejercicio, args.extra)
    discover_line_link(client, ejercicio, results)
    discover_numbering(results)
    if not args.skip_print_probe:
        probe_print_endpoints(client, ejercicio)
        probe_print_tables(client, ejercicio)

    print("\n" + "=" * 74)
    print("SIGUIENTE PASO")
    print("=" * 74)
    print("1. En el FACTUSOL de escritorio: convierte una proforma de prueba")
    print("   → albarán → factura. Anota su CODPRE.")
    print("2. python -m scripts.factusol_discover_albaranes --trace-codpre <N>")
    print("3. python -m scripts.factusol_discover_albaranes --check-invoice-pipeline")
    print("4. Pega las 3 salidas en el PR de ERP-E1.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
