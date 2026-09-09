"""ERP-F6-fix6 — comprobación empírica de que la sincronización NO cambia el
formato ni el valor de las celdas del histórico de la hoja de Drive.

La corrupción del histórico (7.849 celdas reformateadas, números convertidos en
fechas o en `#VALUE!`) se midió sobre backups exportados. Esta herramienta la
comprueba DIRECTAMENTE por API —`spreadsheets.get` con `includeGridData`— sin
exportar a `.xlsx`, leyendo el `userEnteredFormat.numberFormat` y el valor de
una muestra de celdas ANTES y DESPUÉS de una sincronización.

Uso (sobre una COPIA de la hoja, nunca la de producción sin querer):

    # 1) foto ANTES de sincronizar
    python -m scripts.erp_f6_verify_sheet_format snapshot --out antes.json
    # 2) lanzar la sincronización (desde la interfaz, «Actualizar hoja de Drive»)
    # 3) foto DESPUÉS
    python -m scripts.erp_f6_verify_sheet_format snapshot --out despues.json
    # 4) comparar: NINGÚN formato ni valor del histórico debe haber cambiado
    python -m scripts.erp_f6_verify_sheet_format compare antes.json despues.json

Las credenciales salen de la configuración ERP (cuenta de servicio cifrada) por
defecto; pueden pasarse con --credentials / --spreadsheet-id para operar sobre
una copia con otro id. Las celdas por defecto incluyen las cuatro que se
corrompieron (K3434, L6542, B5390, B7681); ajústalas con --cells según el
desplazamiento de filas de la copia.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# Las 4 celdas que se corrompieron (coordenadas de la hoja de referencia) más
# una muestra ancha del histórico. Ajusta el rango a tu copia si el número de
# filas difiere (la sincronización inserta filas arriba).
DEFAULT_CELLS = ["K3434", "L6542", "B5390", "B7681"]
DEFAULT_SAMPLE_RANGES = ["A2:Q60", "A3400:Q3440", "A5380:Q5400", "A6530:Q6550",
                         "A7670:Q7690"]

_API = "https://sheets.googleapis.com/v4/spreadsheets"


def _load_credentials(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    if args.credentials and args.spreadsheet_id:
        with open(args.credentials, encoding="utf-8") as fh:
            return json.load(fh), args.spreadsheet_id
    # Por defecto: de la configuración ERP (cuenta de servicio cifrada).
    from app.db.session import SessionLocal  # noqa: PLC0415
    from app.erp.drive_sheets import drive_config  # noqa: PLC0415
    from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings  # noqa: PLC0415

    with SessionLocal() as session:
        conf = drive_config(session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID))
    if conf is None:
        sys.exit("No hay cuenta de servicio ni hoja configuradas en el ERP; "
                 "usa --credentials y --spreadsheet-id sobre una copia.")
    info, spreadsheet_id = conf
    return info, (args.spreadsheet_id or spreadsheet_id)


def _token(info: dict[str, Any]) -> str:
    from google.auth.transport.requests import Request  # noqa: PLC0415
    from google.oauth2.service_account import Credentials  # noqa: PLC0415

    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    creds.refresh(Request())
    return creds.token


def _read_grid(info: dict[str, Any], spreadsheet_id: str, ranges: list[str]) -> dict[str, Any]:
    """Lee valor + numberFormat de los rangos pedidos, por celda A1."""
    import requests  # noqa: PLC0415

    params = [("ranges", r) for r in ranges]
    params.append((
        "fields",
        "sheets.data.startRow,sheets.data.startColumn,"
        "sheets.data.rowData.values.formattedValue,"
        "sheets.data.rowData.values.userEnteredValue,"
        "sheets.data.rowData.values.userEnteredFormat.numberFormat",
    ))
    r = requests.get(
        f"{_API}/{spreadsheet_id}", params=params,
        headers={"Authorization": f"Bearer {_token(info)}"}, timeout=60,
    )
    if r.status_code >= 400:
        sys.exit(f"Sheets API {r.status_code}: {r.text[:300]}")
    out: dict[str, Any] = {}
    for sheet in r.json().get("sheets", []):
        for block in sheet.get("data", []):
            base_row = block.get("startRow", 0)
            base_col = block.get("startColumn", 0)
            for dr, rowdata in enumerate(block.get("rowData", [])):
                for dc, cell in enumerate(rowdata.get("values", [])):
                    a1 = f"{_col_a1(base_col + dc)}{base_row + dr + 1}"
                    fmt = (cell.get("userEnteredFormat") or {}).get("numberFormat") or {}
                    out[a1] = {
                        "value": cell.get("formattedValue", ""),
                        "numberFormat": fmt.get("type", "") + (
                            f":{fmt['pattern']}" if fmt.get("pattern") else ""
                        ),
                    }
    return out


def _col_a1(idx: int) -> str:
    out, n = "", idx
    while True:
        n, rem = divmod(n, 26)
        out = chr(ord("A") + rem) + out
        if n == 0:
            break
        n -= 1
    return out


def cmd_snapshot(args: argparse.Namespace) -> None:
    info, spreadsheet_id = _load_credentials(args)
    ranges = args.cells or (DEFAULT_CELLS + DEFAULT_SAMPLE_RANGES)
    grid = _read_grid(info, spreadsheet_id, ranges)
    payload = {"spreadsheet_id": spreadsheet_id, "cells": grid}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    focus = {c: grid.get(c) for c in DEFAULT_CELLS if c in grid}
    print(f"Foto guardada en {args.out}: {len(grid)} celdas leídas.")
    if focus:
        print("Celdas críticas:")
        for c, v in focus.items():
            print(f"  {c}: valor={v['value']!r}  numberFormat={v['numberFormat']!r}")


def cmd_compare(args: argparse.Namespace) -> None:
    with open(args.before, encoding="utf-8") as fh:
        before = json.load(fh)["cells"]
    with open(args.after, encoding="utf-8") as fh:
        after = json.load(fh)["cells"]
    fmt_changes, val_changes = [], []
    for a1, b in before.items():
        a = after.get(a1)
        if a is None:
            continue
        if a["numberFormat"] != b["numberFormat"]:
            fmt_changes.append((a1, b["numberFormat"], a["numberFormat"]))
        if a["value"] != b["value"]:
            val_changes.append((a1, b["value"], a["value"]))
    print(f"Celdas comparadas: {len(before)}")
    print(f"Formatos cambiados: {len(fmt_changes)}")
    for a1, x, y in fmt_changes[:50]:
        print(f"  {a1}: {x!r} → {y!r}")
    print(f"Valores cambiados: {len(val_changes)}")
    for a1, x, y in val_changes[:50]:
        print(f"  {a1}: {x!r} → {y!r}")
    if fmt_changes or val_changes:
        sys.exit("❌ La sincronización cambió formato o valores del histórico.")
    print("✅ Ningún formato ni valor del histórico cambió tras sincronizar.")


def _fmt_call(call: dict[str, Any]) -> str:
    """Una llamada a la API de Sheets, tal cual salió, para el informe: método,
    endpoint, y el rango o el número de celdas."""
    api = call.get("api", "?")
    if api == "values:batchUpdate":
        ranges = call.get("ranges") or []
        return (f"{call.get('method')} {api}  celdas={call.get('cells')}  "
                f"rangos={ranges}")
    if api == "batchUpdate:insertDimension":
        return (f"{call.get('method')} {api}  {call.get('range')}  "
                f"inheritFromBefore={call.get('inheritFromBefore')}")
    if api == "values.get":
        return (f"{call.get('method')} {api}  range={call.get('range')}  "
                f"filas={call.get('rows_read')}")
    if api == "values.update":
        return (f"{call.get('method')} {api}  range={call.get('range')}  "
                f"filas_completas={call.get('rows')}")
    return f"{call.get('method')} {api}"


def cmd_trace(args: argparse.Namespace) -> None:
    """ERP-F6-fix7 Parte A — mide contra la hoja REAL: lee valor+formato de las
    celdas testigo, ejecuta UNA sincronización de verdad y vuelve a leerlas,
    volcando además la TRAZA EXACTA de las llamadas a la API de Google (método,
    rango y nº de celdas). Escribe en la hoja → exige --spreadsheet-id de una
    COPIA."""
    if not args.spreadsheet_id:
        sys.exit("Para 'trace' indica --spreadsheet-id con el ID de una COPIA de "
                 "la hoja: este comando ESCRIBE en ella.")
    info, spreadsheet_id = _load_credentials(args)

    from app.db.session import SessionLocal  # noqa: PLC0415
    from app.erp.api.seguimiento import (  # noqa: PLC0415
        _factusol_invoice_serie_resolver,
        build_drive_sync_rows,
    )
    from app.erp.drive_sheets import GoogleSheetsClient, sync_to_sheet  # noqa: PLC0415
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    witness = args.cells or DEFAULT_CELLS
    before = _read_grid(info, spreadsheet_id, witness)

    client = GoogleSheetsClient(info, spreadsheet_id)
    with SessionLocal() as session:
        prefer = bool(series_config(session).get("drive_reference_prefer_albaran", True))
        rows = build_drive_sync_rows(session)
        summary = sync_to_sheet(
            session, client, rows, prefer_albaran=prefer, dry_run=args.dry_run,
            invoice_serie_resolver=_factusol_invoice_serie_resolver(session),
        )
    after = _read_grid(info, spreadsheet_id, witness)

    print("\n===== TRAZA de las llamadas a la API de Sheets (en orden) =====")
    for call in client.calls:
        print("  " + _fmt_call(call))
    print(f"\n  resumen: +{summary['appended_rows']} filas nuevas, "
          f"{summary['already_present']} ya presentes, "
          f"{summary['orders_to_review']} a revisar, "
          f"{len(summary.get('unknown_invoices', []))} facturas desconocidas"
          + ("  (DRY-RUN: no se escribió)" if args.dry_run else ""))

    print("\n===== Celdas testigo — ANTES vs DESPUÉS (por API, no export) =====")
    changed = 0
    for a1 in witness:
        b, a = before.get(a1), after.get(a1)
        bt = f"valor={b['value']!r} fmt={b['numberFormat']!r}" if b else "(no leída)"
        at = f"valor={a['value']!r} fmt={a['numberFormat']!r}" if a else "(no leída)"
        mark = ""
        if b and a and (b["value"] != a["value"] or b["numberFormat"] != a["numberFormat"]):
            mark, changed = "  ⚠ CAMBIÓ", changed + 1
        print(f"  {a1}:\n    antes:   {bt}\n    después: {at}{mark}")
    print(f"\n  {'❌' if changed else '✅'} {changed} celda(s) testigo cambiaron "
          "de valor o formato.")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"spreadsheet_id": spreadsheet_id, "calls": client.calls,
                       "before": before, "after": after,
                       "summary": {k: summary[k] for k in
                                   ("appended_rows", "already_present",
                                    "orders_to_review")}},
                      fh, ensure_ascii=False, indent=2)
        print(f"\nTraza completa guardada en {args.out}.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--credentials", help="JSON de la cuenta de servicio (copia)")
    common.add_argument("--spreadsheet-id", help="id de la hoja (copia)")
    common.add_argument("--cells", nargs="*", help="celdas/rangos A1 a muestrear")

    s = sub.add_parser("snapshot", parents=[common], help="foto de valor+formato")
    s.add_argument("--out", required=True, help="fichero JSON de salida")
    s.set_defaults(func=cmd_snapshot)

    c = sub.add_parser("compare", help="compara dos fotos (antes/después)")
    c.add_argument("before")
    c.add_argument("after")
    c.set_defaults(func=cmd_compare)

    t = sub.add_parser(
        "trace", parents=[common],
        help="sincroniza una COPIA y vuelca la traza de llamadas + testigos",
    )
    t.add_argument("--out", help="fichero JSON de salida (opcional)")
    t.add_argument("--dry-run", action="store_true",
                   help="no escribe: solo lee y muestra qué llamaría")
    t.set_defaults(func=cmd_trace)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
