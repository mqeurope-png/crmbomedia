"""Descubre los nombres REALES de las tablas/columnas de proformas y artículos.

Preparatorio de **C-4 (proformas)**. La API DELSOL tiene una trampa que ya nos
costó un PR entero (C-3-fix1): **un filtro sobre una columna inexistente NO da
error, devuelve `[]`**. Así que antes de escribir código contra F_PRO/F_LPP/F_ART
hay que ver los nombres reales — y más aún aquí, porque C-4 **escribe**
proformas en la contabilidad real.

Uso (desde el VPS; dev/CI no alcanzan api.sdelsol.com):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.factusol_discover_quotes

    # con otro ejercicio, o probando tablas candidatas extra:
    ... python -m scripts.factusol_discover_quotes --ejercicio 2026 F_PRE F_LPRE

**Solo LEE** (`CargaTabla`): no escribe nada en FACTUSOL.

Al terminar imprime, por tabla encontrada: nº de filas, la lista COMPLETA de
columnas y un registro de muestra. Pega esa salida en el PR de C-4 y en
`docs/erp/factusol-schema.md`.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

# Candidatas por concepto. FACTUSOL de escritorio usa F_PRO (presupuestos) con
# líneas F_LPP, pero la base cloud puede diferir: se prueban varias.
CANDIDATE_TABLES: dict[str, tuple[str, ...]] = {
    "proformas (cabecera)": ("F_PRO", "F_PRE", "F_PPR", "F_PVT", "F_PPV"),
    "proformas (líneas)": ("F_LPP", "F_LPR", "F_LPRE", "F_LPPR", "F_LPRO"),
    "artículos": ("F_ART", "F_ARTICULOS"),
    # Útiles de contexto: ya conocidas, sirven de control positivo.
    "clientes (control)": ("F_CLI",),
}

#: Cuántas filas de muestra imprimir por tabla.
SAMPLE_ROWS = 2


def _probe(client: Any, tabla: str, ejercicio: str) -> dict[str, Any]:
    """Lee la tabla sin filtro. Devuelve un dict con el resultado del sondeo."""
    try:
        rows = client.load_table(tabla, filtro="1=1", ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001 — queremos el motivo, no propagar
        return {"tabla": tabla, "ok": False, "error": str(exc)[:300]}
    if not rows:
        # OJO: puede significar «tabla vacía en este ejercicio» O «tabla que no
        # existe» — la API no distingue. Por eso se prueban varias candidatas.
        return {"tabla": tabla, "ok": True, "rows": 0, "columns": []}
    return {
        "tabla": tabla, "ok": True, "rows": len(rows),
        "columns": list(rows[0].keys()),
        "sample": rows[:SAMPLE_ROWS],
    }


def _print_result(res: dict[str, Any]) -> None:
    tabla = res["tabla"]
    if not res["ok"]:
        print(f"  {tabla:<12} ERROR: {res['error']}")
        return
    if res["rows"] == 0:
        print(f"  {tabla:<12} 0 filas (¿tabla vacía en este ejercicio, o no existe?)")
        return
    print(f"  {tabla:<12} ✅ {res['rows']} filas")
    print(f"      columnas ({len(res['columns'])}): {', '.join(res['columns'])}")
    for i, row in enumerate(res.get("sample", []), 1):
        # Recorta los valores largos para que la salida siga siendo legible.
        preview = {
            k: (v if not isinstance(v, str) or len(v) <= 40 else v[:40] + "…")
            for k, v in list(row.items())[:15]
        }
        print(f"      muestra {i}: {preview}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extra", nargs="*", help="tablas candidatas adicionales")
    parser.add_argument("--ejercicio", default=None,
                        help="ejercicio a consultar (default: el de settings)")
    args = parser.parse_args(argv)

    from app.integrations.factusol.client import FactusolClient

    client = FactusolClient.from_settings()
    ejercicio = args.ejercicio or client.default_ejercicio
    print(f"FACTUSOL — descubrimiento de proformas/artículos (ejercicio {ejercicio})")
    print("Solo lectura: no se escribe nada.\n")

    groups = dict(CANDIDATE_TABLES)
    if args.extra:
        groups["extra (CLI)"] = tuple(args.extra)

    found: dict[str, list[str]] = {}
    for concepto, tablas in groups.items():
        print(f"{concepto}:")
        for tabla in tablas:
            res = _probe(client, tabla, ejercicio)
            _print_result(res)
            if res.get("ok") and res.get("rows"):
                found.setdefault(concepto, []).append(tabla)
        print()

    print("=" * 72)
    if found:
        print("Tablas con datos (usa estos nombres en el código de C-4):")
        for concepto, tablas in found.items():
            print(f"  {concepto}: {', '.join(tablas)}")
    else:
        print("Ninguna candidata devolvió filas. Prueba otros nombres:")
        print("  python -m scripts.factusol_discover_quotes NOMBRE1 NOMBRE2")
    print()
    print("Pega esta salida en el PR de C-4 y en docs/erp/factusol-schema.md.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
