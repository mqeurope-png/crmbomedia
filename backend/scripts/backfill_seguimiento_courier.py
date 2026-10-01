"""Backfill de la columna «Courier» de Seguimiento (y del nuevo «Envío»).

Courier y Envío salen del envío de cada pedido al leerlo (no se guardan), así
que TODOS los pedidos ya los tienen y la hoja de Drive los recibe en la
siguiente sincronización («Actualizar hoja de Drive…» o el bucle automático).
Este script solo sirve para:

    # 1) Ver el informe, SIN escribir (por defecto): cuántos pedidos hay de cada
    #    tipo (Genei con / sin agencia, otro courier, sin envío) y cómo queda
    #    Envío; y los Nº de los envíos Genei que no tienen su agencia guardada.
    python -m scripts.backfill_seguimiento_courier

    # 2) Pedir a Genei la agencia de esos envíos y guardarla (uno a uno, con una
    #    pausa entre consultas). Solo LEE de Genei: no crea, paga ni cancela.
    python -m scripts.backfill_seguimiento_courier --apply [--limit 50]

Sin agencia guardada, el Courier de un envío Genei sale «Genei» (no «—»).
Nunca escribe en la hoja de Drive ni en FACTUSOL, y no imprime credenciales.
"""

from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.erp.seguimiento_courier_backfill import informe, rellenar_agencias_genei


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="pide a Genei la agencia de los envíos que no la tienen y la guarda")
    parser.add_argument("--limit", type=int, default=None,
                        help="como mucho tantos envíos en esta pasada")
    parser.add_argument("--pausa", type=float, default=0.5,
                        help="segundos entre consulta y consulta a Genei (0.5)")
    args = parser.parse_args()

    with Session(get_engine()) as session:
        rep = informe(session)
        print(f"Pedidos: {rep['pedidos']}")
        print("Por tipo de envío:")
        for tipo, n in sorted(rep["por_tipo"].items()):
            print(f"  {tipo}: {n}")
        print("Envío (lista cerrada):")
        for valor, n in sorted(rep["por_envio"].items(), key=lambda kv: -kv[1]):
            print(f"  {valor}: {n}")
        sin = rep["genei_sin_agencia"]
        print(f"Envíos Genei sin agencia guardada: {len(sin)}")
        for numero in sin[:50]:
            print(f"  {numero}")
        if len(sin) > 50:
            print(f"  … y {len(sin) - 50} más")
        if not args.apply or not sin:
            if sin:
                print("\nNada escrito. Para pedirlas a Genei: --apply")
            return 0

        from app.erp.api.genei import build_client, get_genei_carrier  # noqa: PLC0415
        from app.erp.integrations.genei.client import GeneiError  # noqa: PLC0415

        carrier = get_genei_carrier(session)
        if carrier is None or not carrier.api_credentials_encrypted:
            print("ERROR: Genei no está configurado (Ajustes → Genei).", file=sys.stderr)
            return 2
        try:
            client = build_client(carrier)
        except GeneiError as exc:
            print(f"ERROR: no se puede conectar con Genei: {exc}", file=sys.stderr)
            return 2
        res = rellenar_agencias_genei(
            session, client, apply=True, limit=args.limit,
            pausa=lambda: time.sleep(max(args.pausa, 0.0)),
        )
        print(f"\nRellenadas: {res['rellenados']} de {res['pendientes']} · "
              f"Genei sin agencia: {res['sin_agencia_en_genei']} · errores: {res['errores']}")
        if res.get("cortado"):
            print(f"Cortado: {res['cortado']}", file=sys.stderr)
        for d in res["detalle"]:
            print(f"  {d['pedido']}: {d.get('agencia') or d.get('error') or 'sin agencia'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
