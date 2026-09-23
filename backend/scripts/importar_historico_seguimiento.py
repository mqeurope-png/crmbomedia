"""Convierte el histórico de la hoja de Drive al formato NUEVO (una sola vez).

Lee la pestaña histórica en bruto, descarta lo que no es un pedido (el bloque
«^^^^ Aquí arriba pedidos que faltan…», cabeceras repetidas, separadores y
filas vacías), mapea cada fila a las 17 columnas del rediseño 2026 y la deja en
una pestaña APARTE («Histórico (formato nuevo)»).

La hoja vieja NO se toca: se lee y ya. Queda como archivo en bruto por si hay
que revisar algo del mapeo.

    # ver qué haría, sin escribir (por defecto)
    python -m scripts.importar_historico_seguimiento

    # con el detalle de las filas dudosas y una muestra del mapeo
    python -m scripts.importar_historico_seguimiento --verbose

    # escribir de verdad
    python -m scripts.importar_historico_seguimiento --apply

Necesita `INTEGRATION_SECRETS_KEY` (las credenciales de la cuenta de servicio
están cifradas en la BD) y que la hoja esté compartida con su `client_email`
como EDITOR — crear una pestaña no se puede solo con lectura.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.erp.drive_historico import LEGACY_HISTORICO_TAB, import_historico
from app.erp.drive_managed import managed_tab_titles
from app.erp.drive_sheets import (
    DriveConfigError,
    DriveSyncError,
    GoogleSheetsClient,
    drive_config,
)
from app.erp.models import ErpSettings
from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID


def _print_resumen(resumen: dict, *, verbose: bool) -> None:
    print(f"Origen (pestaña histórica): «{resumen['origen']}»  ·  "
          f"cabecera en la fila {resumen['header_row']}")
    marcador = resumen.get("marcador_fila")
    print("Marcador «^^^^»: "
          + (f"fila {marcador}" if marcador
             else "NO encontrado — todo se trata como histórico"))
    print(f"Destino histórico:  «{resumen['tab']}» (zona bajo el separador)")
    print(f"Destino pendientes: «{resumen['incidencias_tab']}» (idem)")
    print()
    print("Columnas reconocidas en la hoja vieja "
          f"({len(resumen['columnas_reconocidas'])}):")
    print("  " + ", ".join(resumen["columnas_reconocidas"]))
    if resumen["columnas_ausentes"]:
        print("Columnas que la hoja NO trae (se dejan vacías):")
        print("  " + ", ".join(resumen["columnas_ausentes"]))
    print()
    print(f"Filas leídas:   {resumen['filas_leidas']}")
    print(f"  pendientes:   {resumen['pendientes']}  (ENCIMA del «^^^^» → "
          "Incidencias)")
    print(f"  históricas:   {resumen['mapeadas']}  (DEBAJO del «^^^^»)")
    print(f"  descartadas:  {resumen['descartadas']}  (bloque «^^^^», cabeceras "
          "repetidas, vacías)")
    print(f"  dudosas:      {resumen['dudosas']}  (sin nº de pedido; se importan "
          "igual, con su nota)")
    if verbose and resumen.get("dudosas_muestra"):
        print()
        print("Dudosas (primeras 20):")
        for d in resumen["dudosas_muestra"]:
            print(f"  fila {d['fila']}: cliente={d['cliente']!r} nota={d['nota']!r}")
    if verbose and resumen.get("pendientes_muestra"):
        print()
        print("Pendientes heredados (primeros 5):")
        for fila in resumen["pendientes_muestra"]:
            print("  " + " | ".join(str(v) for v in fila[:5]))
    if verbose and resumen.get("muestra"):
        print()
        print("Muestra del histórico (primeras 5 filas):")
        for fila in resumen["muestra"]:
            print("  " + " | ".join(str(v) for v in fila[:8]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="escribe de verdad (por defecto solo informa)")
    parser.add_argument("--verbose", action="store_true",
                        help="enseña las filas dudosas y una muestra del mapeo")
    parser.add_argument("--tab", default=None,
                        help="título de la pestaña de seguimiento (por defecto, "
                             "la configurada: «Seguimiento (app)»)")
    args = parser.parse_args()

    with Session(get_engine()) as session:
        cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
        try:
            conf = drive_config(cfg)
        except DriveConfigError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if conf is None:
            print("ERROR: falta la cuenta de servicio de Google o el ID de la "
                  "hoja en Configuración ERP.", file=sys.stderr)
            return 2
        info, spreadsheet_id = conf
        pedidos_tab, incidencias_tab = managed_tab_titles(session)
        if args.tab:
            pedidos_tab = args.tab

    client = GoogleSheetsClient(info, spreadsheet_id)
    try:
        resumen = import_historico(
            client, pedidos_tab=pedidos_tab, incidencias_tab=incidencias_tab,
            dry_run=not args.apply,
        )
    except DriveSyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    _print_resumen(resumen, verbose=args.verbose)
    print()
    if resumen["written"]:
        print(f"✔ Escrito en «{resumen['tab']}» y «{resumen['incidencias_tab']}». "
              "La hoja vieja no se ha tocado.")
        print(f"  Si te quedó una pestaña «{LEGACY_HISTORICO_TAB}» de la "
              "importación anterior, ya sobra: bórrala a mano.")
    else:
        print("Nada escrito (dry-run). Repite con --apply para escribirlo.")
        print("Haz copia de seguridad de la hoja antes de aplicar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
