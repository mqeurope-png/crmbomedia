"""Verifica las tablas F_LTA (tarifas) y F_LPS (líneas de presupuesto).

Estas dos tablas se descubrieron en vivo el 2026-08-05 (C-4-fix3) y corrigen dos
conclusiones equivocadas de C-4:

- El **precio de venta** no está en F_ART (`PCOART` es el coste): está en
  `F_LTA`, con `ARTLTA` → `F_ART.CODART` y `PRELTA` = precio. Bomedia usa
  `TARLTA=1`.
- `F_PRE` **sí tiene líneas**: en `F_LPS`, con `CODLPS` → `F_PRE.CODPRE`. C-4 la
  buscó como `F_LPRE`/`F_LPR`/`F_LPP` y concluyó que no existía.

Uso (desde el VPS; dev/CI no alcanzan api.sdelsol.com):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.factusol_discover_article_prices

    # comprobando un artículo y un presupuesto concretos:
    ... python -m scripts.factusol_discover_article_prices --articulo 99cy --presupuesto 574

**Solo LEE** (`CargaTabla`): no escribe nada en FACTUSOL.
"""
from __future__ import annotations

import sys

#: Muestras a imprimir por tabla.
SAMPLE_ROWS = 5


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ejercicio", default=None,
                        help="ejercicio a consultar (default: el de settings)")
    parser.add_argument("--articulo", default="99cy",
                        help="CODART a comprobar en F_LTA (default: 99cy → 80,00)")
    parser.add_argument("--presupuesto", default="574",
                        help="CODPRE a comprobar en F_LPS (default: 574 → 4 líneas)")
    args = parser.parse_args(argv)

    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.quotes import (
        DEFAULT_TARIFA,
        TABLE_QUOTE_LINES,
        TABLE_TARIFFS,
        list_quote_lines,
        tariff_prices,
    )

    client = FactusolClient.from_settings()
    ejercicio = args.ejercicio or client.default_ejercicio
    print(f"FACTUSOL — verificación F_LTA / F_LPS (ejercicio {ejercicio})")
    print("Solo lectura: no se escribe nada.\n")

    # --- F_LTA -------------------------------------------------------------
    print(f"{TABLE_TARIFFS} (tarifas por artículo)")
    rows = client.load_table(TABLE_TARIFFS, filtro="1=1", ejercicio=ejercicio)
    if not rows:
        print("  ⚠️  0 filas. ¿Ejercicio equivocado o tabla vacía?")
    else:
        print(f"  {len(rows)} filas · columnas: {', '.join(rows[0].keys())}")
        for row in rows[:SAMPLE_ROWS]:
            print(f"    {row}")

    art = args.articulo
    prices = tariff_prices(client, [art], ejercicio=ejercicio, tarifa=DEFAULT_TARIFA)
    print(f"\n  Precio de {art!r} en tarifa {DEFAULT_TARIFA}: "
          f"{prices.get(art, '— (sin tarifa configurada)')}")

    # --- F_LPS -------------------------------------------------------------
    print(f"\n{TABLE_QUOTE_LINES} (líneas de presupuesto)")
    rows = client.load_table(TABLE_QUOTE_LINES, filtro="1=1", ejercicio=ejercicio)
    if not rows:
        print("  ⚠️  0 filas. ¿Ejercicio equivocado o tabla vacía?")
    else:
        print(f"  {len(rows)} filas · columnas: {', '.join(rows[0].keys())}")

    codpre = args.presupuesto
    lines = list_quote_lines(client, codpre, ejercicio=ejercicio)
    print(f"\n  Presupuesto {codpre} → {len(lines)} líneas:")
    total = 0.0
    for line in lines:
        total += line["line_total"]
        print(f"    {line['position']:>3}. {line['codart'] or '—':<12} "
              f"{line['description'][:40]:<40} "
              f"{line['quantity']:>6g} × {line['unit_price']:>8.2f} "
              f"= {line['line_total']:>9.2f}")
    if lines:
        print(f"    {'':>3}  {'':<12} {'BASE':<40} {'':>17} {total:>9.2f}")
        print("\n  Compara esta base con el NET1PRE de la cabecera F_PRE: si "
              "cuadran, el vínculo CODLPS = CODPRE es correcto.")

    print("\nPega esta salida en docs/erp/factusol-schema.md si algo no cuadra.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
