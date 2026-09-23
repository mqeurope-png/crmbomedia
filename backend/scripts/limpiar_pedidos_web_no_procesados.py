"""ERP · WooCommerce — BORRAR los pedidos web que nunca entraron en el flujo.

Los `pending`, `on-hold`, `failed`, `draft` y `checkout-draft` que el sync
antiguo creó son carritos sin pagar, no pedidos. Este comando los borra de
verdad (pedido + líneas + historial + excepciones + envíos + fila de Drive),
sin dejar huérfanos. NUNCA toca un pedido con factura, albarán, cobro, nº de
serie, tracking, WhiteRIP, excepción/tarea SAT o ya en preparación (se listan
aparte con su motivo), ni un `refunded`/`completed`/`processing`/`cancelled`,
ni un manual, ni una muestra. Idempotente: tras `--apply`, la siguiente pasada
da 0.

Uso (dentro del contenedor `api`):

    python -m scripts.limpiar_pedidos_web_no_procesados                 # dry-run
    python -m scripts.limpiar_pedidos_web_no_procesados --apply         # borra (confirma)
    python -m scripts.limpiar_pedidos_web_no_procesados --apply --yes   # sin preguntar
    python -m scripts.limpiar_pedidos_web_no_procesados --store boprint # una tienda

HAZ COPIA DE SEGURIDAD DE LA BASE DE DATOS ANTES DE `--apply`: el borrado no
tiene vuelta atrás.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

#: Lo que hay que teclear para confirmar el borrado (sin `--yes`).
CONFIRM_WORD = "BORRAR"


def _run(dry_run: bool, store: str | None) -> dict[str, Any]:
    from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.woocommerce.purge import (  # noqa: PLC0415
        purge_unprocessed_web_orders,
    )

    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    with factory() as session:
        return purge_unprocessed_web_orders(
            session, dry_run=dry_run, store_account_id=store,
        )


def _print_summary(summary: dict[str, Any], *, applied: bool) -> None:
    if not summary.get("ok"):
        print(f"ERROR: {summary.get('error')}")
        return
    print(f"\n{'APLICADO' if applied else 'PREVISUALIZACIÓN (dry-run)'}")
    verbo = "Borrados" if applied else "Se borrarían"
    print(f"  {verbo} (web sin pasar por caja y sin huella fiscal): "
          f"{len(summary['candidates'])}  ·  importe total {summary['total_importe']:.2f}")
    for st, n in summary["por_estado"].items():
        print(f"    · {st:<16} {n}")
    if summary["candidates"]:
        print(f"\n  {'PEDIDO':<16} {'ESTADO WOO':<16} {'IMPORTE':>10}  CLIENTE")
        for c in summary["candidates"]:
            print(f"  {c['order_number']:<16} {c['woo_status'] or '':<16} "
                  f"{c['importe']:>10.2f}  {c['cliente']}")
    print(f"\n  Protegidos (NO se tocan, tienen huella): {len(summary['protected'])}")
    for motivo, n in summary["protegidos_por_motivo"].items():
        print(f"    · {motivo:<28} {n}")
    for p in summary["protected"][:30]:
        print(f"      {p['order_number']:<16} {p['woo_status'] or '':<16} "
              f"{p['importe']:>10.2f}  {p['cliente']}  ← {', '.join(p['motivos'])}")
    if len(summary["protected"]) > 30:
        print(f"      … y {len(summary['protected']) - 30} más")
    if applied:
        filas = summary.get("deleted_rows") or {}
        detalle = ", ".join(f"{t}: {n}" for t, n in sorted(filas.items()))
        print(f"\n  Borrados {summary['deleted']} pedidos"
              + (f" (filas por tabla — {detalle})" if detalle else "") + ".")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="(por defecto) solo lista y cuenta, no borra")
    mode.add_argument("--apply", action="store_true",
                      help="BORRA definitivamente los candidatos")
    p.add_argument("--store", default=None, help="slug de una tienda (opcional)")
    p.add_argument("--yes", action="store_true",
                   help="no pedir confirmación al borrar")
    args = p.parse_args()

    apply = args.apply and not args.dry_run
    if apply and not args.yes:
        preview = _run(True, args.store)
        _print_summary(preview, applied=False)
        if not preview.get("ok") or not preview["candidates"]:
            print("\nNada que borrar.")
            return
        print("\n⚠ Esto BORRA definitivamente los pedidos de arriba (y sus líneas, "
              "historial, excepciones, envíos y fila de Drive). No tiene vuelta atrás.")
        print("  Haz antes copia de seguridad de la base de datos "
              "(mysqldump del esquema entero).")
        resp = input(f"  Escribe {CONFIRM_WORD} para continuar: ").strip()
        if resp != CONFIRM_WORD:
            print("Cancelado. No se ha borrado nada.")
            sys.exit(1)

    summary = _run(not apply, args.store)
    _print_summary(summary, applied=apply)
    if not summary.get("ok"):
        sys.exit(2)
    if not apply:
        print("\nNada borrado (dry-run). Repite con --apply para borrar "
              "(haz copia de seguridad antes).")


if __name__ == "__main__":
    main()
