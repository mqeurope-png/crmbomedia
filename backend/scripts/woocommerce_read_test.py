"""Sprint 0 — Test manual de lectura WooCommerce (últimos 20 pedidos).

    cd backend
    python -m scripts.woocommerce_read_test [PREFIX]   # default WOO_MBOLASERS

Imprime resumen por pedido (id, fecha, estado, pago, total, SKUs) para
validar credenciales + shape antes de diseñar el sync definitivo.
"""
from __future__ import annotations

import sys

from app.integrations.woocommerce.client import WooClient, WooError


def main() -> int:
    prefix = sys.argv[1] if len(sys.argv) > 1 else "WOO_MBOLASERS"
    try:
        client = WooClient(prefix=prefix)
        orders = client.list_orders(per_page=20)
    except WooError as exc:
        print(f"✖ {exc} (status={exc.status})\n{exc.body}")
        return 1
    print(f"{len(orders)} pedidos de {client.base_url}:\n")
    for o in orders:
        skus = ", ".join(
            (li.get("sku") or f"#{li.get('product_id')}")
            for li in o.get("line_items", [])
        )
        print(
            f"  #{o['id']} {o.get('date_created', '')[:10]} "
            f"status={o.get('status')} paid={bool(o.get('date_paid'))} "
            f"total={o.get('total')}{o.get('currency', '')} skus=[{skus}]"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
