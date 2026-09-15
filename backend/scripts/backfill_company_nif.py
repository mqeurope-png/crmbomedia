"""Rellenar el NIF de las empresas del CRM desde FACTUSOL: para las
vinculadas por CODCLI y sin NIF en el CRM, copia `NIFCLI` del cliente a
`tax_id`. NUNCA al revés — FACTUSOL solo lectura. Si el CRM ya tiene otro NIF
distinto no vacío, no se pisa: va a revisión.

Uso (VPS):

    python -m scripts.backfill_company_nif             # DRY-RUN (no cambia nada)
    python -m scripts.backfill_company_nif --apply --yes
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="escribe el NIF en el CRM")
    parser.add_argument("--yes", action="store_true", help="confirmación explícita del --apply")
    parser.add_argument("--sample", type=int, default=20)
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.company_cleanup import apply_backfill, plan_backfill
    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.service import ejercicio_for

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        print(f"Leyendo F_CLI (ejercicio {ejercicio})… FACTUSOL solo lectura.")
        fcli = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
        plan = plan_backfill(session, fcli)

        print(f"\nA rellenar NIF (vinculadas y sin NIF en el CRM): {len(plan.to_fill)}")
        for item in plan.to_fill[:args.sample]:
            print(f"  {item.name} [{item.company_id}] CODCLI {item.codcli} → NIF {item.nif}")
        if len(plan.to_fill) > args.sample:
            print(f"  … y {len(plan.to_fill) - args.sample} más")
        print(f"\nA REVISAR (el CRM ya tiene otro NIF; no se pisa): {len(plan.review)}")
        for item in plan.review[:args.sample]:
            print(f"  {item.name} [{item.company_id}] CRM «{item.crm_nif}» vs FACTUSOL "
                  f"«{item.factusol_nif}» (CODCLI {item.codcli})")

        if not args.apply:
            print("\nDRY-RUN: no se ha escrito nada. Con --apply --yes se rellena tax_id.")
            return 0
        if not args.yes:
            print("\n--apply requiere también --yes. No se ha tocado nada.")
            return 2
        n = apply_backfill(session, plan)
        session.commit()
        print(f"\nRellenado el NIF de {n} empresas (tax_id; FACTUSOL intacto).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
