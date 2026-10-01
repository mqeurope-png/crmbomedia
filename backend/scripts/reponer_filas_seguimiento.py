"""Reponer en «Seguimiento (app)» las filas de pedidos que desaparecieron de la
hoja sin estar completados (bug del 01/10/2026: entregado no es completado).

Con la regla corregida, un pedido sigue en la zona viva hasta que se marca
completado (o se quita, se anula o se marca gestionado fuera), así que esas
filas vuelven solas en la siguiente sincronización. Este script deja verlo
ANTES de escribir:

    # 1) Informe, SIN escribir (por defecto): qué pedidos vivos o completados
    #    no están en la hoja y volverán con la siguiente pasada (marcando los
    #    entregados sin completar), y el resumen de la vista previa.
    python -m scripts.reponer_filas_seguimiento --probar

    # 2) La pasada de verdad (la misma que «Actualizar hoja de Drive», bajo el
    #    mismo cerrojo): repone las filas.
    python -m scripts.reponer_filas_seguimiento --apply

No toca FACTUSOL ni cambia ningún pedido: solo escribe la hoja, como el botón.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.erp.drive_sheets import (
    DriveConfigError,
    DriveSyncError,
    GoogleSheetsClient,
    drive_config,
)
from app.erp.models import ErpSettings
from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID


def _ids_en_hoja(valores: list[list[Any]]) -> tuple[set[str], set[str]]:
    """(ids en la zona viva, ids en el histórico) de la pestaña."""
    from app.erp.drive_managed import _texto, is_separator, realinear_pestana  # noqa: PLC0415
    from app.erp.seguimiento import ID_INDEX  # noqa: PLC0415

    vivos: set[str] = set()
    historico: set[str] = set()
    zona = vivos
    for f in realinear_pestana(valores):
        if is_separator(list(f)):
            zona = historico
            continue
        if len(f) > ID_INDEX and _texto(f[ID_INDEX]):
            zona.add(_texto(f[ID_INDEX]))
    return vivos, historico


def _linea(r: dict[str, Any]) -> str:
    from app.erp.seguimiento import ENVIO_ENTREGADO  # noqa: PLC0415

    entregado = r.get("envio") == ENVIO_ENTREGADO and not r.get("completado")
    marca = "  ← entregado sin completar" if entregado else ""
    return (f"  {r.get('order_number') or r.get('id')}: {r.get('situacion_label') or '—'} · "
            f"Envío {r.get('envio') or '—'} · Courier {r.get('courier') or '—'}{marca}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--probar", action="store_true",
                      help="solo informa (por defecto): no escribe nada")
    modo.add_argument("--apply", action="store_true",
                      help="hace la pasada de verdad y repone las filas")
    args = parser.parse_args()

    from app.erp.api.seguimiento import (  # noqa: PLC0415
        drive_completados_rows,
        drive_live_rows,
    )
    from app.erp.drive_managed import managed_tab_titles, push_managed_tabs  # noqa: PLC0415

    with Session(get_engine()) as session:
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
        client = GoogleSheetsClient(info, spreadsheet_id)

        vivos = drive_live_rows(session)
        completados = drive_completados_rows(session)
        en_viva, en_historico = (
            _ids_en_hoja(client.tab_values(pedidos_tab, raw=True))
            if pedidos_tab in client.tab_titles() else (set(), set())
        )
        en_hoja = en_viva | en_historico
        faltan_vivos = [r for r in vivos if str(r.get("id")) not in en_hoja]
        suben = [r for r in vivos if str(r.get("id")) in en_historico - en_viva]
        faltan_compl = [r for r in completados if str(r.get("id")) not in en_hoja]
        print(f"«{pedidos_tab}»: {len(vivos)} pedidos vivos y {len(completados)} "
              "completados en BoHub.")
        print(f"Vivos que NO están en la hoja (volverán a la zona viva): {len(faltan_vivos)}")
        for r in faltan_vivos:
            print(_linea(r))
        print("Vivos que hoy solo están en el HISTÓRICO (subirán a la zona viva hasta que "
              f"se marquen completados): {len(suben)}")
        for r in suben:
            print(_linea(r))
        print(f"Completados que NO están en la hoja (irán al histórico): {len(faltan_compl)}")
        for r in faltan_compl:
            print(_linea(r))

        if not args.apply:
            try:
                previa = push_managed_tabs(
                    session, client, vivos, completados=completados, dry_run=True,
                )
            except DriveSyncError as exc:
                print(f"ERROR en la vista previa: {exc}", file=sys.stderr)
                return 1
            finally:
                session.rollback()
            esp = previa.get("espejo") or {}
            print(f"\nVista previa: {previa['rows']} filas de BoHub en la zona viva, "
                  f"{previa.get('manuales', 0)} a mano, "
                  f"{previa.get('completados_historico', 0)} completados, "
                  f"{previa.get('historico_preservado', 0)} del histórico "
                  f"({esp.get('historico_duplicados_suprimidos', 0)} quitadas del histórico "
                  "porque el pedido sale arriba).")
            if esp.get("filas_rescatadas"):
                print("AVISO: filas que se conservarían por la salvaguarda: "
                      + ", ".join(esp["filas_rescatadas"]))
            print("Nada escrito. Para reponerlas: --apply (o «Actualizar hoja de Drive…»).")
            return 0

    from app.erp.seguimiento_sync_job import run_reconcile  # noqa: PLC0415

    with Session(get_engine()) as session:
        resumen = run_reconcile(session, force=True)
    if resumen is None:
        print("ERROR: la pasada no se ha hecho (otra en curso, modo antiguo o Drive sin "
              "configurar; mira el log).", file=sys.stderr)
        return 1
    esp = resumen.get("espejo") or {}
    print(f"✔ Hoja escrita: {resumen['rows']} filas de BoHub en la zona viva, "
          f"{resumen.get('completados_historico', 0)} completados.")
    if esp.get("filas_rescatadas"):
        print(f"AVISO: filas conservadas por la salvaguarda: {', '.join(esp['filas_rescatadas'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
