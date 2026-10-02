"""Mide cuánto texto admite de verdad una línea de presupuesto en FACTUSOL.

Contexto (lote Proformas · punto D): BoHub rechazaba descripciones de más de
255 caracteres («lines.3.description: String should have at most 255
characters») y, peor, recortaba a 255 en silencio al escribir `F_LPS.DESLPS`.
Ese límite era de BoHub; el de FACTUSOL no está verificado. Este script lo
mide contra la base real:

1. Las líneas de una proforma concreta (por defecto la **5-004360**, que tiene
   una descripción larga): longitud de `DESLPS` y de `MEMLPS` (el memo de la
   línea), artículo, cantidad y precio de cada fila. Si el texto largo está en
   una sola fila de más de 255 caracteres → la columna admite más y se sube
   `DESLPS_MAX_LENGTH` en `app/integrations/factusol/quotes.py`. Si está
   repartido en filas sin artículo con cantidad 0 → son líneas de
   continuación, que es lo que BoHub hace ahora. Si está en `MEMLPS` → hay que
   escribir el memo.
2. Estadística de TODO `F_LPS` del ejercicio: máximo de `DESLPS`, cuántas
   pasan de 255 / 100, cuántas usan `MEMLPS`, cuántas líneas sin artículo (y
   de ellas con cantidad 0) y las 5 descripciones más largas.

Uso (desde el VPS; dev/CI no alcanzan api.sdelsol.com):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.factusol_discover_line_lengths

    # otra proforma u otros ejercicios:
    ... python -m scripts.factusol_discover_line_lengths --codpre 4360 --ejercicio 2026 2025

**Solo LEE** (`CargaTabla`): no escribe nada en FACTUSOL. El filtro de líneas
va solo por `CODLPS`: en las proformas del escritorio el `TIPLPS` de la línea
no siempre coincide con el `TIPPRE` de la cabecera (viene en blanco o
heredado), y filtrar por serie dejaba la consulta vacía.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any


def _text(value: Any) -> str:
    return str(value or "")


def _num(value: Any) -> float:
    try:
        return float(_text(value).strip().replace(",", ".") or 0)
    except ValueError:
        return 0.0


def _preview(text: str, width: int = 110) -> str:
    return text if len(text) <= width else text[:width] + "…"


def inspect_quote(client: Any, codpre: int, ejercicio: str) -> None:
    pre = client.load_table("F_PRE", filtro=f"CODPRE={codpre}", ejercicio=ejercicio)
    print(f"\n[{ejercicio}] F_PRE CODPRE={codpre}: {len(pre)} cabecera(s)")
    for row in pre:
        print(f"   TIPPRE={row.get('TIPPRE')!s} FECPRE={row.get('FECPRE')!s} "
              f"CNOPRE={_preview(_text(row.get('CNOPRE')), 50)!r} FOPPRE={row.get('FOPPRE')!s}")
    lps = client.load_table(
        "F_LPS", filtro=f"CODLPS={codpre} ORDER BY POSLPS", ejercicio=ejercicio,
    )
    series = sorted({_text(r.get("TIPLPS")) for r in lps})
    print(f"[{ejercicio}] F_LPS CODLPS={codpre}: {len(lps)} línea(s); TIPLPS presentes: {series}")
    if lps:
        print(f"   columnas: {', '.join(lps[0].keys())}")
    for row in lps:
        des = _text(row.get("DESLPS"))
        mem = _text(row.get("MEMLPS"))
        print(f"   TIP={_text(row.get('TIPLPS')):>2} POS={_text(row.get('POSLPS')):>3} "
              f"ART={_text(row.get('ARTLPS'))!r:<14} CAN={_text(row.get('CANLPS')):>6} "
              f"PRE={_text(row.get('PRELPS')):>10} len(DESLPS)={len(des):>4} "
              f"len(MEMLPS)={len(mem):>4}")
        print(f"      DESLPS: {_preview(des)}")
        if mem.strip():
            print(f"      MEMLPS: {_preview(mem)}")


def inspect_table(client: Any, ejercicio: str) -> None:
    rows = client.load_table("F_LPS", filtro="1=1", ejercicio=ejercicio)
    if not rows:
        print(f"\n[{ejercicio}] F_LPS vacía (¿ejercicio sin presupuestos?)")
        return
    lens = [len(_text(r.get("DESLPS"))) for r in rows]
    mems = [len(_text(r.get("MEMLPS"))) for r in rows]
    sin_art = [r for r in rows if not _text(r.get("ARTLPS")).strip()]
    print(f"\n[{ejercicio}] F_LPS: {len(rows)} líneas")
    print(f"   DESLPS: máx {max(lens)} | >255: {sum(1 for n in lens if n > 255)} "
          f"| >100: {sum(1 for n in lens if n > 100)}")
    print(f"   MEMLPS con texto: {sum(1 for n in mems if n)} | máx {max(mems)}")
    textos = [_text(r.get("DESLPS")) for r in rows]
    crlf = sum(1 for t in textos if "\r\n" in t)
    solo_lf = sum(1 for t in textos if "\n" in t.replace("\r\n", ""))
    # Qué saltos de línea usa el escritorio (BoHub escribe \r\n desde los
    # remates de proformas): si aquí sale «solo \n» en las del escritorio,
    # hay que revisar la normalización de `build_quote_line_payload`.
    print(f"   saltos de línea en DESLPS: con \\r\\n {crlf} | solo \\n {solo_lf}")
    print(f"   líneas sin artículo: {len(sin_art)} | de ellas con CANLPS=0: "
          f"{sum(1 for r in sin_art if _num(r.get('CANLPS')) == 0)}")
    print("   5 DESLPS más largas:")
    for row in sorted(rows, key=lambda r: len(_text(r.get("DESLPS"))), reverse=True)[:5]:
        des = _text(row.get("DESLPS"))
        print(f"      {_text(row.get('TIPLPS'))}-{_text(row.get('CODLPS'))} "
              f"POS={_text(row.get('POSLPS'))} len={len(des)}: {_preview(des, 90)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--codpre", type=int, default=4360,
                        help="número de la proforma a inspeccionar (por defecto 4360 = 5-004360)")
    parser.add_argument("--ejercicio", nargs="+", default=["2026", "2025"],
                        help="ejercicio(s) donde buscarla (por defecto 2026 y 2025)")
    parser.add_argument("--sin-estadistica", action="store_true",
                        help="no recorrer toda F_LPS (solo la proforma)")
    args = parser.parse_args(argv)

    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415

    client = FactusolClient.from_settings()
    for ejercicio in args.ejercicio:
        inspect_quote(client, args.codpre, ejercicio)
    if not args.sin_estadistica:
        inspect_table(client, args.ejercicio[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
