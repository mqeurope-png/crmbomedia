"""Diagnóstico SOLO LECTURA de la incidencia «los pedidos web dejan de aparecer
en la bandeja» desde ~13-sep (filtro «solo processing» de PR #387).

NO cambia nada: solo lee `sync_logs`, `integration_events` y `orders` y cuenta
cuántos webhooks de pedido se descartan y con qué estado de Woo, cuántos pedidos
distintos llegaron y nunca se crearon, cuántos creados-pero-ocultos, y la fecha
de corte de cada tienda. Alimenta la decisión de arreglo (que NO se hace aquí).

Uso (en producción, dentro del contenedor api):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.diag_pedidos_web_no_aparecen
    ... --since 2026-09-13     # ventana (por defecto el día del despliegue)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy.orm import Session


def _parse_since(value: str | None) -> datetime:
    from app.erp.woo_ingest_diag import DEFAULT_SINCE  # noqa: PLC0415

    if not value:
        return DEFAULT_SINCE
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"--since no es una fecha ISO válida: {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--since", default=None,
                        help="fecha ISO desde la que contar (default 2026-09-13)")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.woo_ingest_diag import diagnose, format_report

    since = _parse_since(args.since)
    with Session(get_engine()) as session:
        diag = diagnose(session, since=since)
    print(format_report(diag))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
