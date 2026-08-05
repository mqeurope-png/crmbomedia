"""Vuelca TODAS las columnas de F_ART para identificar el precio de venta.

Preparatorio/verificación de **C-4-fix2**. El descubrimiento de C-4 solo llegó a
volcar las 15 primeras columnas de F_ART y ahí la única de precio es `PCOART`,
que es **coste**. Este script imprime la lista completa y resalta las candidatas
a precio de venta.

Uso (desde el VPS; dev/CI no alcanzan api.sdelsol.com):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.factusol_discover_article_prices

**Solo LEE** (`CargaTabla`): no escribe nada en FACTUSOL.

El adaptador NO necesita esta salida para funcionar — `detect_price_column`
resuelve la columna en runtime mirando las claves reales de la fila. El script
sirve para **confirmar** cuál eligió y para documentarla en
`docs/erp/factusol-schema.md`.
"""
from __future__ import annotations

import sys

#: Subcadenas que delatan una columna de precio/tarifa/coste.
PRICE_HINTS = ("PVP", "PRE", "PVT", "TAR", "COS", "PCO")

#: Cuántos artículos con precio distinto de cero mostrar.
SAMPLE_ARTICLES = 3


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ejercicio", default=None,
                        help="ejercicio a consultar (default: el de settings)")
    args = parser.parse_args(argv)

    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.quotes import (
        ARTICLE_PRICE_CANDIDATES,
        detect_price_column,
    )

    client = FactusolClient.from_settings()
    ejercicio = args.ejercicio or client.default_ejercicio
    print(f"FACTUSOL — columnas de F_ART (ejercicio {ejercicio})")
    print("Solo lectura: no se escribe nada.\n")

    rows = client.load_table("F_ART", filtro="1=1", ejercicio=ejercicio)
    if not rows:
        print("F_ART devolvió 0 filas. ¿Ejercicio equivocado?")
        return 1

    first = rows[0]
    print(f"Total columnas: {len(first)}\n")
    print("TODAS las columnas:")
    for i, col in enumerate(first.keys()):
        print(f"  {i:3d}. {col}")

    print("\nCandidatas de precio/tarifa/coste (con su valor en la 1ª fila):")
    for col in first:
        if any(h in col.upper() for h in PRICE_HINTS):
            print(f"  {col:<12} = {first.get(col)!r}")

    detected = detect_price_column(first)
    print()
    print("=" * 72)
    if detected:
        print(f"✅ El adaptador usará: {detected}")
    else:
        print("⚠️  NINGUNA de las candidatas existe en esta base:")
        print(f"    {', '.join(ARTICLE_PRICE_CANDIDATES)}")
        print("    → el precio se dejará en blanco y el operador lo teclea.")
        print("    Añade el nombre real a ARTICLE_PRICE_CANDIDATES en quotes.py.")
    print("=" * 72)

    print(f"\nArtículos de muestra con precio no-cero (máx {SAMPLE_ARTICLES}):")
    shown = 0
    for row in rows:
        values = {
            col: row.get(col) for col in row
            if any(h in col.upper() for h in PRICE_HINTS)
            and row.get(col) not in (None, "", 0, 0.0)
        }
        if not values:
            continue
        print(f"  CODART={row.get('CODART')!r} EQUART={row.get('EQUART')!r} "
              f"DESART={str(row.get('DESART') or '')[:40]!r}")
        for col, val in values.items():
            print(f"      {col:<12} = {val!r}")
        shown += 1
        if shown >= SAMPLE_ARTICLES:
            break
    if not shown:
        print("  (ningún artículo tiene precios distintos de cero)")

    print("\nPega esta salida en el PR de C-4-fix2 y en docs/erp/factusol-schema.md.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
