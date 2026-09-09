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
from collections.abc import Callable
from datetime import UTC, date, datetime
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
    clients_match,
    extract_order_number,
    is_structure_row,
    match_header_columns,
    match_number,
    order_match_numbers,
    parse_sheet_date,
    reference_for_row,
    row_to_sheet_values,
    serie_of_invoice,
    snapshot_dump,
    split_client_parts,
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
        # ERP-F6-fix6 — traza de las llamadas a la API (método, rango, tamaño
        # del payload). Se vuelca en el log tras cada sincronización para poder
        # comprobar que NUNCA se manda un rango mayor que las celdas que cambian
        # ni formato alguno. No contiene credenciales ni valores de celda.
        self.calls: list[dict[str, Any]] = []

    def _record(self, entry: dict[str, Any]) -> None:
        self.calls.append(entry)

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
        rng = f"{title}!A1:Q100000"
        data = self._request("GET", f"/values/{rng}")
        values = data.get("values") or []
        self._record({"method": "GET", "api": "values.get", "range": rng,
                      "rows_read": len(values)})
        return values

    def update_cells(self, updates: list[tuple[int, int, str]]) -> None:
        """Escribe SOLO las celdas indicadas, cada una como un rango de UNA
        celda, en una única llamada `values:batchUpdate` con `RAW`. Nunca manda
        una fila entera, una columna ni la hoja: el payload son exactamente las
        celdas que cambian. No lleva formato (ni `userEnteredFormat` ni
        `repeatCell`): el formato de la hoja es de Bart y no se toca."""
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
        self._record({"method": "POST", "api": "values:batchUpdate",
                      "valueInputOption": "RAW", "cells": len(data),
                      "ranges": [d["range"] for d in data]})
        self._request(
            "POST", "/values:batchUpdate",
            json={"valueInputOption": "RAW", "data": data},
        )

    def insert_rows_at(self, row: int, count: int) -> None:
        """Inserta `count` filas SIN heredar el formato de la fila anterior
        (`inheritFromBefore=False`): las filas nuevas no arrastran el formato de
        una fila del histórico. Solo mueve filas; no escribe valor ni formato."""
        sheet_id, _ = self._sheet()
        self._record({"method": "POST", "api": "batchUpdate:insertDimension",
                      "range": f"ROWS {row}..{row + count - 1}",
                      "inheritFromBefore": False})
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
        """ERP-F6-fix6 — DESUSO en la sincronización: escribía un rango de filas
        COMPLETO (`A{fila}:Q…`), lo que hacía que Sheets reinfiriera el formato
        de cada celda reenviada y corrompiera números del histórico. La
        sincronización ya no lo llama: las filas nuevas se escriben celda a
        celda con `update_cells`. Se conserva por compatibilidad del transporte
        (y con `RAW`), pero no debe usarse para tocar el histórico."""
        if not rows:
            return
        _, title = self._sheet()
        width = max((len(r) for r in rows), default=len(SEGUIMIENTO_COLUMNS))
        end_col = _col_a1(width - 1)
        rng = f"{title}!A{start_row}:{end_col}{start_row + len(rows) - 1}"
        self._record({"method": "PUT", "api": "values.update",
                      "valueInputOption": "RAW", "range": rng,
                      "rows": len(rows)})
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


#: Índice de la columna Empresa (abreviatura), que se compara normalizada.
_EMPRESA_INDEX = SEGUIMIENTO_COLUMNS.index("Empresa")


def _abbr_aliases(session: Session) -> dict[str, str]:
    """`{forma normalizada → canónica}` de las abreviaturas de empresa y sus
    variantes históricas (ERP-F6-fix4): `STR`/`STREAMTEC`→`ST`, `BOM`→`BO`."""
    from app.erp.seguimiento import (  # noqa: PLC0415
        abbr_alias_to_canonical,
        abbr_variants_config,
        series_abbreviations_config,
    )
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    cfg = series_config(session)
    return abbr_alias_to_canonical(
        series_abbreviations_config(cfg.get("series_abbreviations")),
        abbr_variants_config(cfg.get("series_abbr_variants")),
    )


