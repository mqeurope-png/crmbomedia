"""Discovery (SOLO LECTURA) del estado de las empresas del CRM frente a
FACTUSOL: cuántas existen de verdad en F_CLI (cruce por NIF/VAT
normalizado), cuántas son ruido y cuáles tienen negocio vivo. Para decidir
con datos qué se vincula y qué se archiva — aquí NO se vincula, NO se borra
y NO se archiva nada: solo lee (CRM y `CargaTabla` de FACTUSOL) y reporta.

Uso (desde el VPS; dev/CI no alcanzan api.sdelsol.com):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.discover_companies_factusol

    # opciones
    ... --csv /tmp/empresas_crm_vs_factusol.csv   # ruta del CSV (default)
    ... --recent-days 180                          # ventana de «actividad reciente»
    ... --ejercicio 2026                           # ejercicio de F_CLI / F_PRE
    ... --no-proformas                             # no leer F_PRE
    ... --sample 30                                # nº de casos ambiguos que imprime

Copia el CSV fuera del contenedor con
`docker compose -f … cp api:/tmp/empresas_crm_vs_factusol.csv .`.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from sqlalchemy.orm import Session

DEFAULT_CSV = "/tmp/empresas_crm_vs_factusol.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", default=DEFAULT_CSV, help="ruta del CSV (una fila por empresa)")
    parser.add_argument("--recent-days", type=int, default=180,
                        help="ventana de email / actividad reciente (días)")
    parser.add_argument("--ejercicio", default=None,
                        help="ejercicio FACTUSOL (default: el de ajustes)")
    parser.add_argument("--no-proformas", action="store_true", help="no leer F_PRE")
    parser.add_argument("--sample", type=int, default=15, help="casos ambiguos a imprimir")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.company_discovery import build_report, format_report, write_csv
    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.service import ejercicio_for

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = args.ejercicio or ejercicio_for(session)
        print(f"Leyendo F_CLI (ejercicio {ejercicio})… solo lectura, no se escribe nada.")
        fcli = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
        print(f"  {len(fcli)} clientes en F_CLI")
        proformas: dict[str, int] | None = None
        if not args.no_proformas:
            print("Leyendo F_PRE (proformas por cliente)…")
            try:
                rows = client.load_table("F_PRE", filtro="1=1", ejercicio=ejercicio)
                proformas = dict(Counter(
                    str(r.get("CLIPRE")).strip() for r in rows if r.get("CLIPRE") is not None
                ))
                print(f"  {len(rows)} proformas de {len(proformas)} clientes")
            except Exception as exc:  # noqa: BLE001 — sin proformas el informe sigue
                print(f"  F_PRE no disponible ({exc}); se sigue sin proformas")
        report = build_report(session, fcli, proformas_by_codcli=proformas,
                              recent_days=args.recent_days)

    print()
    print(format_report(report, sample=args.sample))
    path = write_csv(report, args.csv)
    print()
    print(f"CSV (una fila por empresa, separador ';'): {path}")
    print("Nada se ha escrito ni en el CRM ni en FACTUSOL.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
