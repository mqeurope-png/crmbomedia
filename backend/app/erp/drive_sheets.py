"""ERP-F6 — escritura del seguimiento en la hoja de Drive de Bart.

Autenticación: CUENTA DE SERVICIO de Google, no el OAuth de Gmail — la app
OAuth está sin verificar y sus refresh tokens caducan cada 7 días, lo que
rompería cualquier sincronizado automático. La cuenta de servicio no caduca,
no necesita verificación y no depende de que nadie reconecte. Bart comparte
la hoja con el `client_email` de la cuenta (paso manual, documentado en el
PR).

Reglas de escritura (las tres son sagradas):
  1. INCREMENTAL: solo se tocan las celdas que cambian, nunca se reescribe
     la hoja entera.
  2. NUNCA se borra una fila que BoHub no reconozca — hay anotaciones
     manuales del equipo. (Este módulo no tiene NINGUNA operación de borrado.)
  3. NUNCA se pisa una celda con contenido manual: solo se escribe si la
     celda está vacía o si su valor es exactamente el que BoHub dejó la
     última vez (foto en `erp_drive_sync_rows`). Ante la duda, no se toca y
     el conflicto se devuelve en el resumen.

Los pedidos nuevos se insertan justo debajo de la cabecera — la sección de
«pedidos en curso» que Bart tiene arriba del histórico. Las credenciales son
un secreto: cifradas en la base, jamás en el log ni en la interfaz.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import ErpDriveSyncRow, ErpSettings
from app.erp.seguimiento import (
    KEY_COLUMN,
    KEY_COLUMN_INDEX,
    SEG_COLUMNS,
    SEGUIMIENTO_COLUMNS,
    UNMANAGED_COLUMN_INDEXES,
    is_structure_row,
    match_header_columns,
    row_to_sheet_values,
    snapshot_dump,
    snapshot_load,
)

logger = logging.getLogger(__name__)

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
_API = "https://sheets.googleapis.com/v4/spreadsheets"
_TIMEOUT = 30


class DriveConfigError(RuntimeError):
    """Cuenta de servicio o hoja destino sin configurar / ilegibles."""


class DriveSyncError(RuntimeError):
    """La hoja no tiene la forma esperada (cabecera no encontrada, etc.)."""


def parse_service_account_json(raw: str) -> dict[str, Any]:
    """Valida el JSON de la cuenta de servicio SIN loguearlo jamás: los
    mensajes de error nunca incluyen el contenido."""
    try:
        info = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise DriveConfigError(
            "las credenciales no son un JSON válido (no se muestra el contenido)"
        ) from exc
    if not isinstance(info, dict) or not info.get("client_email") or not info.get("private_key"):
        raise DriveConfigError(
            "el JSON de la cuenta de servicio debe incluir client_email y private_key"
        )
    return info


def drive_config(cfg: ErpSettings | None) -> tuple[dict[str, Any], str] | None:
    """(credenciales, spreadsheet_id) o None si falta algo por configurar."""
    if cfg is None or not cfg.drive_service_account_json_encrypted or not cfg.drive_spreadsheet_id:
        return None
    from app.core.crypto import decrypt  # noqa: PLC0415

    return (
        parse_service_account_json(decrypt(cfg.drive_service_account_json_encrypted)),
        cfg.drive_spreadsheet_id,
    )


def service_account_email(cfg: ErpSettings | None) -> str | None:
    """Solo el client_email (lo único de las credenciales que se enseña: es
    el email con el que Bart comparte la hoja)."""
    if cfg is None or not cfg.drive_service_account_json_encrypted:
        return None
    try:
        from app.core.crypto import decrypt  # noqa: PLC0415

        info = parse_service_account_json(decrypt(cfg.drive_service_account_json_encrypted))
    except Exception:  # noqa: BLE001 — nunca propagar contenido de credenciales
        return None
    return str(info.get("client_email"))


class SheetsTransport(Protocol):
    """Lo que necesita el sincronizador. `GoogleSheetsClient` lo implementa
    contra la API real; los tests usan una hoja en memoria."""

    def get_values(self) -> list[list[str]]: ...
    def update_cells(self, updates: list[tuple[int, int, str]]) -> None: ...
    def insert_rows_at(self, row: int, count: int) -> None: ...
    def write_rows(self, start_row: int, rows: list[list[str]]) -> None: ...


class GoogleSheetsClient:
    """Cliente mínimo de la API de Sheets con cuenta de servicio."""

    def __init__(self, info: dict[str, Any], spreadsheet_id: str) -> None:
        self._info = info
        self.spreadsheet_id = spreadsheet_id
        self._token: str | None = None
        self._sheet_id: int | None = None
        self._sheet_title: str | None = None

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            try:
                from google.auth.transport.requests import Request  # noqa: PLC0415
                from google.oauth2.service_account import Credentials  # noqa: PLC0415

                creds = Credentials.from_service_account_info(
                    self._info, scopes=[SHEETS_SCOPE],
                )
                creds.refresh(Request())
                self._token = creds.token
            except Exception as exc:
                # SOLO el tipo del error: jamás su contenido (podría arrastrar
                # material de la clave privada).
                raise DriveSyncError(
                    "no se pudo autenticar con la cuenta de servicio "
                    f"({type(exc).__name__}); revisa las credenciales y que la "
                    "hoja esté compartida con su client_email"
                ) from exc
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        import requests  # noqa: PLC0415

        r = requests.request(
            method, f"{_API}/{self.spreadsheet_id}{path}",
            headers=self._headers(), timeout=_TIMEOUT, **kwargs,
        )
        if r.status_code >= 400:
            # El cuerpo del error de Google puede ser largo pero nunca
            # contiene credenciales; se recorta igualmente.
            raise DriveSyncError(f"Sheets API {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else {}

    def _sheet(self) -> tuple[int, str]:
        if self._sheet_id is None or self._sheet_title is None:
            data = self._request("GET", "?fields=sheets.properties")
            props = (data.get("sheets") or [{}])[0].get("properties") or {}
            self._sheet_id = int(props.get("sheetId") or 0)
            self._sheet_title = str(props.get("title") or "Hoja 1")
        return self._sheet_id, self._sheet_title

    def get_values(self) -> list[list[str]]:
        _, title = self._sheet()
        data = self._request("GET", f"/values/{title}!A1:Q100000")
        return data.get("values") or []

    def update_cells(self, updates: list[tuple[int, int, str]]) -> None:
        if not updates:
            return
        _, title = self._sheet()
        data = [
            {
                "range": f"{title}!{_col_a1(col)}{row}",
                "values": [[value]],
            }
            for row, col, value in updates
        ]
        self._request(
            "POST", "/values:batchUpdate",
            json={"valueInputOption": "RAW", "data": data},
        )

    def insert_rows_at(self, row: int, count: int) -> None:
        sheet_id, _ = self._sheet()
        self._request("POST", ":batchUpdate", json={"requests": [{
            "insertDimension": {
                "range": {
                    "sheetId": sheet_id, "dimension": "ROWS",
                    "startIndex": row - 1, "endIndex": row - 1 + count,
                },
                "inheritFromBefore": False,
            },
        }]})

    def write_rows(self, start_row: int, rows: list[list[str]]) -> None:
        if not rows:
            return
        _, title = self._sheet()
        width = max((len(r) for r in rows), default=len(SEGUIMIENTO_COLUMNS))
        end_col = _col_a1(width - 1)
        rng = f"{title}!A{start_row}:{end_col}{start_row + len(rows) - 1}"
        self._request(
            "PUT", f"/values/{rng}?valueInputOption=RAW",
            json={"values": rows},
        )


def _col_a1(idx: int) -> str:
    """Índice de columna 0-based → letra(s) A1 (0→A, 25→Z, 26→AA)."""
    out = ""
    n = idx
    while True:
        n, rem = divmod(n, 26)
        out = chr(ord("A") + rem) + out
        if n == 0:
            break
        n -= 1
    return out


def _norm_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _render_header(row: list[Any]) -> str:
    """La cabecera leída, tal cual, con índices — para que Bart vea con qué se
    está comparando (Parte D)."""
    return " · ".join(f"[{i}] «{str(c).strip()}»" for i, c in enumerate(row))


def _locate_header(values: list[list[str]]) -> tuple[int, dict[int, int]]:
    """(fila de cabecera 1-based, mapa columna lógica → columna de la hoja).

    La cabecera es la PRIMERA fila que reconoce la columna clave y al menos
    HEADER_MIN_MATCHES columnas por sus alias (ERP-F6-fix1): así se detecta
    aunque los nombres difieran (tildes, mayúsculas, `Nº`/`Núm`, `f` por
    Empresa…) y sin confundirla con una fila de datos. Si no aparece, el error
    dice qué columna falta y ENSEÑA la cabecera leída con sus índices."""
    from app.erp.seguimiento import HEADER_MIN_MATCHES  # noqa: PLC0415

    best_row: list[Any] = []
    best_score = -1
    for i, row in enumerate(values):
        col_map = match_header_columns(row)
        if len(col_map) > best_score:
            best_score, best_row = len(col_map), row
        if KEY_COLUMN_INDEX in col_map and len(col_map) >= HEADER_MIN_MATCHES:
            return i + 1, col_map
    read = _render_header(best_row) if best_row else "(hoja vacía)"
    raise DriveSyncError(
        f"no se reconoció la cabecera de la hoja: falta la columna «{KEY_COLUMN}» "
        "(imprescindible para localizar cada pedido). Cabecera leída — "
        f"{read}. Añade esa columna a la hoja, o si ya existe con otro nombre, "
        "avisa para incluirlo entre los nombres reconocidos. No se ha escrito nada."
    )


def sync_to_sheet(
    session: Session,
    sheets: SheetsTransport,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Sincroniza las filas del seguimiento con la hoja. Devuelve el resumen
    (celdas actualizadas, filas añadidas y conflictos NO tocados) para que
    Bart vea exactamente qué se escribió."""
    values = sheets.get_values()
    header_row, col_map = _locate_header(values)
    key_col = col_map[KEY_COLUMN_INDEX]
    # Columnas opcionales que la hoja NO trae: se omiten (no rompen la sync).
    omitted_columns = [
        SEG_COLUMNS[c].header
        for c in range(len(SEG_COLUMNS))
        if c not in col_map and c not in UNMANAGED_COLUMN_INDEXES
    ]

    # clave → nº de fila (1-based) en la hoja; se saltan separadores («^^^^»)
    # y la cabecera repetida a media hoja (estructura, no pedidos).
    sheet_index: dict[str, int] = {}
    for i, row in enumerate(values):
        if i + 1 <= header_row or is_structure_row(row):
            continue
        key = _norm_key(row[key_col] if len(row) > key_col else "")
        if key and key not in sheet_index:
            sheet_index[key] = i + 1

    snapshots = {
        s.order_id: s for s in session.scalars(select(ErpDriveSyncRow)).all()
    }

    updates: list[tuple[int, int, str]] = []
    conflicts: list[dict[str, Any]] = []
    new_rows: list[tuple[dict[str, Any], list[str]]] = []
    touched: list[tuple[dict[str, Any], list[str]]] = []

    for row in rows:
        key = _norm_key(row.get("albaran_pedido"))
        if not key:
            continue
        # `new_values` va en el orden CANÓNICO; el mapa lleva cada columna a su
        # sitio real en la hoja (que puede diferir en nombre y posición).
        new_values = row_to_sheet_values(row)
        if key not in sheet_index:
            new_rows.append((row, new_values))
            continue
        rownum = sheet_index[key]
        existing_raw = values[rownum - 1]
        snap = snapshots.get(row["id"])
        last = snapshot_load(snap.last_values_json if snap else None) or []
        # `written` = lo que BoHub HA escrito (nunca el estado de la hoja):
        # si la foto incluyera ediciones manuales, el siguiente sincronizado
        # las tomaría por propias y las pisaría. Sigue en orden canónico.
        written = [
            (last[c].strip() if c < len(last) else "")
            for c in range(len(SEGUIMIENTO_COLUMNS))
        ]
        for canon, new in enumerate(new_values):
            if canon in UNMANAGED_COLUMN_INDEXES:
                continue  # «Orden» es de Bart: no se escribe jamás
            sheet_col = col_map.get(canon)
            if sheet_col is None:
                continue  # esa columna no existe en la hoja: se omite
            current = (
                str(existing_raw[sheet_col]).strip()
                if sheet_col < len(existing_raw) else ""
            )
            new_s = new.strip()
            if current == new_s:
                written[canon] = new_s  # la hoja ya dice lo mismo que BoHub
                continue
            if not new_s:
                continue  # BoHub sin dato nunca borra lo que haya escrito
            if current == "" or current == written[canon]:
                updates.append((rownum, sheet_col, new_s))
                written[canon] = new_s
            else:
                # Contenido manual distinto de lo que BoHub dejó: no tocar.
                conflicts.append({
                    "order_number": row.get("albaran_pedido"),
                    "row": rownum,
                    "column": SEG_COLUMNS[canon].header,
                    "sheet_value": current,
                    "bohub_value": new_s,
                })
        touched.append((row, written))

    # 1º las actualizaciones (índices originales), 2º la inserción de filas
    # nuevas bajo la cabecera — así la inserción no desplaza nada ya escrito.
    sheets.update_cells(updates)
    if new_rows:
        width = max(len(values[header_row - 1]), max(col_map.values()) + 1)
        lines = []
        for _row, new_values in new_rows:
            line = [""] * width
            for canon, val in enumerate(new_values):
                if canon in UNMANAGED_COLUMN_INDEXES:
                    continue
                sheet_col = col_map.get(canon)
                if sheet_col is not None:
                    line[sheet_col] = val
            lines.append(line)
        sheets.insert_rows_at(header_row + 1, len(new_rows))
        sheets.write_rows(header_row + 1, lines)
        touched.extend(new_rows)

    now = datetime.now(UTC).replace(tzinfo=None)
    for row, written in touched:
        snap = snapshots.get(row["id"])
        if snap is None:
            snap = ErpDriveSyncRow(order_id=row["id"], row_key=_norm_key(row["albaran_pedido"]))
            session.add(snap)
            snapshots[row["id"]] = snap
        snap.row_key = _norm_key(row["albaran_pedido"])
        snap.last_values_json = snapshot_dump(written)
        snap.synced_at = now
    session.commit()

    summary = {
        "ok": True,
        "orders_considered": len(rows),
        "updated_cells": len(updates),
        "appended_rows": len(new_rows),
        "conflicts": conflicts,
        "sheet_rows": len(values),
        # ERP-F6-fix1: columnas opcionales ausentes en la hoja (Tracking,
        # Nº de serie, WhiteRIP…): se sincroniza el resto y se avisa.
        "omitted_columns": omitted_columns,
    }
    logger.info(
        "drive sync: %s celdas actualizadas, %s filas nuevas, %s conflictos, "
        "columnas omitidas: %s",
        len(updates), len(new_rows), len(conflicts), omitted_columns or "ninguna",
    )
    return summary