def _abbreviations(session: Session) -> dict[int, str]:
    """`{serie → abreviatura canónica}` de la configuración."""
    from app.erp.seguimiento import series_abbreviations_config  # noqa: PLC0415
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return series_abbreviations_config(series_config(session).get("series_abbreviations"))


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


#: Fila separadora entre la sección de arriba (pedidos con incidencia, de Bart)
#: y la de pedidos en curso (ERP-F6-fix5).
_SECTION_SEPARATOR_PREFIX = "^^^^"


def _row_is_blank(row: list[Any]) -> bool:
    return not any(str(c).strip() for c in row)


def _locate_sections(values: list[list[str]], top_header_row: int) -> tuple[int, bool]:
    """ERP-F6-fix5 — dónde empieza la sección de pedidos EN CURSO.

    La hoja real de Bart tiene DOS secciones: arriba los pedidos con incidencia
    (que Bart mantiene a mano, entre la cabecera superior y la fila «^^^^»), y
    debajo, tras una segunda cabecera, los pedidos en curso. Los pedidos nuevos
    van bajo esa SEGUNDA cabecera, nunca en la sección de arriba.

    Devuelve (fila del encabezado bajo el que insertar 1-based, hay_dos_secciones).
    Si hay «^^^^» pero no aparece el segundo encabezado, NO se escribe (error).
    Si no hay «^^^^», la hoja tiene una sola sección: se inserta bajo la única
    cabecera (comportamiento de las hojas simples)."""
    sep = None
    for i in range(top_header_row, len(values)):
        first = str(values[i][0] if values[i] else "").strip()
        if first.startswith(_SECTION_SEPARATOR_PREFIX):
            sep = i
            break
    if sep is None:
        return top_header_row, False
    for i in range(sep + 1, len(values)):
        cmap = match_header_columns(values[i])
        if KEY_COLUMN_INDEX in cmap and len(cmap) >= _min_matches():
            return i + 1, True
    raise DriveSyncError(
        "la hoja tiene la fila separadora «^^^^» (pedidos con incidencia arriba) "
        "pero no se encontró debajo el segundo encabezado de «ARRIBA EN PROCESO»; "
        "no se escribe nada para no insertar en el sitio equivocado."
    )


def _min_matches() -> int:
    from app.erp.seguimiento import HEADER_MIN_MATCHES  # noqa: PLC0415

    return HEADER_MIN_MATCHES


_PRODUCTOS_INDEX = SEGUIMIENTO_COLUMNS.index("Productos")
_CLIENTE_INDEX = SEGUIMIENTO_COLUMNS.index("Cliente")
_FACTURA_INDEX = SEGUIMIENTO_COLUMNS.index("Nº de Factura")
#: Columnas de fecha: se comparan como fechas (mismo día), no como texto.
_DATE_INDEXES = {
    SEGUIMIENTO_COLUMNS.index("Fecha entrada albarán"),
    SEGUIMIENTO_COLUMNS.index("Preparado"),
    SEGUIMIENTO_COLUMNS.index("Recogido"),
    SEGUIMIENTO_COLUMNS.index("F Envío Factura"),
}
#: Columnas que NUNCA generan conflicto y solo se rellenan si están vacías:
#: Productos (texto libre, nunca coincide) y Cliente (Bart lo escribe más rico).
_FILL_ONLY_IF_EMPTY = {_PRODUCTOS_INDEX, _CLIENTE_INDEX}


def _sheet_row_meta(
    values: list[list[str]], header_row: int, col_map: dict[int, int],
) -> list[dict[str, Any]]:
    """Datos de cada fila de PEDIDO de la hoja para el emparejamiento por
    número + confirmación secundaria. Se saltan cabecera, separadores y la
    cabecera repetida. El número exige ≥4 dígitos (ERP-F6-fix4)."""
    key_col = col_map[KEY_COLUMN_INDEX]
    fac_col = col_map.get(SEGUIMIENTO_COLUMNS.index("Nº de Factura"))
    cli_col = col_map.get(_CLIENTE_INDEX)
    fec_col = col_map.get(SEGUIMIENTO_COLUMNS.index("Fecha entrada albarán"))

    def cell(row: list[str], col: int | None) -> str:
        return str(row[col]).strip() if col is not None and col < len(row) else ""

    meta: list[dict[str, Any]] = []
    for i, row in enumerate(values):
        if i + 1 <= header_row or is_structure_row(row):
            continue
        number = match_number(cell(row, key_col))
        if number is None:
            continue
        meta.append({
            "rownum": i + 1,
            "number": number,
            "factura": extract_order_number(cell(row, fac_col)),
            "factura_raw": cell(row, fac_col),
            "cliente_raw": cell(row, cli_col),
            "fecha": parse_sheet_date(cell(row, fec_col)),
        })
    return meta


