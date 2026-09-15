"""Corregir vínculos factura↔pedido CRUZADOS (Lote 2 · Bloque B).

Por defecto es un DRY-RUN: enseña el plan y no escribe nada. Con `--apply`
pide confirmación escrita (o `--yes`) y aplica: re-vincula cada pedido cruzado
a la factura cuya REFFAC es su referencia (guardando serie + número) o, si no
tiene factura propia, lo deja SIN vínculo. Solo escribe campos del pedido en
BoHub; FACTUSOL solo se lee.

Uso (en producción, dentro del contenedor api):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.corregir_vinculos_factura_cruzados            # dry-run
    ... --only BOPRIN-99919,BOPRIN-99927,ARTISJ-9537                    # solo esos
    ... --apply                       # aplica (pide escribir APLICAR)
    ... --apply --yes                 # aplica sin preguntar
    ... --categorias cruzado,ambiguo  # también los ambiguos del escaneo
    ... --ejercicio 2026
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apply", action="store_true", help="aplicar el plan (si no, dry-run)")
    parser.add_argument("--yes", action="store_true", help="no pedir confirmación escrita")
    parser.add_argument("--only", default=None, help="nº de pedido separados por coma")
    parser.add_argument("--categorias", default="cruzado",
                        help="categorías del escaneo a corregir (cruzado[,ambiguo])")
    parser.add_argument("--ejercicio", default=None)
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.invoice_link_fix import apply_relinks, format_plan, plan_relinks
    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.service import ejercicio_for

    only = {s.strip() for s in (args.only or "").split(",") if s.strip()} or None
    categories = tuple(c.strip() for c in args.categorias.split(",") if c.strip())
    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = args.ejercicio or ejercicio_for(session)
        plan = plan_relinks(session, client, ejercicio=ejercicio, only=only,
                            categories=categories)
        print(format_plan(plan))
        if not args.apply:
            print("\n(dry-run: no se ha escrito nada; añade --apply para corregir)")
            return 0
        if not plan["plans"]:
            return 0
        if not args.yes:
            answer = input("\nEscribe APLICAR para corregir los vínculos anteriores: ")
            if answer.strip() != "APLICAR":
                print("Cancelado: no se ha escrito nada.")
                return 1
        result = apply_relinks(session, plan)
    print(f"\nCorregidos: {len(result['done'])}")
    for d in result["done"]:
        print(f"  - {d['order_number']}: {d['action']} → {d['numero'] or 'sin vínculo'}")
    if result["skipped"]:
        print(f"Omitidos: {len(result['skipped'])}")
        for s in result["skipped"]:
            print(f"  - {s['order_number']}: {s['reason']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
