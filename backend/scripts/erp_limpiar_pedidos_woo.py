"""ERP · WooCommerce — limpieza de los pedidos importados que NO están en `processing`.

Con la regla nueva BoHub solo crea pedidos en `processing`. Este comando localiza
los que ya entraron con la regla antigua (`pending`, `on-hold`, `cancelled`…) y,
tras confirmación, los EXCLUYE del seguimiento (reversible con «Reincluir»; no
borra nada). NUNCA toca un pedido facturado, cobrado, con albarán/envío, con
excepción/tarea SAT, escrito en Drive o ya en preparación: esos se listan aparte.
Los pedidos sin `woo_status` no se tocan (reconciliar primero).

Uso (dentro del contenedor `api`):

    python -m scripts.erp_limpiar_pedidos_woo                  # dry-run: lista y cuenta
    python -m scripts.erp_limpiar_pedidos_woo --apply          # excluye (pide confirmación)
    python -m scripts.erp_limpiar_pedidos_woo --apply --yes    # excluye sin preguntar
    python -m scripts.erp_limpiar_pedidos_woo --store boprint  # solo una tienda
"""
from __future__ import annotations

import argparse
import sys
from typing import Any


def _run(dry_run: bool, store: str | None) -> dict[str, Any]:
    from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.woocommerce.cleanup import (  # noqa: PLC0415
        cleanup_non_processing_orders,
    )

    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    with factory() as session:
        return cleanup_non_processing_orders(
            session, dry_run=dry_run, store_account_id=store,
        )


def _print_summary(summary: dict[str, Any], *, applied: bool) -> None:
    if not summary.get("ok"):
        print(f"ERROR: {summary.get('error')}")
        return
    print(f"\n{'APLICADO' if applied else 'PREVISUALIZACIÓN (dry-run)'}")
    print(f"  Candidatos a excluir (no processing/completed, sin nada aguas abajo): "
          f"{len(summary['candidates'])}")
    for st, n in summary["por_estado"].items():
        print(f"    · {st:<12} {n}")
    if summary["candidates"]:
        print(f"\n  {'PEDIDO':<16} {'ESTADO WOO':<12} {'IMPORTE':>10}  CLIENTE")
        for c in summary["candidates"]:
            print(f"  {c['order_number']:<16} {c['woo_status'] or '':<12} "
                  f"{c['importe']:>10.2f}  {c['cliente']}")
    print(f"\n  Protegidos (NO se tocan, tienen algo aguas abajo): {len(summary['protected'])}")
    for motivo, n in summary["protegidos_por_motivo"].items():
        print(f"    · {motivo:<28} {n}")
    for p in summary["protected"][:30]:
        print(f"      {p['order_number']:<16} {p['woo_status'] or '':<12} "
              f"{p['importe']:>10.2f}  {p['cliente']}  ← {', '.join(p['motivos'])}")
    if len(summary["protected"]) > 30:
        print(f"      … y {len(summary['protected']) - 30} más")
    print(f"  Sin estado Woo (no se tocan; reconciliar primero): {summary['sin_estado']}")
    if applied:
        print(f"\n  Excluidos del seguimiento: {summary['excluded']} "
              "(reversible con «Reincluir» en el seguimiento).")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="(por defecto) solo lista y cuenta, no escribe")
    mode.add_argument("--apply", action="store_true",
                      help="excluye del seguimiento los candidatos (reversible)")
    p.add_argument("--store", default=None, help="slug de una tienda (opcional)")
    p.add_argument("--yes", action="store_true",
                   help="no preguntar confirmación al aplicar")
    args = p.parse_args()

    apply = args.apply and not args.dry_run
    if apply and not args.yes:
        print("Vas a EXCLUIR del seguimiento los pedidos Woo que no están en "
              "processing/completed. Primero la previsualización:")
        preview = _run(dry_run=True, store=args.store)
        _print_summary(preview, applied=False)
        if not preview.get("ok") or not preview["candidates"]:
            print("\nNada que excluir.")
            sys.exit(0)
        resp = input("\n¿Excluir estos pedidos? [escribe 'si']: ").strip().lower()
        if resp not in ("si", "sí", "s", "yes", "y"):
            print("Cancelado. No se ha cambiado nada.")
            sys.exit(0)

    summary = _run(dry_run=not apply, store=args.store)
    _print_summary(summary, applied=apply)


if __name__ == "__main__":
    main()