def _secondary_signals(row: dict[str, Any], entry: dict[str, Any]) -> dict[str, str]:
    """Estado de cada dato secundario entre el pedido y la fila de la hoja:
    'agree' (coincide), 'contradict' (ambos existen y difieren) o 'missing'
    (falta en un lado, no contradice nada). Distingue «contradicho» de «sin
    confirmar» (ERP-F6-fix4, Parte E)."""
    out: dict[str, str] = {}

    fac = extract_order_number(row.get("factura"))
    if fac and entry["factura"]:
        out["factura"] = "agree" if fac == entry["factura"] else "contradict"
    else:
        out["factura"] = "missing"

    bohub_clients = [c for c in (row.get("cliente_company"), row.get("cliente_person"))
                     if str(c or "").strip()]
    sheet_clients = split_client_parts(entry["cliente_raw"])
    if bohub_clients and sheet_clients:
        matched = any(clients_match(a, b) for a in bohub_clients for b in sheet_clients)
        out["cliente"] = "agree" if matched else "contradict"
    else:
        out["cliente"] = "missing"

    fecha = row.get("fecha")
    if fecha and entry["fecha"]:
        try:
            same = date.fromisoformat(str(fecha)[:10]) == entry["fecha"]
        except ValueError:
            same = False
        out["fecha"] = "agree" if same else "contradict"
    else:
        out["fecha"] = "missing"
    return out


def _locate_existing(
    row: dict[str, Any], meta: list[dict[str, Any]], used: set[int],
) -> tuple[int | None, str, dict[str, Any] | None]:
    """Localiza la fila de la hoja del pedido. Devuelve (rownum, estado, info):
      - (rownum, "found", None)      → confirmado: se actualiza.
      - (None, "new", None)          → no está en la hoja: se añade.
      - (None, "contradicted", info) → el número casa pero un dato lo CONTRADICE.
      - (None, "probable", info)     → el número casa, nada contradice pero falta
                                        confirmación (dato ausente en un lado).
      - (None, "ambiguous", info)    → el número casa con varias filas."""
    nums = order_match_numbers(row)
    if not nums:
        return None, "new", None
    candidates = [e for e in meta if e["number"] in nums and e["rownum"] not in used]
    if not candidates:
        return None, "new", None

    scored = [(e, _secondary_signals(row, e)) for e in candidates]
    confirmed = [(e, s) for e, s in scored if "agree" in s.values()]
    if len(confirmed) == 1:
        return confirmed[0][0]["rownum"], "found", None
    if len(confirmed) > 1:
        return None, "ambiguous", {
            "rows": [e["rownum"] for e, _ in confirmed],
            "detail": (
                f"el número {sorted(nums)} casa con varias filas confirmadas "
                f"({', '.join(str(e['rownum']) for e, _ in confirmed)})"
            ),
        }
    # Ninguno confirmado: ¿alguien CONTRADICE, o solo falta el dato?
    contradicted = [(e, s) for e, s in scored if "contradict" in s.values()]
    if contradicted:
        e, s = contradicted[0]
        diffs = [k for k, v in s.items() if v == "contradict"]
        return None, "contradicted", {
            "rows": [e["rownum"] for e, _ in contradicted],
            "detail": (
                f"el número {sorted(nums)} casa con la fila {e['rownum']} pero "
                f"{', '.join(diffs)} no coinciden"
            ),
        }
    e = candidates[0]
    return None, "probable", {
        "rows": [c["rownum"] for c in candidates],
        "detail": (
            f"el número {sorted(nums)} casa con la fila {e['rownum']}; falta un "
            "segundo dato para confirmarlo (revisar)"
        ),
    }


