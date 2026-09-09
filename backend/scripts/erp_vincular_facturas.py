"""ERP · CLI para vincular a los pedidos las facturas creadas a mano en FACTUSOL.

Vía de escape / diagnóstico: saca la previsualización (o aplica) sin pasar por la
cola ni el proxy. Reutiliza EXACTAMENTE el mismo núcleo que el job y el endpoint
(`reconcile_factusol_invoices`: índice por REFFAC × cruce con pedidos no
facturados); no duplica lógica. Solo escribe en BoHub; NUNCA en FACTUSOL.

Uso (dentro del contenedor `api`):

    python -m scripts.erp_vincular_facturas --dry-run       # (por defecto) solo lista
    python -m scripts.erp_vincular_facturas --apply         # enlaza (pide confirmación)
    python -m scripts.erp_vincular_facturas --apply --yes   # enlaza sin preguntar
"""
from __future__ import annotations

import argparse
import sys
from typing import Any


def _run(dry_run: bool) -> dict[str, Any]:
    from app.integrations.factusol.jobs import (  # noqa: PLC0415
        run_factusol_invoice_reconcile,
    )

    return run_factusol_invoice_reconcile(dry_run=dry_run)


def _print(summary: dict[str, Any], *, applied: bool) -> None:
    print(f"\n{'APLICADO' if applied else 'PREVISUALIZACIÓN (dry-run)'} — "
          f"{summary['scanned']} pedidos no facturados escaneados")
    print(f"  Facturas a enlazar: {len(summary['to_link'])}")
    for it in summary["to_link"]:
        extra = []
        if it.get("total") is not None:
            extra.append(f"{it['total']} €")
        if it.get("fecha"):
            extra.append(str(it["fecha"]))
        suffix = f" ({', '.join(extra)})" if extra else ""
        print(f"    · {it['order_number']} → {it['numero']}  [ref {it['ref']}]{suffix}")
    print(f"  Pedidos sin factura en FACTUSOL: {summary['no_match']}")
    if summary["conflicts"]:
        print(f"  ⚠ Conflictos (NO se enlazan; decide tú y anula la sobrante): "
              f"{len(summary['conflicts'])}")
        for c in summary["conflicts"]:
            nums = " · ".join(f.get("numero", "?") for f in c["facturas"])
            print(f"    · {c['order_number']} [ref {c['ref']}]: {nums}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(por defecto) solo lista")
    mode.add_argument("--apply", action="store_true", help="enlaza de verdad")
    p.add_argument("--yes", action="store_true", help="no preguntar al aplicar")
    args = p.parse_args()

    apply = args.apply and not args.dry_run
    if apply and not args.yes:
        print("Vas a ENLAZAR facturas (solo en BoHub; nada en FACTUSOL). "
              "Primero la previsualización:")
        _print(_run(dry_run=True), applied=False)
        resp = input("\n¿Enlazar estas facturas? [escribe 'si']: ").strip().lower()
        if resp not in ("si", "sí", "s", "yes", "y"):
            print("Cancelado. No se ha enlazado nada.")
            sys.exit(0)

    _print(_run(dry_run=not apply), applied=apply)


if __name__ == "__main__":
    main()
