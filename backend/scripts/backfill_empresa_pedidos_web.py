"""Backfill: empresa CRM de los pedidos WEB que se quedaron sin ella.

Los pedidos web (FesteWeb) crean/reutilizan el cliente en FACTUSOL (F_CLI), pero
BoHub solo sabía resolver la empresa del CRM cuando el *billing* de Woo traía el
NIF. Cuando no lo traía, el pedido quedaba con `company_id` vacío (~26 % de los
pedidos web en producción).

Este comando recorre los pedidos WEB sin empresa y la resuelve por el camino
fiable, el mismo que usa ya la importación:

    pedido web → REFPCL (prefijo de tienda + nº Woo) → F_PCL.CLIPCL → F_CLI

Con el NIF de ese cliente busca la empresa en el CRM por clave canónica (sin
separadores y sin el prefijo de país de la UE: `FR91523447399` ≡ `91523447399`).
Si existe, la vincula (y le rellena su CODCLI si estaba vacío); si no existe, la
crea a partir del F_CLI, ya enlazada.

Los pedidos que NO se pueden resolver (sin F_PCL en FACTUSOL, cliente sin NIF…)
se LISTAN para revisión manual: nunca se inventa una empresa sin datos fiscales.

FACTUSOL SIEMPRE solo lectura: este comando solo escribe en el CRM (empresa,
su vínculo y `orders.company_id`).

Uso (VPS):

    # DRY-RUN (por defecto): no escribe nada. Enseña qué haría y qué no puede.
    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.backfill_empresa_pedidos_web
    # aplicar de verdad (exige teclear APLICAR o pasar --yes):
    ... python -m scripts.backfill_empresa_pedidos_web --apply

Opciones: --apply · --yes · --limit N · --order NUM (un solo pedido) ·
--csv RUTA (informe).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

DEFAULT_CSV = "/tmp/backfill_empresa_pedidos_web.csv"
CONFIRM_WORD = "APLICAR"

#: Motivos legibles para el informe.
REASON_LABELS = {
    "sin_pedido_en_factusol": "no se encuentra su pedido (F_PCL) en FACTUSOL",
    "sin_cliente_en_factusol": "el cliente (F_CLI) no se pudo leer",
    "cliente_sin_nif": "el cliente de FACTUSOL no tiene NIF (no se crea a ciegas)",
    "factusol_no_disponible": "FACTUSOL no disponible (credenciales/conexión)",
    "codcli_vinculado_a_otra_ficha": "su CODCLI ya está vinculado a otra ficha",
}


def _rows_without_company(session: Session, *, order_number: str | None, limit: int | None):
    from app.erp.models import Order, OrderSource

    stmt = (
        select(Order)
        .where(
            Order.external_source == OrderSource.WOOCOMMERCE.value,
            Order.company_id.is_(None),
        )
        .order_by(Order.placed_at.desc())
    )
    if order_number:
        stmt = stmt.where(Order.order_number == order_number)
    if limit:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def _write_csv(rows: list[dict], path: str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["order_number", "resultado", "codcli", "empresa", "motivo"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="vincula/crea de verdad; exige teclear APLICAR o --yes")
    parser.add_argument("--yes", action="store_true",
                        help="salta la confirmación tecleada del --apply (no interactivo)")
    parser.add_argument("--limit", type=int, default=None,
                        help="procesa como mucho N pedidos (pruebas)")
    parser.add_argument("--order", default=None, metavar="NUM",
                        help="procesa SOLO ese número de pedido (p. ej. ARTISJ-9572)")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="ruta del informe CSV")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.web_order_company import (
        ensure_web_order_company,
        factusol_client_and_ejercicio,
    )

    with Session(get_engine()) as session:
        orders = _rows_without_company(
            session, order_number=args.order, limit=args.limit)
        print(f"Pedidos WEB sin empresa: {len(orders)}")
        if not orders:
            print("Nada que hacer.")
            return 0

        pair = factusol_client_and_ejercicio(session)
        if pair is None:
            print("\n✗ FACTUSOL no disponible (credenciales/conexión): sin él no se "
                  "puede resolver el cliente de ningún pedido. No se ha tocado nada.")
            return 2
        client, ejercicio = pair
        print(f"FACTUSOL OK (ejercicio {ejercicio}) — solo LECTURA de F_PCL/F_CLI.\n")

        report: list[dict] = []
        resolved = created = linked_company = 0
        for order in orders:
            result = ensure_web_order_company(
                session, order, client=client, ejercicio=ejercicio,
            )
            if result.resolved:
                resolved += 1
                created += 1 if result.created else 0
                linked_company += 1 if result.linked_company else 0
                accion = "CREAR empresa" if result.created else "vincular"
                print(f"  ✓ {order.order_number}: {accion} «{result.company.name}» "
                      f"(F_CLI {result.codcli})")
                report.append({
                    "order_number": order.order_number,
                    "resultado": "creada" if result.created else "vinculada",
                    "codcli": result.codcli or "",
                    "empresa": result.company.name,
                    "motivo": "",
                })
            else:
                motivo = REASON_LABELS.get(result.reason or "", result.reason or "?")
                print(f"  · {order.order_number}: SIN empresa — {motivo}")
                report.append({
                    "order_number": order.order_number,
                    "resultado": "a_revisar",
                    "codcli": result.codcli or "",
                    "empresa": "",
                    "motivo": motivo,
                })

        pendientes = len(orders) - resolved
        print(f"\nResumen: {resolved} resueltos "
              f"({created} empresas nuevas, {linked_company} vínculos rellenados) · "
              f"{pendientes} a revisar a mano")
        csv_path = _write_csv(report, args.csv)
        print(f"Informe: {csv_path}")

        if not args.apply:
            session.rollback()
            print("\nDRY-RUN: no se ha escrito nada. Cuando lo tengas claro:")
            print("  python -m scripts.backfill_empresa_pedidos_web --apply")
            return 0

        if not args.yes:
            print("\n⚠  Vas a crear/vincular empresas y rellenar `orders.company_id`.")
            try:
                answer = input(f"   Escribe {CONFIRM_WORD} para confirmar: ").strip()
            except EOFError:
                answer = ""
            if answer != CONFIRM_WORD:
                session.rollback()
                print("Confirmación incorrecta. No se ha tocado nada.")
                return 2

        session.commit()
        print(f"\n✓ Aplicado: {resolved} pedidos con empresa.")
        if pendientes:
            print(f"  Quedan {pendientes} para revisar a mano (ver el CSV).")
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
