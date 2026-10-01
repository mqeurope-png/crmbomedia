"""VUELTA ATRÁS de la columna «Courier» de «Seguimiento (app)» (paso 3 de
«Volver a la versión anterior» en docs/guia-erp-usuario.md).

Borra la columna O («Courier») con la cuenta de servicio de BoHub, que puede
hacerlo aunque la hoja sea suya o tenga las columnas protegidas (a mano, solo el
propietario podría). Antes comprueba que:

- la base de datos YA se ha bajado (`alembic downgrade 20260930_0122`): si no,
  la versión nueva volvería a insertar la columna en su siguiente pasada;
- la pestaña está en el formato de 20 columnas, bien colocada;
- nadie la está tocando (la relee justo antes de borrar).

    # 1) Informe, SIN escribir (por defecto)
    python -m scripts.quitar_columna_courier

    # 2) Borrar la columna O de verdad
    python -m scripts.quitar_columna_courier --apply

Se pierden las celdas de «Courier» (la versión anterior no las tiene). No toca
la base de datos ni ninguna otra columna.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.erp.drive_managed import managed_tab_titles, quitar_columna_courier
from app.erp.drive_sheets import (
    DriveConfigError,
    DriveSyncError,
    GoogleSheetsClient,
    drive_config,
)
from app.erp.models import ErpSettings
from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="borra la columna O de verdad (por defecto solo informa)")
    args = parser.parse_args()

    engine = get_engine()
    if inspect(engine).has_table("seguimiento_sync_meta"):
        print("ERROR: la base de datos aún tiene la tabla del espejo de esta versión. "
              "Primero baja la base de datos (`alembic downgrade 20260930_0122`) con la "
              "sincronización parada; si no, la versión nueva volvería a insertar la "
              "columna.", file=sys.stderr)
        return 2
    with Session(engine) as session:
        try:
            conf = drive_config(session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID))
        except DriveConfigError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if conf is None:
            print("ERROR: falta la cuenta de servicio de Google o el ID de la hoja en "
                  "Configuración ERP.", file=sys.stderr)
            return 2
        info, spreadsheet_id = conf
        pedidos_tab, _incidencias = managed_tab_titles(session)

    from app.erp.seguimiento_sync_job import reconcile_lock  # noqa: PLC0415

    client = GoogleSheetsClient(info, spreadsheet_id)
    with reconcile_lock() as cogido:
        if not cogido:
            print("ERROR: hay una sincronización con la hoja en curso; para la "
                  "sincronización y repite.", file=sys.stderr)
            return 1
        try:
            res = quitar_columna_courier(client, pedidos_tab, apply=args.apply)
        except DriveSyncError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    print(f"«{pedidos_tab}»: {res['filas']} filas; {res['celdas_courier']} celdas con "
          "dato en «Courier» (columna O).")
    if not res["aplicado"]:
        print("Nada escrito. Para borrar la columna O: --apply")
        return 0
    print(f"✔ Columna «Courier» quitada. Celdas con dato: {res['celdas_antes']} antes, "
          f"{res['celdas_despues']} después (las de «Courier» y su cabecera, fuera).")
    print("Ya puedes arrancar la versión anterior.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
