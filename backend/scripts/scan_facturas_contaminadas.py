"""Escaneo SOLO LECTURA de facturas de FACTUSOL con líneas contaminadas —
secuela del bug de emisión #382 (líneas del pedido homónimo de otra serie
arrastradas a `F_LFA`; el fix llegó el 2026-09-10).

Detector fiable: para cada factura, la suma de sus renglones de `F_LFA` (por
clave compuesta serie+número, `TIPLFA`+`CODLFA`) debe cuadrar con la base
imponible NETA de la cabecera (Σ `NET1FAC..NET4FAC`). Si no cuadra → el detalle
tiene líneas de más (o de menos) → contaminada.

NO escribe nada — ni en FACTUSOL (solo `CargaTabla`) ni en el CRM (solo lee el
historial de emisión para saber qué facturas emitió BoHub y su fecha). Reporta:
total revisadas, contaminadas (nº, cliente, base, suma de líneas, diferencia,
antes/después del fix) y limpias, y escribe un CSV con las contaminadas.

Uso (desde el VPS; dev/CI no alcanzan api.sdelsol.com):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.scan_facturas_contaminadas

    # opciones
    ... --csv /tmp/facturas_contaminadas.csv   # ruta del CSV (default)
    ... --ejercicio 2026                        # ejercicio de F_FAC / F_LFA
    ... --tolerance 0.005                       # tolerancia de redondeo (€)
    ... --only-bohub                            # no incluir las creadas a mano
    ... --no-crm                                # no cruzar con el CRM (origen y
                                                #   fecha solo desde FACTUSOL)

Copia el CSV fuera del contenedor con
`docker compose -f … cp api:/tmp/facturas_contaminadas.csv .`.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session

DEFAULT_CSV = "/tmp/facturas_contaminadas.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv", default=DEFAULT_CSV,
                        help="ruta del CSV (una fila por factura contaminada)")
    parser.add_argument("--ejercicio", default=None,
                        help="ejercicio FACTUSOL (default: el de ajustes)")
    parser.add_argument("--tolerance", type=float, default=None,
                        help="tolerancia de redondeo en € (default: 0.005)")
    parser.add_argument("--only-bohub", action="store_true",
                        help="no revisar las facturas creadas a mano en FACTUSOL")
    parser.add_argument("--no-crm", action="store_true",
                        help="no cruzar con el CRM (origen/fecha solo de FACTUSOL)")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.invoice_scan import (
        DEFAULT_TOLERANCE,
        bohub_emit_map,
        build_report,
        format_report,
        write_csv,
    )
    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.service import ejercicio_for

    tolerance = args.tolerance if args.tolerance is not None else DEFAULT_TOLERANCE

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = args.ejercicio or ejercicio_for(session)
        print(f"Leyendo F_FAC y F_LFA (ejercicio {ejercicio})… "
              "SOLO LECTURA, no se escribe nada.")
        fac = client.load_table(
            "F_FAC", filtro="1=1 ORDER BY CODFAC DESC", ejercicio=ejercicio,
        )
        lfa = client.load_table("F_LFA", filtro="1=1", ejercicio=ejercicio)
        print(f"  {len(fac)} facturas · {len(lfa)} líneas F_LFA")
        emit_map = {}
        if not args.no_crm:
            emit_map = bohub_emit_map(session)
            print(f"  {len(emit_map)} facturas con emisión registrada en el CRM")
        report = build_report(
            fac, lfa, ejercicio=str(ejercicio), bohub_map=emit_map,
            tolerance=tolerance, include_manual=not args.only_bohub,
        )

    print()
    print(format_report(report))
    path = write_csv(report, args.csv)
    print()
    print(f"CSV (facturas contaminadas, separador ';'): {path}")
    print("SOLO LECTURA — no se ha escrito nada ni en FACTUSOL ni en el CRM.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
