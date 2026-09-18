"""Cerrar en FACTUSOL los pedidos de cliente (F_PCL) de pedidos web YA
facturados desde BoHub que se quedaron ABIERTOS (Parte B, retroactivo).

Antes de este PR, `emit_invoice` no marcaba el F_PCL como facturado por defecto,
así que los pedidos de cliente web se acumulaban «abiertos» en FACTUSOL aunque
su factura estuviera emitida y cobrada (caso FLUXLA-5784). Ahora se cierran al
facturar; este comando limpia los que quedaron pendientes.

Lee cada pedido web `invoiced_by_erp`, localiza su F_PCL por REFPCL y, si sigue
abierto (ESTPCL ≠ el valor de «facturado»), lo cierra con el mecanismo idempotente
`mark_origin_converted` (mismo que la emisión). El cierre se ENCOLA en el worker
serial `factusol:writes` (nunca inline), como el resto de escrituras FACTUSOL.

Uso (VPS):

    # DRY-RUN (por defecto): NO escribe. Lista los F_PCL abiertos + CSV.
    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.cerrar_pedidos_cliente_facturados
    # aplicar de verdad (encola el cierre de cada uno): exige --apply.
    ... python -m scripts.cerrar_pedidos_cliente_facturados --apply

Opciones: --apply · --limit N (máx. pedidos) · --csv RUTA.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

DEFAULT_CSV = "/tmp/pedidos_cliente_abiertos.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="encola el cierre del F_PCL de cada pedido abierto")
    parser.add_argument("--limit", type=int, default=None,
                        help="procesar como mucho N pedidos")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="CSV de candidatos (dry-run)")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.models import Order, OrderSource
    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.jobs import enqueue_close_order_pcl
    from app.integrations.factusol.service import (
        _estado_str,
        _store_ref_prefix,
        ejercicio_for,
        find_pcl_by_order,
        origin_mark_value,
    )

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        target = origin_mark_value(session, "pedidos")
        if not target:
            print("`estpcl_invoiced` está VACÍO en /erp/settings: el cierre del "
                  "F_PCL está desactivado. Configúralo (p. ej. «2») antes de "
                  "usar este comando. No se ha tocado nada.")
            return 2
        print(f"Valor de ESTPCL «facturado»: {target}. Ejercicio {ejercicio}. "
              "FACTUSOL solo se escribe con --apply (vía factusol:writes).")

        orders = session.scalars(
            select(Order).where(
                Order.external_source == OrderSource.WOOCOMMERCE,
                Order.factusol_invoice_number.is_not(None),
                Order.cancelled_at.is_(None),
            ).order_by(Order.created_at.desc())
        )
        abiertos: list[dict[str, str]] = []
        sin_pcl = 0
        procesados = 0
        for order in orders:
            if args.limit is not None and procesados >= args.limit:
                break
            procesados += 1
            prefix = _store_ref_prefix(session, order)
            pcl = find_pcl_by_order(client, order, ejercicio, ref_prefix=prefix)
            if pcl is None:
                sin_pcl += 1
                continue
            if _estado_str(pcl.get("ESTPCL")) == _estado_str(target):
                continue   # ya cerrado
            abiertos.append({
                "order_id": order.id, "order_number": order.order_number,
                "factura": str(order.factusol_invoice_number or ""),
                "codpcl": str(pcl.get("CODPCL") or ""),
                "estpcl": _estado_str(pcl.get("ESTPCL")),
            })

        print(f"\nPedidos web facturados revisados: {procesados} "
              f"(sin F_PCL localizable: {sin_pcl}).")
        print(f"F_PCL ABIERTOS que hay que cerrar: {len(abiertos)}")
        for a in abiertos:
            print(f"  {a['order_number']} · factura {a['factura']} · "
                  f"F_PCL {a['codpcl']} (ESTPCL={a['estpcl'] or '∅'})")

        csv_path = _write_csv(abiertos, args.csv)
        print(f"\nCSV de candidatos: {csv_path}")

        if not args.apply:
            print("\nDRY-RUN: no se ha cerrado nada. Cuando lo tengas claro:")
            print("  1) HAZ UNA COPIA DE SEGURIDAD de la base de datos.")
            print("  2) python -m scripts.cerrar_pedidos_cliente_facturados --apply")
            return 0
        if not abiertos:
            print("\nNada que cerrar. No se ha tocado nada.")
            return 0

        for a in abiertos:
            job_id = enqueue_close_order_pcl(a["order_id"])
            print(f"  encolado cierre de {a['order_number']} (job {job_id})")
        print(f"\nEncolados {len(abiertos)} cierres en factusol:writes. Revisa el "
              "worker-factusol y confirma en FACTUSOL desktop que quedan como "
              "«Enviado/facturado».")
    return 0


def _write_csv(rows: list[dict[str, str]], path: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["order_id", "order_number", "factura", "codpcl", "estpcl"])
        for r in rows:
            w.writerow([r["order_id"], r["order_number"], r["factura"],
                        r["codpcl"], r["estpcl"]])
    return p


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
