"""Recalcular el DESGLOSE económico de los pedidos web (WooCommerce) aún NO
facturados, tras el fix del IVA fabricado y del envío/comisiones no importados
(`app/integrations/woocommerce/mapper.py`).

Los pedidos web importados ANTES del fix llevan las líneas con un IVA inventado
(21 % fijo) y sin el envío ni las comisiones, así que el «Resumen económico» de
la ficha y la cola «Por facturar» enseñan un desglose falso (el total sí es el
de Woo, que nunca se recalcula). Este comando rehace las líneas de esos pedidos
desde el payload de Woo (el guardado en `integration_events`, o en vivo si no
hay), con la lógica nueva: IVA real + envío + comisiones. Idempotente: los que
ya cuadran se saltan.

Solo toca pedidos web SIN factura (`invoiced_by_erp` NO). Los ya facturados no
se tocan aquí — para esos, `diagnostico_iva_pedidos_web_facturados.py` los lista
para revisarlos a mano. No escribe NADA en FACTUSOL (solo BoHub).

Uso (VPS):

    # DRY-RUN (por defecto): NO escribe. Lista lo que cambiaría + CSV.
    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.recalcular_importes_pedidos_web
    # aplicar de verdad (rehace las líneas): exige --apply.
    ... python -m scripts.recalcular_importes_pedidos_web --apply

Opciones: --apply · --limit N · --store SLUG · --no-live (solo payloads
guardados, sin llamar a la REST API de Woo) · --csv RUTA.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

DEFAULT_CSV = "/tmp/recalculo_importes_web.csv"
_FIELDS = ["order_id", "order_number", "store", "fuente",
           "base", "envio", "cargos", "iva", "total", "diff"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="rehace las líneas de los pedidos que no cuadran")
    parser.add_argument("--limit", type=int, default=None,
                        help="revisar como mucho N pedidos")
    parser.add_argument("--store", default=None,
                        help="limitar a una tienda (slug de integration_accounts)")
    parser.add_argument("--no-live", action="store_true",
                        help="usar solo payloads guardados (no llamar a Woo)")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="CSV de candidatos")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.models import Order, OrderSource
    from app.erp.models.orders import InvoiceStatus
    from app.integrations.woocommerce.mapper import remap_web_order_lines
    from app.integrations.woocommerce.recalc import (
        economics_mismatch,
        order_economics,
        payload_for_order,
        store_for_order,
        woo_economics,
    )

    with Session(get_engine()) as session:
        orders = session.scalars(
            select(Order).where(
                Order.external_source == OrderSource.WOOCOMMERCE,
                Order.factusol_invoice_number.is_(None),
                Order.invoice_status != InvoiceStatus.INVOICED_BY_ERP.value,
                Order.cancelled_at.is_(None),
            ).options(selectinload(Order.lines)).order_by(Order.created_at.desc())
        )
        cambiados: list[dict[str, str]] = []
        sin_payload: list[str] = []
        procesados = ya_cuadran = 0
        for order in orders:
            if args.limit is not None and procesados >= args.limit:
                break
            store = store_for_order(session, order)
            if store is None:
                continue
            if args.store and store.account_id != args.store:
                continue
            procesados += 1
            woo, fuente = payload_for_order(
                session, order, store, live=not args.no_live)
            if woo is None:
                sin_payload.append(order.order_number)
                continue
            antes = order_economics(order)
            woo_eco = woo_economics(woo)
            diff = economics_mismatch(antes, woo_eco)
            if not diff:
                ya_cuadran += 1
                continue
            cambiados.append({
                "order_id": order.id, "order_number": order.order_number,
                "store": store.account_id, "fuente": fuente,
                "base": f"{woo_eco['base']:.2f}", "envio": f"{woo_eco['envio']:.2f}",
                "cargos": f"{woo_eco['cargos']:.2f}", "iva": f"{woo_eco['iva']:.2f}",
                "total": f"{woo_eco['total']:.2f}", "diff": " · ".join(diff),
            })
            if args.apply:
                remap_web_order_lines(session, order, woo, store)

        print(f"Pedidos web SIN factura revisados: {procesados} "
              f"(ya cuadraban: {ya_cuadran}; sin payload recuperable: "
              f"{len(sin_payload)}).")
        print(f"Pedidos a recalcular: {len(cambiados)}")
        for c in cambiados[:50]:
            print(f"  {c['order_number']} [{c['store']}·{c['fuente']}] → "
                  f"base {c['base']} envío {c['envio']} cargos {c['cargos']} "
                  f"IVA {c['iva']} · {c['diff']}")
        if len(cambiados) > 50:
            print(f"  … y {len(cambiados) - 50} más (ver CSV)")
        if sin_payload:
            print(f"\nSin payload recuperable (revisar a mano): "
                  f"{', '.join(sin_payload[:30])}"
                  + (" …" if len(sin_payload) > 30 else ""))

        csv_path = _write_csv(cambiados, args.csv)
        print(f"\nCSV: {csv_path}")

        if not args.apply:
            print("\nDRY-RUN: no se ha tocado nada. Cuando lo tengas claro:")
            print("  1) HAZ UNA COPIA DE SEGURIDAD de la base de datos.")
            print("  2) python -m scripts.recalcular_importes_pedidos_web --apply")
            return 0
        if not cambiados:
            print("\nNada que recalcular. No se ha tocado nada.")
            return 0
        session.commit()
        print(f"\nRecalculados {len(cambiados)} pedidos (líneas rehechas en "
              "BoHub; FACTUSOL no se ha tocado).")
    return 0


def _write_csv(rows: list[dict[str, str]], path: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_FIELDS, delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
