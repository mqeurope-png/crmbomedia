"""Escaneo SOLO LECTURA de vínculos factura↔pedido cruzados (Bloque 1c).

Lista los pedidos cuyo nº de factura apunta a una factura de F_FAC cuyo CLIFAC
es de OTRA empresa («cruzado»), y los que no se pueden resolver («ambiguo»:
mismo nº en varias series sin serie guardada ni referencia/cliente que
desempaten). No corrige nada: ni FACTUSOL ni el CRM.

Uso (en producción, dentro del contenedor api):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.scan_vinculos_factura_cruzados
    ... --ejercicio 2026          # por defecto el configurado
    ... --todos                   # lista también ok / sin_fila / sin_empresa
    ... --csv /tmp/vinculos.csv   # volcado completo en CSV (;)
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ejercicio", default=None)
    parser.add_argument("--todos", action="store_true", help="listar todas las categorías")
    parser.add_argument("--csv", default=None, help="ruta del CSV completo a escribir")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.invoice_link_scan import format_report, scan_invoice_links, to_csv
    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.service import ejercicio_for

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = args.ejercicio or ejercicio_for(session)
        scan = scan_invoice_links(session, client, ejercicio=ejercicio)
    only = {"ok", "cruzado", "ambiguo", "sin_fila", "sin_empresa"} if args.todos else None
    print(format_report(scan, only=only))
    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as fh:
            fh.write(to_csv(scan))
        print(f"\nCSV escrito en {args.csv}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
