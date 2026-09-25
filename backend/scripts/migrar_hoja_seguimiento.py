"""Pasa la hoja de seguimiento a una hoja cuya propietaria no es una persona, para
que las columnas bloqueadas bloqueen a TODAS las personas (Google nunca bloquea
al propietario). Detalle y orden de los pasos en `app.erp.drive_migracion`.

    # 1) informe, sin escribir nada: propietario, acceso, filas, ids
    python -m scripts.migrar_hoja_seguimiento

    # 2) ¿puede la cuenta de servicio ser propietaria? (crea una hoja vacía y
    #    la borra en el acto)
    python -m scripts.migrar_hoja_seguimiento --probar

    # 3a) si puede: BoHub crea la hoja nueva (propiedad de la cuenta de servicio)
    python -m scripts.migrar_hoja_seguimiento --crear            # plan
    python -m scripts.migrar_hoja_seguimiento --crear --apply    # migra

    # 3b) si no puede: una cuenta de Google DEDICADA (que no use nadie a diario)
    #     crea una hoja vacía y la comparte con la cuenta de servicio como Editor
    python -m scripts.migrar_hoja_seguimiento --destino ID_HOJA            # plan
    python -m scripts.migrar_hoja_seguimiento --destino ID_HOJA --apply    # migra

    # después: comprobar la hoja actual, o dar acceso a alguien más
    python -m scripts.migrar_hoja_seguimiento --verificar
    python -m scripts.migrar_hoja_seguimiento --compartir persona@bomedia.es

Opciones de la migración: `--editor EMAIL` (repetible) añade editores a los de la
hoja vieja. Necesita `INTEGRATION_SECRETS_KEY` y la **Google Drive API**
activada en el proyecto de la cuenta de servicio (además de la Sheets API).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.erp.drive_managed import managed_tab_titles
from app.erp.drive_migracion import (
    archivar_origen,
    compartir,
    migrar,
    plan_migracion,
    probar_propiedad,
    verificar_hoja,
)
from app.erp.drive_sheets import (
    DriveConfigError,
    DriveSyncError,
    GoogleDriveFiles,
    GoogleSheetsClient,
    create_spreadsheet,
    drive_config,
)
from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings


def _ayuda_drive_api(exc: DriveSyncError) -> str:
    texto = str(exc)
    if "Drive API" in texto and ("SERVICE_DISABLED" in texto or "has not been used" in texto
                                 or "accessNotConfigured" in texto):
        return (f"{texto}\n  → Activa la «Google Drive API» en el proyecto de la cuenta "
                "de servicio (Google Cloud → APIs y servicios → Biblioteca) y repite.")
    return texto


def _print_estado(titulo: str, e: dict[str, Any]) -> None:
    print(f"{titulo}: {e.get('nombre') or ''} ({e['id']})")
    print(f"  propietario: {', '.join(e['propietarios']) or '—'}"
          + ("  ← la cuenta de servicio" if e["sa_es_propietaria"] else ""))
    for p in e["personas"]:
        print(f"  · {p['email']}: {p['rol']}")
    if e["exentos"]:
        print(f"  se salta la protección (propietario): {', '.join(e['exentos'])}")


def _print_plan(plan: dict[str, Any]) -> None:
    _print_estado("Hoja actual", plan["origen"])
    print()
    for t, r in plan["resumen"].items():
        print(f"«{t}»: {r['vivas']} filas vivas · {r['historico']} del histórico · "
              f"{r['ids']} con id")
    if plan["otras_pestanas"]:
        print(f"Otras pestañas (se quedan en la vieja): {', '.join(plan['otras_pestanas'])}")
    bd = plan["bd"]
    print(f"En la base de datos (no dependen de la hoja): {bd['overrides']} overrides · "
          f"{bd['historico_bd']} del histórico · {bd['manuales_bd']} manuales · "
          f"{bd['foto']} en la foto")
    if plan.get("destino"):
        print()
        _print_estado("Hoja destino", plan["destino"])
    for a in plan["avisos"]:
        print(f"AVISO: {a}")
    for e in plan["errores"]:
        print(f"ERROR: {e}")


def _print_verificacion(v: dict[str, Any]) -> None:
    print(f"Propietario: {', '.join(v['propietarios'])}"
          + ("  ✔ cuenta de servicio" if v["sa_es_propietaria"] else ""))
    print(f"Rangos protegidos del espejo: {v['rangos_protegidos']}"
          + ("" if v["problemas"] else " (único editor: la cuenta de servicio)"))
    for p in v["problemas"]:
        print(f"  ✘ {p}")
    if v["bloqueados"]:
        print(f"Bloqueados en las columnas protegidas: {', '.join(v['bloqueados'])}")
    if v["exentos"]:
        print(f"Se la saltan (propietario): {', '.join(v['exentos'])}")
    if v.get("editores_pueden_compartir"):
        print("AVISO: los editores pueden cambiar el acceso de la hoja")
    print("✔ La protección bloquea a TODAS las personas." if v["ok"]
          else "✘ La protección NO bloquea a todas las personas (ver arriba).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--probar", action="store_true",
                      help="¿puede la cuenta de servicio ser propietaria? (crea y borra "
                           "una hoja vacía)")
    modo.add_argument("--crear", action="store_true",
                      help="migrar a una hoja NUEVA creada por la cuenta de servicio")
    modo.add_argument("--destino", metavar="ID",
                      help="migrar a una hoja ya creada por una cuenta dedicada")
    modo.add_argument("--verificar", action="store_true",
                      help="comprobar propietario y protección de la hoja actual")
    modo.add_argument("--compartir", metavar="EMAIL",
                      help="dar acceso a la hoja actual (por defecto, como editor)")
    parser.add_argument("--rol", default="writer", choices=["writer", "commenter", "reader"])
    parser.add_argument("--editor", action="append", default=[], metavar="EMAIL",
                        help="editor adicional para la hoja nueva (repetible)")
    parser.add_argument("--apply", action="store_true",
                        help="migrar de verdad (por defecto, solo el plan)")
    args = parser.parse_args()

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
        sa_email = str(info.get("client_email"))
        drive = GoogleDriveFiles(info)
        origen = GoogleSheetsClient(info, spreadsheet_id)
        print(f"Cuenta de servicio: {sa_email}")
        try:
            return _run(args, session, info, sa_email, drive, origen)
        except DriveSyncError as exc:
            session.rollback()
            print(f"ERROR: {_ayuda_drive_api(exc)}", file=sys.stderr)
            return 1


def _crear(info: dict[str, Any]):
    return lambda titulo, props: create_spreadsheet(
        info, titulo, locale=props.get("locale"), time_zone=props.get("timeZone"),
    )


def _run(args: argparse.Namespace, session: Session, info: dict[str, Any], sa_email: str,
         drive: GoogleDriveFiles, origen: GoogleSheetsClient) -> int:
    def abrir(file_id: str) -> GoogleSheetsClient:
        return GoogleSheetsClient(info, file_id)

    if args.probar:
        r = probar_propiedad(_crear(info), drive)
        if r["puede"]:
            print("✔ La cuenta de servicio PUEDE ser propietaria de una hoja: usa --crear."
                  + ("" if r["borrada"] else f" (No se pudo borrar la de prueba: {r['id']})"))
            return 0
        print(f"✘ La cuenta de servicio NO puede ser propietaria: {r['motivo']}")
        print("  → Crea la hoja con una cuenta de Google dedicada (que no use nadie a "
              f"diario), compártela con {sa_email} como Editor y usa --destino ID.")
        return 1
    if args.verificar:
        pedidos_tab, _ = managed_tab_titles(session)
        _print_verificacion(verificar_hoja(origen, drive, origen.spreadsheet_id, sa_email,
                                           pedidos_tab))
        return 0
    if args.compartir:
        compartir(drive, origen.spreadsheet_id, args.compartir, args.rol)
        print(f"✔ {args.compartir} tiene acceso ({args.rol}) a la hoja actual.")
        return 0

    plan = plan_migracion(session, origen=origen, drive=drive, sa_email=sa_email,
                          destino_id=args.destino, abrir=abrir)
    plan.pop("_valores", None)
    _print_plan(plan)
    if not (args.crear or args.destino):
        print("\nSiguiente paso: --probar (¿puede la cuenta de servicio ser propietaria?)")
        return 0
    if plan["errores"]:
        return 1
    if not args.apply:
        print("\nNada escrito (plan). Repite con --apply para migrar. Avisa al equipo de que "
              "no toque la hoja durante ese minuto.")
        return 0

    from app.erp.seguimiento_sync_job import reconcile_lock  # noqa: PLC0415

    with reconcile_lock() as cogido:
        if not cogido:
            print("ERROR: hay una sincronización con la hoja en curso; repite en un momento.",
                  file=sys.stderr)
            return 1
        res = migrar(
            session, origen=origen, drive=drive, sa_email=sa_email, abrir=abrir,
            crear_hoja=_crear(info) if args.crear else None, destino_id=args.destino,
            editores_extra=args.editor,
        )
        session.commit()
    archivo = archivar_origen(origen, drive, sa_email)

    nueva = res["destino_id"]
    print(f"\n✔ Migrada a https://docs.google.com/spreadsheets/d/{nueva}")
    print("  Copia idéntica, celda a celda, de: " + ", ".join(f"«{t}»" for t in res["pestanas"]))
    for t, r in res["resumen_nueva"].items():
        print(f"  «{t}» en la nueva: {r['vivas']} vivas · {r['historico']} del histórico · "
              f"{r['ids']} con id")
    antes, despues = res["bd"], res["bd_despues"]
    print(f"  Base de datos: overrides {antes['overrides']}→{despues['overrides']} · "
          f"histórico {antes['historico_bd']}→{despues['historico_bd']} · "
          f"manuales {antes['manuales_bd']}→{despues['manuales_bd']}")
    esp = res["espejo"]
    if esp.get("ediciones_leidas"):
        print(f"  Ediciones de la hoja vieja leídas de vuelta: {esp['ediciones_leidas']}")
    for c in res["compartidos"]:
        print(f"  Compartida con {c['email']} ({c['rol']})")
    print("  Configuración ERP re-apuntada a la hoja nueva.")
    print()
    _print_verificacion(res["verificacion"])
    print()
    print("Hoja vieja: " + ("renombrada «ARCHIVO · …»; " if archivo["renombrada"] else "")
          + (f"a solo lectura: {', '.join(archivo['a_lectura'])}" if archivo["a_lectura"]
             else "sin editores que pasar a lectura"))
    for a in archivo["avisos"]:
        print(f"AVISO: {a}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
