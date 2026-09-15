"""Diagnóstico SOLO LECTURA del vínculo factura FACTUSOL → pedido del CRM
(«Enviar factura al cliente», bugs #426: destinatario / tienda de OTRO pedido
homónimo).

Para una factura (serie, número) enseña la cabecera de F_FAC, la empresa del
CRM enlazada a su CLIFAC, TODOS los pedidos con ese nº de factura (tienda,
serie guardada, empresa, contacto, referencia) con el veredicto de cada uno,
qué pedido devolvía la búsqueda ANTIGUA (solo por número) y cuál devuelve la
NUEVA, y el destinatario que saldría. NO escribe nada (ni FACTUSOL ni CRM).

Uso (en producción, dentro del contenedor api):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.diag_vinculo_factura_pedido 5 260090
    ... --ejercicio 2026     # por defecto el ejercicio configurado
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("serie", type=int, help="serie (TIPFAC) de la factura, p. ej. 5")
    parser.add_argument("codigo", type=int, help="número (CODFAC) de la factura, p. ej. 260090")
    parser.add_argument("--ejercicio", default=None,
                        help="ejercicio FACTUSOL (por defecto el configurado)")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.invoice_email_diag import diagnose_invoice_link, format_report
    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.service import ejercicio_for

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = args.ejercicio or ejercicio_for(session)
        diag = diagnose_invoice_link(
            session, client, serie=args.serie, codigo=args.codigo, ejercicio=ejercicio,
        )
    print(format_report(diag))
    return 0 if not diag.get("error") else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
