"""Inventario de los textos de cara al cliente que aún nombran «Bomedia».

Solo LEE: es exactamente lo que encuentra la comprobación del Cuadre
`bomedia_en_textos_cliente`, sacado como un listado para repasar a mano (las
plantillas comerciales antiguas, FESPA, SmartJet… son datos y los corrige Bart
en su pantalla; aquí no se toca nada). No cuenta las direcciones de bomedia.net
ni la serie 1 de FACTUSOL.

Uso (VPS):

    docker compose --env-file .env.production -f docker-compose.prod.yml \\
        -f docker-compose.plesk.yml exec api python -m scripts.inventario_bomedia

    … --markdown     # tabla lista para pegar en el PR
"""
from __future__ import annotations

import argparse
import sys


def _celda(texto: str) -> str:
    return " ".join(str(texto).split()).replace("|", "\\|")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--markdown", action="store_true", help="salida en tabla Markdown")
    args = parser.parse_args(argv)

    from sqlalchemy.orm import Session

    from app.db.session import get_engine
    from app.erp.cuadre.checks_textos import agrupar, textos_cliente

    with Session(get_engine()) as session:
        items = agrupar(textos_cliente(session))

    if args.markdown:
        print("| # | Dónde | Campos | Frase(s) | Se arregla en |")
        print("|---|---|---|---|---|")
        for n, it in enumerate(items, 1):
            frases = " · ".join(f"«{f}»" for f in it["frases"])
            print(f"| {n} | {_celda(it['nombre'])} | {_celda(', '.join(it['campos']))} | "
                  f"{_celda(frases)} | {it['enlace'] or ''} |")
    else:
        for n, it in enumerate(items, 1):
            print(f"{n:>3}. {it['nombre']}  [{', '.join(it['campos'])}]  → {it['enlace'] or '—'}")
            for f in it["frases"]:
                print(f"       «{f}»")
    print(f"\n{len(items)} texto(s) de cara al cliente nombran «Bomedia».", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
