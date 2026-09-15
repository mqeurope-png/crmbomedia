"""Auto-vincular empresas del CRM a su cliente de FACTUSOL por NIF/VAT
normalizado (guarda el CODCLI en el CRM). FACTUSOL solo lectura.

Solo vincula cuando el NIF casa con UN ÚNICO CODCLI. Si casa con varios, no
vincula: lo lista para revisión a mano.

Uso (VPS):

    python -m scripts.link_companies_by_nif            # DRY-RUN (no cambia nada)
    python -m scripts.link_companies_by_nif --apply --yes
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="guarda el CODCLI en el CRM")
    parser.add_argument("--yes", action="store_true", help="confirmación explícita del --apply")
    parser.add_argument("--sample", type=int, default=20)
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.company_cleanup import apply_links, plan_links
    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.service import ejercicio_for

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        print(f"Leyendo F_CLI (ejercicio {ejercicio})… FACTUSOL solo lectura.")
        fcli = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
        plan = plan_links(session, fcli)

        print(f"\nA vincular (NIF con un único CODCLI): {len(plan.to_link)}")
        for item in plan.to_link[:args.sample]:
            print(f"  {item.name} [{item.company_id}] "
                  f"{', '.join(item.keys)} → CODCLI {item.codcli}")
        if len(plan.to_link) > args.sample:
            print(f"  … y {len(plan.to_link) - args.sample} más")
        print(f"\nA REVISAR (NIF con varios CODCLI, no se vinculan): {len(plan.review)}")
        for item in plan.review[:args.sample]:
            print(f"  {item.name} [{item.company_id}] {', '.join(item.keys)} → "
                  f"CODCLI {', '.join(item.codclis)}")

        if not args.apply:
            print("\nDRY-RUN: no se ha vinculado nada. Con --apply --yes se guardan los CODCLI.")
            return 0
        if not args.yes:
            print("\n--apply requiere también --yes. No se ha tocado nada.")
            return 2
        n = apply_links(session, plan)
        session.commit()
        print(f"\nVinculadas {n} empresas (CODCLI guardado en el CRM; FACTUSOL intacto).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
