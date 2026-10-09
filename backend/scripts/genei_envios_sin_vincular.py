"""Envíos que existen en Genei y en BoHub no están vinculados (**SOLO LECTURA**).

El taller crea envíos a mano en el panel de Genei cuando BoHub falla —y hace
bien, así no para la expedición—, pero BoHub no se enteraba: el pedido se
queda sin envío vinculado, sin etiqueta, sin seguimiento y sin aviso al
cliente. Esto los lista cruzando la referencia externa del envío
(`codigo_envio_externo`, que es el nº de pedido) con los pedidos de BoHub.

No toca nada: ni Genei, ni la base de BoHub. Lo que salga se vincula desde la
ficha del pedido con «Vincular envío existente de Genei», pegando el código.

Uso (VPS):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.genei_envios_sin_vincular

Opciones: --paginas N (páginas del listado de Genei que se miran, 20 por
defecto · 50 envíos cada una) · --csv RUTA.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

DEFAULT_CSV = "/tmp/genei_envios_sin_vincular.csv"
_FIELDS = ["order_id", "order_number", "codigo_envio", "estado", "tracking",
           "agencia", "fecha", "motivo"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--paginas", type=int, default=20,
                        help="páginas del listado de Genei a revisar (50 envíos cada una)")
    parser.add_argument("--csv", default=DEFAULT_CSV)
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.integrations.genei.client import GeneiClient
    from app.erp.integrations.genei.service import (
        external_code_of,
        shipment_code_of_order,
    )
    from app.erp.models import Order
    from app.erp.models.carriers import Carrier

    with Session(get_engine()) as session:
        carrier = session.scalar(select(Carrier).where(Carrier.code == "genei"))
        if carrier is None or not carrier.api_credentials_encrypted:
            print("Genei no está configurado (Ajustes → Envíos).")
            return 2
        client = GeneiClient.from_carrier(carrier)

        envios: dict[str, dict] = {}
        for page in range(1, args.paginas + 1):
            filas, total = client.list_shipments(page=page)
            for row in filas:
                ref = external_code_of(row).strip()
                if ref:
                    envios.setdefault(ref.upper(), row)
            if not filas or page * len(filas) >= (total or 0):
                break
        print(f"Envíos leídos de Genei: {len(envios)} con referencia externa.")

        sueltos: list[dict[str, str]] = []
        for ref, row in sorted(envios.items()):
            order = session.scalar(select(Order).where(Order.order_number == ref))
            if order is None:
                continue                      # referencia que no es un pedido de BoHub
            codigo = str(row.get("codigo_envio") or "").strip()
            vinculado = shipment_code_of_order(order)
            if vinculado and vinculado.strip().upper() == codigo.upper():
                continue                      # ya vinculado: nada que hacer
            sueltos.append({
                "order_id": order.id,
                "order_number": order.order_number or "",
                "codigo_envio": codigo,
                "estado": str(row.get("estado") or ""),
                "tracking": str(row.get("codigo_seguimiento") or ""),
                "agencia": str(row.get("nombre_agencia") or ""),
                "fecha": str(row.get("fecha_creacion") or row.get("fecha") or ""),
                "motivo": ("el pedido tiene vinculado OTRO envío "
                           f"({vinculado})" if vinculado else "el pedido no tiene envío"),
            })

        print(f"\nPedidos con envío en Genei y sin vincular en BoHub: {len(sueltos)}")
        for s in sueltos:
            print(f"  {s['order_number']}  envío {s['codigo_envio']}  "
                  f"{s['agencia']}  {s['tracking']}  → {s['motivo']}")
        ruta = _write_csv(sueltos, args.csv)
        print(f"\nCSV: {ruta}")
        print("SOLO LECTURA: no se ha tocado nada. Se vinculan desde la ficha "
              "del pedido con «Vincular envío existente de Genei».")
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
