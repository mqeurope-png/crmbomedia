"""Diagnóstico (SOLO LECTURA): pedidos web (WooCommerce) YA facturados desde
BoHub cuyo IVA / estructura en BoHub no cuadra con el impuesto REAL de
WooCommerce — candidatos a revisar A MANO la factura de FACTUSOL.

Antes del fix, el mapper metía un IVA fabricado (21 % fijo) y no importaba el
envío ni las comisiones. La factura de FACTUSOL de un pedido web se genera
COPIANDO el pedido de cliente (F_PCL) que crea la app Woo→FACTUSOL, no las
líneas de BoHub; pero si el impuesto real de Woo es 0 (o un tipo distinto) y en
BoHub consta un 21 %, conviene comprobar que la factura emitida es correcta.

Este comando NO re-factura ni escribe nada: las rectificativas / abonos los
decide administración. Solo lista los pedidos facturados cuyo desglose en BoHub
difiere del real de Woo, con la diferencia y un CSV.

Uso (VPS):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.diagnostico_iva_pedidos_web_facturados

Opciones: --limit N · --store SLUG · --no-live (solo payloads guardados) ·
--csv RUTA.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

DEFAULT_CSV = "/tmp/diagnostico_iva_web_facturados.csv"
_FIELDS = ["order_id", "order_number", "store", "factura", "fuente",
           "bohub_base", "bohub_iva", "woo_base", "woo_iva",
           "woo_envio", "woo_cargos", "woo_total", "diff"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
                Order.factusol_invoice_number.is_not(None),
                Order.cancelled_at.is_(None),
            ).options(selectinload(Order.lines)).order_by(Order.created_at.desc())
        )
        descuadrados: list[dict[str, str]] = []
        sin_payload: list[str] = []
        procesados = cuadran = 0
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
                cuadran += 1
                continue
            descuadrados.append({
                "order_id": order.id, "order_number": order.order_number,
                "store": store.account_id,
                "factura": str(order.factusol_invoice_number or ""),
                "fuente": fuente,
                "bohub_base": f"{antes['base']:.2f}", "bohub_iva": f"{antes['iva']:.2f}",
                "woo_base": f"{woo_eco['base']:.2f}", "woo_iva": f"{woo_eco['iva']:.2f}",
                "woo_envio": f"{woo_eco['envio']:.2f}", "woo_cargos": f"{woo_eco['cargos']:.2f}",
                "woo_total": f"{woo_eco['total']:.2f}", "diff": " · ".join(diff),
            })

        print(f"Pedidos web FACTURADOS revisados: {procesados} "
              f"(cuadran: {cuadran}; sin payload recuperable: {len(sin_payload)}).")
        print(f"Facturas a REVISAR a mano en FACTUSOL: {len(descuadrados)}")
        for d in descuadrados[:80]:
            print(f"  {d['order_number']} · factura {d['factura']} "
                  f"[{d['store']}·{d['fuente']}] → {d['diff']}")
        if len(descuadrados) > 80:
            print(f"  … y {len(descuadrados) - 80} más (ver CSV)")
        if sin_payload:
            print(f"\nSin payload recuperable (no se pudo comparar): "
                  f"{', '.join(sin_payload[:30])}"
                  + (" …" if len(sin_payload) > 30 else ""))

        csv_path = _write_csv(descuadrados, args.csv)
        print(f"\nCSV: {csv_path}")
        print("\nSOLO LECTURA: no se ha tocado nada. Revisa estas facturas a mano "
              "en FACTUSOL; las rectificativas / abonos los decide administración.")
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
