"""BoHub ERP · Lote 5 — BACKFILL: los pedidos YA pagados entran en la Cola SAT.

La auto-entrada del Lote 4 (`app.erp.sat_autoenqueue.enqueue_paid_order`) solo
actúa sobre pagos NUEVOS (webhook Woo, cobro manual, conversión FACTUSOL). Los
pedidos que ya estaban pagados ANTES de ese lote se quedaron en la pre-cola
(«pending_review») sin entrar en el taller. Este script los mete de una sola vez
en la Cola SAT («Por embalar»), REUTILIZANDO exactamente los mismos guards que
la auto-entrada: llama a `enqueue_paid_order`, que solo promueve
`pending_review` → `in_queue` y respeta anulados / completados / excluidos /
no-pagados y las salidas ya hechas. Es idempotente: una segunda pasada promueve 0.

Por defecto es un DRY-RUN: cuenta cuántos promovería (recorriendo el MISMO
helper sobre una transacción que se descarta) y no escribe nada. Con `--apply`
pide confirmación escrita (o `--yes`) y hace UN solo commit al final.

Uso (en producción, dentro del contenedor api):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.backfill_sat_pagados             # DRY-RUN (no escribe)
    ... --apply                       # aplica (pide escribir APLICAR)
    ... --apply --yes                 # aplica sin preguntar
    ... --sample 60                   # cuántas líneas por pedido enseñar
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session


def _value(v: object) -> object:
    return getattr(v, "value", v)


def select_candidates(session: Session) -> list:
    """Prefiltro TIGHT que refleja los guards de `enqueue_paid_order`.

    Pagado (paid / credit_approved / partial_paid) Y en la pre-cola
    (`pending_review`) Y no anulado / no completado / no excluido del
    seguimiento. Es una `select` filtrada (no carga la tabla entera)."""
    from sqlalchemy import select

    from app.erp.models.orders import Order, PreparationStatus
    from app.erp.state_machine.definitions import PAYMENT_OK_FOR_PREPARATION

    stmt = (
        select(Order)
        .where(
            Order.payment_status.in_(sorted(PAYMENT_OK_FOR_PREPARATION)),
            Order.preparation_status == PreparationStatus.PENDING_REVIEW.value,
            Order.cancelled_at.is_(None),
            Order.completed_at.is_(None),
            Order.seguimiento_excluded_at.is_(None),
        )
        .order_by(Order.placed_at)
    )
    return list(session.scalars(stmt))


def _fmt(order) -> str:
    placed = order.placed_at.isoformat() if order.placed_at is not None else "sin fecha"
    store = order.store_id or _value(order.external_source) or "—"
    return f"  {order.order_number}  [{store}]  {placed}"


def _print_candidates(candidates: list, *, sample: int) -> None:
    print(f"Candidatos (pagados y en la pre-cola «pending_review»): {len(candidates)}")
    for order in candidates[:sample]:
        print(_fmt(order))
    if len(candidates) > sample:
        print(f"  … y {len(candidates) - sample} más")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apply", action="store_true",
                        help="mete los pedidos en la Cola SAT (si no, dry-run)")
    parser.add_argument("--yes", action="store_true",
                        help="no pedir confirmación escrita del --apply")
    parser.add_argument("--sample", type=int, default=40,
                        help="cuántos pedidos enseñar en el listado")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.sat_autoenqueue import enqueue_paid_order

    with Session(get_engine()) as session:
        candidates = select_candidates(session)
        _print_candidates(candidates, sample=args.sample)

        if not candidates:
            print("\nNo hay pedidos pagados en la pre-cola: nada que hacer.")
            return 0

        if not args.apply:
            # DRY-RUN: cuenta REAL reutilizando el helper sobre una transacción
            # que se descarta, para que el número no pueda diverger del --apply.
            would = sum(1 for o in candidates if enqueue_paid_order(session, o))
            session.rollback()
            skipped = len(candidates) - would
            print(f"\nDRY-RUN: se promoverían {would} de {len(candidates)} pedidos "
                  "a «Por embalar» (in_queue). No se ha escrito nada.")
            if skipped:
                print(f"  ({skipped} quedarían fuera: algún guard los frena tras el "
                      "prefiltro.)")
            print("Con --apply (te pedirá escribir APLICAR) se aplica.")
            return 0

        if not args.yes:
            answer = input(
                f"\nEscribe APLICAR para meter {len(candidates)} pedidos "
                "en la Cola SAT: "
            )
            if answer.strip() != "APLICAR":
                print("Cancelado: no se ha escrito nada.")
                return 1

        promoted = sum(1 for o in candidates if enqueue_paid_order(session, o))
        session.commit()

    skipped = len(candidates) - promoted
    print(f"\nHecho: {promoted} pedidos promovidos a «Por embalar» (in_queue).")
    print(f"Candidatos: {len(candidates)} · promovidos: {promoted} · omitidos: {skipped}")
    if skipped:
        print("  (los omitidos los frenó un guard de enqueue_paid_order tras el "
              "prefiltro.)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
