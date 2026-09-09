"""ERP · WooCommerce — CLI de la reconciliación de estados (vía de escape).

Saca el recuento de la reconciliación SIN pasar por la cola ni por el proxy
(útil si la cola se congestiona o para diagnóstico). Reutiliza EXACTAMENTE la
misma lógica que el job y el endpoint (`reconcile_open_order_statuses`: listado
por estado × cruce con activos); no duplica nada.

Uso (dentro del contenedor `api`):

    python -m scripts.erp_reconcile_woo --dry-run          # (por defecto) solo cuenta
    python -m scripts.erp_reconcile_woo --apply            # aplica (pide confirmación)
    python -m scripts.erp_reconcile_woo --apply --yes      # aplica sin preguntar
    python -m scripts.erp_reconcile_woo --store boprint    # solo una tienda
"""
from __future__ import annotations

import argparse
import sys
from typing import Any


def _run(dry_run: bool, store: str | None) -> dict[str, Any]:
    from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.woocommerce.reconcile import (  # noqa: PLC0415
        reconcile_open_order_statuses,
    )

    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    with factory() as session:
        return reconcile_open_order_statuses(
            session, dry_run=dry_run, store_account_id=store,
        )


def _print_summary(summary: dict[str, Any], *, applied: bool) -> None:
    verbo = "cambiarían" if not applied else "cambiaron"
    print(f"\n{'APLICADO' if applied else 'PREVISUALIZACIÓN (dry-run)'} — "
          f"{summary['scanned']} pedidos activos re-consultados "
          f"({summary.get('woo_calls', '?')} llamadas a WooCommerce)")
    print(f"  Saldrían del seguimiento: {summary['removed_total']} pedidos que {verbo}")
    print(f"    · cancelados:              {summary['to_cancel']}")
    print(f"    · fallidos:                {summary['to_fail']}")
    print(f"    · reembolsos no cumplidos: {summary['to_refund_out']}")
    print(f"    · en papelera (trash):     {summary['to_trash']}")
    print(f"  Reembolsos ya cumplidos (se quedan, marcados): {summary['to_refund_kept']}")
    print(f"  Sin cambios: {summary['unchanged']}")
    if summary.get("errors"):
        print(f"  ⚠ {len(summary['errors'])} errores (tiendas no consultables):")
        for e in summary["errors"][:10]:
            print(f"      {e}")
    if summary.get("capped"):
        print("  ⚠ Se alcanzó el tope de páginas en alguna tienda: vuelve a ejecutar.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="(por defecto) solo cuenta, no escribe")
    mode.add_argument("--apply", action="store_true",
                      help="aplica los cambios de estado de verdad")
    p.add_argument("--store", default=None, help="slug de una tienda (opcional)")
    p.add_argument("--yes", action="store_true",
                   help="no preguntar confirmación al aplicar")
    args = p.parse_args()

    apply = args.apply and not args.dry_run
    if apply and not args.yes:
        print("Vas a APLICAR cambios de estado (sacar del seguimiento / marcar "
              "reembolsos). Primero la previsualización:")
        preview = _run(dry_run=True, store=args.store)
        _print_summary(preview, applied=False)
        resp = input("\n¿Aplicar estos cambios? [escribe 'si']: ").strip().lower()
        if resp not in ("si", "sí", "s", "yes", "y"):
            print("Cancelado. No se ha cambiado nada.")
            sys.exit(0)

    summary = _run(dry_run=not apply, store=args.store)
    _print_summary(summary, applied=apply)


if __name__ == "__main__":
    main()
