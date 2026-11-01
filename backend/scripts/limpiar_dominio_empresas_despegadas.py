"""Limpiar el dominio de la lista que se quedó en las empresas de los lotes de
despegue ya pasados (1/11/2026). No escribe en FACTUSOL.

Hasta el 1/11/2026 el despegue quitaba los contactos pero dejaba en la empresa
el `domain` que los había pegado: una empresa con «Dominio: outlook.fr» en su
ficha. Este script lo limpia en las empresas de los lotes anteriores. Por
defecto, los que no se han deshecho, ni a medias, y sin tocar una empresa a la
que alguien ya le devolvió el dominio deshaciendo un lote. Desde ahora, el
despegue ya lo hace solo.

Cada empresa lleva un apunte en el lote que trató SU dominio (si ninguno, el
primero en el que salió): deshacer ese lote con
`despegar_dominios_compartidos --revertir LOTE --yes` le devuelve el dominio,
igual que a los contactos.

Uso (VPS), siempre en este orden:

    # 1. Ver qué haría (no escribe nada).
    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.limpiar_dominio_empresas_despegadas --probar
    # 2. Hacerlo (exige --yes).
    ... python -m scripts.limpiar_dominio_empresas_despegadas --apply --yes
    # Solo unos lotes:
    ... --probar --lote 202610312054-164cdd --lote 202611010454-ac9c89

En la consola no salen nombres: las empresas van por id corto, código de
cliente de FACTUSOL y dominio.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from sqlalchemy.orm import Session

POR = "script limpiar_dominio_empresas_despegadas"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    modo = ap.add_mutually_exclusive_group(required=True)
    modo.add_argument("--probar", action="store_true", help="ver qué haría (no escribe)")
    modo.add_argument("--apply", action="store_true", help="hacerlo (exige --yes)")
    ap.add_argument("--yes", action="store_true", help="confirmación de --apply")
    ap.add_argument("--lote", action="append", help="solo este lote (se puede repetir)")
    args = ap.parse_args(argv)

    from app.db.session import get_engine
    from app.services import despegue_dominios as dd

    if args.apply and not args.yes:
        print("Falta --yes.", file=sys.stderr)
        return 2
    with Session(get_engine()) as s:
        filas = dd.limpiar_dominios_de_lotes(s, args.lote, por=POR, aplicar=args.apply)
        titulo = "LIMPIAR EL DOMINIO DE LAS EMPRESAS DESPEGADAS"
        print(titulo + (" — PRUEBA, no escribe nada" if args.probar else ""))
        print(f"Lotes: {', '.join(args.lote) if args.lote else 'todos los no deshechos'}")
        verbo = "se limpia" if args.probar else "limpiado"
        print(f"Empresas con un dominio de la lista ({verbo}): {len(filas)}")
        for lote, n in sorted(Counter(f["lote"] for f in filas).items()):
            print(f"  lote {lote}: {n}")
        print("\nPOR DOMINIO")
        for d, n in Counter(f["dominio"] for f in filas).most_common():
            print(f"  {d:34} {n}")
        print("\nPOR EMPRESA")
        print(f"  {'empresa':8} {'cliente':>8} {'dominio':34} lote")
        for f in filas:
            print(f"  {f['company_id'][:8]} {f['codcli'] or '—':>8} {f['dominio'][:34]:34} "
                  f"{f['lote']}")
        if args.probar:
            print("\nNo se ha escrito nada. Para hacerlo: --apply --yes")
        else:
            print("\nHecho. Deshacer un lote le devuelve el dominio a sus empresas: "
                  "despegar_dominios_compartidos --revertir LOTE --yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
