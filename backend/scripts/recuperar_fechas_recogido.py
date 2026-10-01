"""Recuperar las «Fecha recogido» perdidas en el deduplicado de la hoja del 24/09.

Al deduplicar «Seguimiento (app)» se quitó la fila del histórico de cada pedido
que ya salía arriba, sin fusionar: sus fechas de recogida se perdieron de la
hoja. La fila original sigue en `seguimiento_legacy.raw_json`. Este script la
compara con el pedido de hoy y, SOLO si el pedido no tiene fecha, la guarda en
él (con rastro en la auditoría). Nunca pisa una fecha que ya exista.

    # 1) Informe, SIN escribir (por defecto): la lista de pedidos afectados.
    python -m scripts.recuperar_fechas_recogido --probar

    # 2) Guardar las fechas de la lista (solo las que siguen vacías).
    python -m scripts.recuperar_fechas_recogido --apply

Después, «Actualizar hoja de Drive…» (o la siguiente pasada del bucle) las
pinta. No toca FACTUSOL ni escribe la hoja.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session

from app.db.session import get_engine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--probar", action="store_true",
                      help="solo informa (por defecto): no escribe nada")
    modo.add_argument("--apply", action="store_true",
                      help="guarda las fechas de la lista (solo si el pedido no tiene)")
    args = parser.parse_args()

    from app.erp.api.seguimiento import (  # noqa: PLC0415
        drive_completados_rows,
        drive_live_rows,
    )
    from app.erp.seguimiento_recogido import (  # noqa: PLC0415
        IGUAL,
        NO_ES_FECHA,
        RELLENAR,
        YA_TIENE,
        aplicar,
        planificar,
    )

    with Session(get_engine()) as session:
        pintados = {str(r.get("id")) for r in (*drive_live_rows(session),
                                                *drive_completados_rows(session))
                    if r.get("id")}
        casos = planificar(session, pintados)
        por_accion = {a: [c for c in casos if c.accion == a]
                      for a in (RELLENAR, YA_TIENE, IGUAL, NO_ES_FECHA)}
        print(f"Pedidos que salen arriba con fecha de recogida en su fila original del "
              f"histórico: {len(casos)}")
        print(f"\nSe recuperará la fecha (el pedido no tiene): {len(por_accion[RELLENAR])}")
        for c in por_accion[RELLENAR]:
            print(f"  {c.order_number}: {c.fecha_hoja}")
        print(f"\nNO se toca (el pedido ya tiene otra fecha; se queda la suya): "
              f"{len(por_accion[YA_TIENE])}")
        for c in por_accion[YA_TIENE]:
            print(f"  {c.order_number}: hoja {c.fecha_hoja} · pedido {c.fecha_pedido}")
        print(f"\nYa coinciden: {len(por_accion[IGUAL])}")
        if por_accion[NO_ES_FECHA]:
            print(f"\nNO se entiende como fecha (no se inventa): "
                  f"{len(por_accion[NO_ES_FECHA])}")
            for c in por_accion[NO_ES_FECHA]:
                print(f"  {c.order_number}: «{c.fecha_hoja}»")

        if not args.apply:
            print("\nNada escrito. Para guardarlas: --apply.")
            return 0
        try:
            hechos = aplicar(session, casos, actor_email="script:recuperar_fechas_recogido")
            session.commit()
        except Exception as exc:  # noqa: BLE001 — se informa y no queda nada a medias
            session.rollback()
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    print(f"\n✔ {len(hechos)} fechas de recogida guardadas (con rastro en la auditoría). "
          "Ahora «Actualizar hoja de Drive…» para verlas en la hoja.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