def _sheet_invoice_serie(
    factura_raw: str,
    resolver: Callable[[str], int | None] | None,
) -> int | None:
    """Serie de una factura escrita EN LA HOJA (ERP-F6-fix5):
      - con serie explícita (`1-260737`) → esa serie, el dato más fiable;
      - número desnudo (`260731`) → se resuelve contra FACTUSOL por CODFAC;
        si el mismo CODFAC vive en varias series (gotcha) → None (no tocar)."""
    raw = str(factura_raw or "").strip()
    if not raw:
        return None
    explicit = serie_of_invoice(raw)
    if explicit is not None:
        return explicit
    num = extract_order_number(raw)
    if num is None or resolver is None:
        return None
    return resolver(num)


def sync_to_sheet(
    session: Session,
    sheets: SheetsTransport,
    rows: list[dict[str, Any]],
    *,
    prefer_albaran: bool = True,
    dry_run: bool = False,
    invoice_serie_resolver: Callable[[str], int | None] | None = None,
) -> dict[str, Any]:
    """Sincroniza las filas del seguimiento con la hoja. ERP-F6-fix7 — SOLO
    INSERTA: identifica cada pedido por su NÚMERO desnudo confirmado con un
    segundo dato (ERP-F6-fix2) para saber si YA está en la hoja; si está, no lo
    toca (ni una celda, ni la Empresa, ni nada); si no está, lo AÑADE bajo el
    segundo encabezado (ERP-F6-fix5), con el bloque ordenado del más reciente al
    más antiguo (ERP-F6-fix7 Parte B-bis). Nunca borra filas ni pisa contenido
    manual. `dry_run` calcula y devuelve el resumen SIN escribir."""
    values = sheets.get_values()
    header_row, col_map = _locate_header(values)
    # ERP-F6-fix5: bajo qué encabezado se insertan los pedidos EN CURSO y desde
    # dónde se leen (la sección de arriba, de incidencias, se ignora).
    insert_header_row, _two_sections = _locate_sections(values, header_row)
    # Columnas opcionales que la hoja NO trae: se omiten (no rompen la sync).
    omitted_columns = [
        SEG_COLUMNS[c].header
        for c in range(len(SEG_COLUMNS))
        if c not in col_map and c not in UNMANAGED_COLUMN_INDEXES
    ]
    meta = _sheet_row_meta(values, insert_header_row, col_map)
    meta_by_row = {e["rownum"]: e for e in meta}

    snapshots = {
        s.order_id: s for s in session.scalars(select(ErpDriveSyncRow)).all()
    }

    conflicts: list[dict[str, Any]] = []
    probable_matches: list[dict[str, Any]] = []
    # ERP-F6-fix7 — «facturas de tu hoja que BoHub no conoce»: es INFORMACIÓN,
    # NO requiere decisión de Bart, así que va en su propia sección (fuera de
    # «a revisar»).
    unknown_invoices: list[dict[str, Any]] = []
    new_rows: list[tuple[dict[str, Any], list[str]]] = []
    present_rows: list[dict[str, Any]] = []  # ya están en la hoja: no se tocan
    used: set[int] = set()

    for row in rows:
        rownum, status, info = _locate_existing(row, meta, used)
        if status in ("contradicted", "ambiguous"):
            conflicts.append({
                "kind": status,
                "order_number": row.get("albaran_pedido"),
                "rows": (info or {}).get("rows"),
                "detail": (info or {}).get("detail"),
            })
            continue
        if status == "probable":
            probable_matches.append({
                "kind": "probable_match",
                "order_number": row.get("albaran_pedido"),
                "rows": (info or {}).get("rows"),
                "detail": (info or {}).get("detail"),
            })
            continue
        if status == "new":
            # `new_values` va en el orden CANÓNICO; el mapa lleva cada columna a
            # su sitio real. La referencia se escribe DESNUDA (formato de Bart).
            new_values = row_to_sheet_values(row)
            new_values[KEY_COLUMN_INDEX] = reference_for_row(
                row, prefer_albaran=prefer_albaran,
            )
            new_rows.append((row, new_values))
            continue
        # status == "found": el pedido YA está en la hoja. ERP-F6-fix7 — no se
        # actualiza NADA (ni celdas vacías, ni Empresa). El emparejamiento solo
        # sirve para NO volver a insertarlo (evita duplicados).
        used.add(rownum)
        entry = meta_by_row.get(rownum, {})
        # La hoja trae una factura que BoHub no tiene registrada: INFO útil (qué
        # facturas le faltan a BoHub), fuera de «a revisar» — no se escribe nada.
        if row.get("serie_invoice") is None and entry.get("factura_raw"):
            unknown_invoices.append({
                "kind": "sheet_invoice_unknown",
                "order_number": row.get("albaran_pedido"),
                "rows": [rownum],
                "detail": (
                    f"la hoja tiene la factura «{entry['factura_raw']}» que BoHub "
                    "no tiene registrada"
                ),
            })
        present_rows.append(row)

    # ERP-F6-fix7 Parte B-bis — el bloque a insertar va del MÁS RECIENTE (arriba,
    # justo bajo el segundo encabezado) al más antiguo. Orden estable: fecha de
    # entrada DESCENDENTE y, a igualdad de fecha, la referencia ASCENDENTE (para
    # que dos sincronizaciones seguidas no barajen el bloque). Y como siempre se
    # inserta bajo el encabezado, un pedido nuevo de mañana entra ENCIMA de los
    # de hoy.
    new_rows.sort(key=lambda rv: reference_for_row(rv[0], prefer_albaran=prefer_albaran))
    new_rows.sort(key=lambda rv: rv[0].get("fecha") or "", reverse=True)

    if not dry_run:
        # Cada fila nueva → lista de (columna_hoja, valor) solo para sus celdas
        # con contenido (las vacías no se tocan: no se reformatean celdas en
        # blanco de las filas reservadas de Bart). ERP-F6-fix6: celda a celda,
        # NUNCA una fila/columna completa.
        new_cells_per_row: list[list[tuple[int, str]]] = []
        for _row, new_values in new_rows:
            cells: list[tuple[int, str]] = []
            for canon, val in enumerate(new_values):
                if canon in UNMANAGED_COLUMN_INDEXES:
                    continue
                sheet_col = col_map.get(canon)
                if sheet_col is not None and str(val).strip():
                    cells.append((sheet_col, val))
            new_cells_per_row.append(cells)

        # Filas vacías reservadas justo debajo del segundo encabezado.
        reserved = 0
        r0 = insert_header_row  # 0-based índice de la primera fila candidata
        while r0 < len(values) and _row_is_blank(values[r0]):
            reserved += 1
            r0 += 1
        fill, overflow = new_cells_per_row[:reserved], new_cells_per_row[reserved:]

        # FASE 1 — relleno de las filas reservadas (por ENCIMA del punto de
        # inserción, su número no se desplaza), en una sola llamada de celdas.
        phase1: list[tuple[int, int, str]] = []
        for offset, cells in enumerate(fill):
            rownum = insert_header_row + 1 + offset
            phase1 += [(rownum, col, val) for col, val in cells]
        sheets.update_cells(phase1)

        # FASE 2 — desbordamiento: insertar exactamente las filas que faltan (sin
        # heredar formato) y escribir SOLO sus celdas con dato. No se añade
        # ninguna fila vacía al final ni se envía rango más allá de esas filas.
        if overflow:
            at = insert_header_row + 1 + reserved
            sheets.insert_rows_at(at, len(overflow))
            phase2: list[tuple[int, int, str]] = []
            for offset, cells in enumerate(overflow):
                rownum = at + offset
                phase2 += [(rownum, col, val) for col, val in cells]
            sheets.update_cells(phase2)

        # Marcar como «escrito en Drive» los pedidos insertados Y los que ya
        # estaban en la hoja (así salen de «pendiente de escribir» sin haber
        # tocado la hoja). NO es exclusión: siguen en la vista diaria.
        now = datetime.now(UTC).replace(tzinfo=None)
        for row, new_values in new_rows:
            _mark_written(session, snapshots, row, prefer_albaran, now,
                          snapshot_dump(new_values))
        for row in present_rows:
            _mark_written(session, snapshots, row, prefer_albaran, now, None)
        session.commit()

    # ERP-F6-fix4/fix7 — «a revisar» agrupado POR PEDIDO: contradicciones,
    # ambigüedades y coincidencias probables (lo que EXIGE una decisión de Bart).
    # Las facturas desconocidas NO entran aquí: son información.
    review_by_order: dict[str, dict[str, Any]] = {}
    for c in [*conflicts, *probable_matches]:
        key = c.get("order_number") or "(sin nº)"
        group = review_by_order.setdefault(
            key, {"order_number": c.get("order_number"), "items": []},
        )
        group["items"].append(c)
    review_groups = list(review_by_order.values())

    # ERP-F6-fix6 — traza COMPACTA de las llamadas a la API de Sheets (método,
    # endpoint, tamaño del payload). Las hojas en memoria (tests) no la exponen.
    api_trace = _compact_trace(getattr(sheets, "calls", None))

    summary = {
        "ok": True,
        "preview": dry_run,
        "orders_considered": len(rows),
        # ERP-F6-fix7: SOLO se añaden filas; nunca se actualiza una ya escrita.
        "appended_rows": len(new_rows),
        # Pedidos que ya estaban en la hoja (no se tocan): info de la preview.
        "already_present": len(present_rows),
        # Compat: la sincronización ya no actualiza nada (siempre 0).
        "updated_rows": 0,
        "updated_cells": 0,
        # Conflictos «duros» (contradicción, ambigüedad). Ya NO hay «celda
        # manual distinta»: al no actualizar, no hay nada que contrastar.
        "conflicts": conflicts,
        # Coincidencias probables (falta un dato para confirmar) — a revisar.
        "probable_matches": probable_matches,
        # ERP-F6-fix7 — facturas de la hoja que BoHub no conoce: INFORMACIÓN,
        # sección aparte, NO «a revisar».
        "unknown_invoices": unknown_invoices,
        # «A revisar» agrupado por pedido (contradicciones + probables).
        "review_groups": review_groups,
        "orders_to_review": len(review_groups),
        "sheet_rows": len(values),
        # ERP-F6-fix1: columnas opcionales ausentes en la hoja: se avisa.
        "omitted_columns": omitted_columns,
        # ERP-F6-fix6: qué llamadas se hicieron a la API (diagnóstico).
        "api_trace": api_trace,
    }
    logger.info(
        "drive sync%s: +%s filas nuevas, %s ya presentes, %s a revisar "
        "(%s conflictos, %s probables), %s facturas desconocidas, columnas "
        "omitidas: %s; API: %s",
        " (preview)" if dry_run else "", len(new_rows), len(present_rows),
        len(review_groups), len(conflicts), len(probable_matches),
        len(unknown_invoices), omitted_columns or "ninguna",
        api_trace or "sin llamadas (memoria)",
    )
    return summary


def _mark_written(
    session: Session,
    snapshots: dict[str, ErpDriveSyncRow],
    row: dict[str, Any],
    prefer_albaran: bool,
    now: datetime,
    last_values_json: str | None,
) -> None:
    """Marca un pedido como «escrito en Drive» (presente en la hoja). No escribe
    en la hoja: solo registra la foto de sincronización para que salga de
    «pendiente de escribir»."""
    key = reference_for_row(row, prefer_albaran=prefer_albaran)
    snap = snapshots.get(row["id"])
    if snap is None:
        snap = ErpDriveSyncRow(order_id=row["id"], row_key=key)
        session.add(snap)
        snapshots[row["id"]] = snap
    snap.row_key = key
    if last_values_json is not None:
        snap.last_values_json = last_values_json
    snap.synced_at = now


def _compact_trace(calls: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Resume las llamadas a la API en (método, endpoint, tamaño) para el log y
    el resumen, sin arrastrar los miles de rangos de celda individuales."""
    if not calls:
        return []
    out: list[dict[str, Any]] = []
    for c in calls:
        entry = {k: v for k, v in c.items() if k != "ranges"}
        ranges = c.get("ranges")
        if ranges:
            entry["sample_ranges"] = ranges[:3]
        out.append(entry)
    return out
