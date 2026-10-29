"""El extracto del banco, por Drive: BoHub lee la hoja de Bart sola y escribe
la factura y el pedido conciliados en la fila del movimiento.

Es el patrón de Seguimiento (`app/erp/drive_sheets.py`) aplicado a las hojas
del banco, con la MISMA cuenta de servicio (la de Configuración ERP): Bart
comparte cada hoja con su `client_email`, como Lector si solo se lee y como
Editor si además se escribe.

Leer («Traer de Drive» y el tic diario, `drive_job`):
  - una hoja de Google se exporta a .xlsx; un .xlsx subido se baja tal cual.
    Lo que se importa es ese .xlsx, con el lector de siempre
    (`service.import_statement`): solo cambia de dónde sale el fichero;
  - se lee la hoja ENTERA y entra lo que falta: la identidad de cada
    movimiento es la clave anti-duplicados del importador, que no depende de
    la fila, así que las filas nuevas por arriba no mueven nada.

Escribir (al confirmar una conciliación; `escribir_pendientes`):
  - solo en una hoja de Google: a un .xlsx no se le puede escribir una celda
    suelta sin subir el fichero entero (y pisar lo que Bart tenga abierto).
    Con un .xlsx, BoHub lee y la pantalla avisa de que hay que convertirlo;
  - la fila se busca por la identidad del movimiento, nunca por su número
    (la hoja crece por arriba). Si ya no está, no se escribe nada y queda
    constancia (`hoja_estado = no_encontrada`);
  - solo se rellena lo VACÍO. Una celda con algo (aunque sea una nota) no se
    toca; la única excepción es lo que escribió el propio BoHub
    (`hoja_factura` / `hoja_pedido`), como en Seguimiento. Nunca se borran
    filas ni se reordena nada: este módulo solo escribe celdas sueltas;
  - justo antes de escribir se vuelven a leer esas celdas y las de la
    identidad de la fila: si la fila se ha movido entretanto, no se escribe y
    se reintenta en la siguiente pasada.

Datos de clientes: las hojas llevan nombres, direcciones e IBAN de terceros.
Nada de eso va a los registros ni a los mensajes: solo cuentas, filas,
recuentos e identificadores de movimiento.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.erp.bank.parsing import (
    Identidades,
    ParsedStatement,
    ParseError,
    cuenta_casa,
    interpretar,
    parse_amount,
    read_statement,
    texto,
)
from app.erp.bank.service import (
    HOJA_PENDIENTE,
    _cuenta_del_extracto,
    _formatos_de_cuentas,
    claves_de_movimientos,
    datos_de_identidad,
    import_statement,
    movimientos_de_la_cuenta,
    valores_conciliados,
)
from app.erp.models import BankAccount, BankMovement

logger = logging.getLogger(__name__)

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
_DRIVE_API = "https://www.googleapis.com/drive/v3/files"
_SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
_TIMEOUT = 60
#: Rangos por lectura (van en la URL de `values:batchGet`).
_RANGOS_POR_LECTURA = 80

MIME_HOJA = "application/vnd.google-apps.spreadsheet"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: `BankAccount.drive_formato`: hoja de Google (se puede escribir) o .xlsx.
FORMATO_HOJA = "hoja"
FORMATO_XLSX = "xlsx"

#: `BankMovement.hoja_estado`, además de `service.HOJA_PENDIENTE`.
HOJA_ESCRITA = "escrita"
HOJA_OCUPADA = "ocupada"
HOJA_NO_ENCONTRADA = "no_encontrada"
HOJA_CORRIDA = "corrida"
HOJA_SIN_COLUMNA = "sin_columna"

ORIGEN_MANUAL = "manual"
ORIGEN_PROGRAMADA = "programada"
ORIGEN_CONCILIAR = "conciliar"

XLSX_NO_SE_ESCRIBE = (
    "El fichero de Drive es un .xlsx subido: BoHub lo lee, pero no puede escribir en él "
    "sin subirlo entero y pisar lo que haya abierto. Para que escriba la factura y el "
    "pedido, conviértelo (Archivo → Guardar como hoja de cálculo de Google), comparte la "
    "hoja nueva con la cuenta de servicio como Editor y pon aquí su enlace."
)


class DriveBancoError(RuntimeError):
    """No se ha podido leer o escribir la hoja. El mensaje es para la
    pantalla: en castellano, sin datos de la hoja ni de las credenciales."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# --- la cuenta de servicio (la de Seguimiento) ------------------------------------


def cuenta_de_servicio(session: Session) -> dict[str, Any] | None:
    """Las credenciales de la cuenta de servicio de Configuración ERP (las de
    Seguimiento), o None si no están. Nunca se registran ni se enseñan."""
    from app.core.crypto import decrypt  # noqa: PLC0415
    from app.erp.drive_sheets import parse_service_account_json  # noqa: PLC0415
    from app.erp.models import ErpSettings  # noqa: PLC0415
    from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID  # noqa: PLC0415

    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is None or not cfg.drive_service_account_json_encrypted:
        return None
    try:
        return parse_service_account_json(decrypt(cfg.drive_service_account_json_encrypted))
    except Exception as exc:  # noqa: BLE001 — jamás el contenido de las credenciales
        raise DriveBancoError(
            "credenciales",
            "Las credenciales de la cuenta de servicio no se pueden leer: vuelve a pegarlas "
            "en Configuración ERP.",
        ) from exc


def email_cuenta_de_servicio(session: Session) -> str | None:
    """El `client_email`: lo único de las credenciales que se enseña (es con
    quien Bart comparte la hoja)."""
    try:
        info = cuenta_de_servicio(session)
    except DriveBancoError:
        return None
    return str(info.get("client_email")) if info else None


# --- la hoja ------------------------------------------------------------------------


class HojaBanco(Protocol):
    """Lo que BoHub hace con la hoja de una cuenta. `GoogleHojaBanco` contra
    Google; los tests, contra una hoja en memoria."""

    def descargar(self) -> tuple[bytes, str]:
        """El libro como .xlsx y qué es el fichero (`FORMATO_HOJA` / `_XLSX`)."""

    def pestanas(self) -> list[str]:
        """Las pestañas de la hoja de Google, en orden."""

    def leer_celdas(self, rangos: list[str]) -> list[Any]:
        """El valor de cada celda (rango A1 de una celda), sin formatear."""

    def escribir_celdas(self, celdas: list[tuple[str, str]]) -> None:
        """Escribe cada (rango A1 de una celda, texto) tal cual, de una vez."""

    def insertar_filas(self, pestana: str, fila: int, filas: list[list[Any]]) -> None:
        """Inserta `filas` (ya con sus valores) a partir de la fila `fila`
        (1-based) de la pestaña, empujando hacia abajo lo que había."""


class GoogleHojaBanco:
    """La hoja de Drive de una cuenta, con la cuenta de servicio. Lee por la
    API de Drive (exportar / bajar), escribe celdas sueltas por la de Sheets
    (`values:batchUpdate`, RAW) e inserta filas nuevas (`batchUpdate`). No
    borra nada."""

    def __init__(self, info: dict[str, Any], file_id: str):
        self.info = info
        self.file_id = file_id
        self.email = str(info.get("client_email") or "la cuenta de servicio")
        self._token: str | None = None

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            from app.erp.drive_sheets import (  # noqa: PLC0415
                DriveSyncError,
                _service_account_token,
            )

            try:
                self._token = _service_account_token(
                    self.info, [DRIVE_READONLY_SCOPE, SHEETS_SCOPE],
                )
            except DriveSyncError as exc:
                raise DriveBancoError("credenciales", str(exc)) from exc
        return {"Authorization": f"Bearer {self._token}"}

    def _error(self, status: int, *, escribiendo: bool = False) -> DriveBancoError:
        """El error de Google, en castellano. Nunca el cuerpo de la respuesta."""
        if status == 404:
            return DriveBancoError(
                "no_existe",
                f"Drive no encuentra la hoja {self.file_id}: o no existe, o no está "
                f"compartida con {self.email}.",
            )
        if status in (401, 403) and escribiendo:
            return DriveBancoError(
                "solo_lectura",
                f"{self.email} solo puede leer la hoja: compártela con ella como Editor para "
                "que BoHub escriba la factura y el pedido.",
            )
        if status in (401, 403):
            return DriveBancoError(
                "no_compartida",
                f"{self.email} no puede abrir la hoja: compártela con ella (Lector para leer; "
                "Editor si además BoHub escribe la factura y el pedido).",
            )
        return DriveBancoError(
            "drive_caido",
            f"Drive no responde ahora (HTTP {status}). Prueba en un rato; mientras, el "
            "fichero a mano sigue funcionando.",
        )

    def _pedir(self, method: str, url: str, *, escribiendo: bool = False, **kwargs: Any) -> Any:
        import requests  # noqa: PLC0415

        try:
            r = requests.request(
                method, url, headers=self._headers(), timeout=_TIMEOUT, **kwargs,
            )
        except requests.RequestException as exc:
            raise DriveBancoError(
                "drive_caido",
                f"Drive no responde ahora ({type(exc).__name__}). Prueba en un rato; "
                "mientras, el fichero a mano sigue funcionando.",
            ) from exc
        if r.status_code >= 400:
            raise self._error(r.status_code, escribiendo=escribiendo)
        return r

    def descargar(self) -> tuple[bytes, str]:
        meta = self._pedir(
            "GET", f"{_DRIVE_API}/{self.file_id}",
            params={"fields": "mimeType", "supportsAllDrives": "true"},
        ).json()
        mime = str(meta.get("mimeType") or "")
        if mime == MIME_HOJA:
            r = self._pedir(
                "GET", f"{_DRIVE_API}/{self.file_id}/export", params={"mimeType": MIME_XLSX},
            )
            return r.content, FORMATO_HOJA
        if mime == MIME_XLSX:
            r = self._pedir(
                "GET", f"{_DRIVE_API}/{self.file_id}",
                params={"alt": "media", "supportsAllDrives": "true"},
            )
            return r.content, FORMATO_XLSX
        raise DriveBancoError(
            "no_es_hoja",
            "El fichero de Drive no es una hoja de cálculo (ni hoja de Google ni .xlsx).",
        )

    def pestanas(self) -> list[str]:
        data = self._pedir(
            "GET", f"{_SHEETS_API}/{self.file_id}",
            params={"fields": "sheets.properties.title"},
        ).json()
        return [str(s["properties"]["title"]) for s in data.get("sheets", [])]

    def leer_celdas(self, rangos: list[str]) -> list[Any]:
        out: list[Any] = []
        # Por tandas: cada rango va en la URL, y una conciliación en bloque
        # deja cientos de celdas por leer.
        for i in range(0, len(rangos), _RANGOS_POR_LECTURA):
            data = self._pedir(
                "GET", f"{_SHEETS_API}/{self.file_id}/values:batchGet",
                params=[("ranges", r) for r in rangos[i:i + _RANGOS_POR_LECTURA]] + [
                    ("valueRenderOption", "UNFORMATTED_VALUE"),
                    ("dateTimeRenderOption", "SERIAL_NUMBER"),
                ],
            ).json()
            for vr in data.get("valueRanges", []):
                values = vr.get("values") or [[]]
                out.append(values[0][0] if values and values[0] else None)
        return out

    def escribir_celdas(self, celdas: list[tuple[str, str]]) -> None:
        if not celdas:
            return
        self._pedir(
            "POST", f"{_SHEETS_API}/{self.file_id}/values:batchUpdate", escribiendo=True,
            json={
                "valueInputOption": "RAW",
                "data": [{"range": rango, "values": [[valor]]} for rango, valor in celdas],
            },
        )

    def insertar_filas(self, pestana: str, fila: int, filas: list[list[Any]]) -> None:
        if not filas:
            return
        data = self._pedir(
            "GET", f"{_SHEETS_API}/{self.file_id}",
            params={"fields": "sheets.properties(sheetId,title)"},
        ).json()
        sheet_id = next(
            (s["properties"]["sheetId"] for s in data.get("sheets", [])
             if s["properties"].get("title") == pestana),
            None,
        )
        if sheet_id is None:
            raise DriveBancoError("sin_pestana", f"La hoja no tiene la pestaña «{pestana}».")
        # Insertar y rellenar en la MISMA petición: o entra todo o nada (nunca
        # quedan filas en blanco a medias). Las filas nuevas heredan el formato
        # de la de debajo (la primera de datos): fechas e importes igual.
        self._pedir(
            "POST", f"{_SHEETS_API}/{self.file_id}:batchUpdate", escribiendo=True,
            json={"requests": [
                {"insertDimension": {
                    "range": {"sheetId": sheet_id, "dimension": "ROWS",
                              "startIndex": fila - 1, "endIndex": fila - 1 + len(filas)},
                    "inheritFromBefore": False,
                }},
                {"updateCells": {
                    "start": {"sheetId": sheet_id, "rowIndex": fila - 1, "columnIndex": 0},
                    "rows": [{"values": [_celda_de_google(v) for v in f]} for f in filas],
                    "fields": "userEnteredValue",
                }},
            ]},
        )


def _celda_de_google(valor: Any) -> dict[str, Any]:
    """Un valor tal cual (RAW): un texto que empiece por «=» no es fórmula."""
    if valor is None or valor == "":
        return {}
    if isinstance(valor, bool):
        return {"userEnteredValue": {"stringValue": str(valor)}}
    if isinstance(valor, (int, float)):
        return {"userEnteredValue": {"numberValue": valor}}
    return {"userEnteredValue": {"stringValue": str(valor)}}


def abrir_hoja(session: Session, account: BankAccount) -> HojaBanco:
    """La hoja de Drive de la cuenta, con la cuenta de servicio."""
    if not account.drive_file_id:
        raise DriveBancoError("sin_hoja", f"La cuenta {account.name} no tiene hoja de Drive.")
    info = cuenta_de_servicio(session)
    if info is None:
        raise DriveBancoError(
            "sin_cuenta_de_servicio",
            "Falta la cuenta de servicio de Google: un admin la pega en Configuración ERP "
            "(la misma de Seguimiento).",
        )
    return GoogleHojaBanco(info, account.drive_file_id)


# --- un pase a la vez por cuenta -------------------------------------------------------

#: Cerrojo por cuenta, compartido por el botón, el tic diario y la escritura.
LOCK_KEY = "bank:drive:running:{account_id}"
LOCK_TTL_SECONDS = 600


@contextmanager
def cerrojo(account_id: str) -> Iterator[bool]:
    """Cede True si se ha cogido (o si no hay Redis) y False si ya hay otro
    pase de Drive en marcha para esa cuenta."""
    clave = LOCK_KEY.format(account_id=account_id)
    try:
        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        token = str(uuid4())
        cogido = bool(conn.set(clave, token, nx=True, ex=LOCK_TTL_SECONDS))
    except Exception:  # noqa: BLE001 — Redis caído: se sigue sin cerrojo
        yield True
        return
    if not cogido:
        yield False
        return
    try:
        yield True
    finally:
        try:
            actual = conn.get(clave)
            if actual is not None and actual.decode() == token:
                conn.delete(clave)
        except Exception:  # noqa: BLE001
            logger.warning("bank.drive: no se pudo soltar el cerrojo de %s", account_id)


# --- leer ------------------------------------------------------------------------------


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _guardar_resultado(account: BankAccount, resultado: dict[str, Any]) -> None:
    """La última pasada, para la pantalla. Solo recuentos: ni filas ni nombres."""
    account.drive_resultado_json = json.dumps(resultado, default=str)


def traer_de_drive(
    session: Session, account: BankAccount, *, user_id: str | None = None,
    origen: str = ORIGEN_MANUAL, hoja: HojaBanco | None = None,
) -> dict[str, Any]:
    """Lee la hoja de la cuenta entera e importa lo que falta, con el lector
    de siempre; después escribe lo pendiente si la cuenta escribe en su hoja.
    Devuelve el resumen del import (`service.import_statement`) más `drive`.
    Lanza `DriveBancoError` si Drive falla (y lo deja anotado en la cuenta) y
    `ParseError` si la hoja no tiene la forma de un extracto."""
    momento = _ahora()
    try:
        hoja = hoja or abrir_hoja(session, account)
        contenido, formato = hoja.descargar()
    except DriveBancoError as exc:
        session.rollback()
        _guardar_resultado(account, {
            "origen": origen, "at": momento.isoformat(), "ok": False,
            "code": exc.code, "error": str(exc),
        })
        session.commit()
        logger.info("bank.drive: cuenta %s sin leer (%s)", account.id, exc.code)
        raise
    account.drive_formato = formato
    try:
        resumen = import_statement(
            session, content=contenido, filename=f"Drive · {account.name}.xlsx",
            account_id=account.id, user_id=user_id, pestana=account.drive_pestana,
        )
    except ParseError as exc:
        session.rollback()
        _guardar_resultado(account, {
            "origen": origen, "at": momento.isoformat(), "ok": False,
            "code": "parse_error", "error": str(exc),
        })
        session.commit()
        raise
    escritura: dict[str, Any] | None = None
    if resumen.get("ok") and account.drive_escribir:
        try:
            escritura = escribir_pendientes(session, account, hoja=hoja, contenido=contenido)
        except (DriveBancoError, ParseError) as exc:
            # La lectura ya está importada (y guardada): un fallo al escribir
            # se dice aparte y lo pendiente sigue pendiente.
            escritura = {"error": str(exc), "code": getattr(exc, "code", "parse_error")}
            logger.info("bank.drive: cuenta %s leída pero sin escribir (%s)", account.id,
                        escritura["code"])
        escritura = {**escritura, "origen": origen, "at": momento.isoformat()}
    drive = {
        "origen": origen,
        "at": momento.isoformat(),
        "ok": bool(resumen.get("ok")),
        "code": resumen.get("code"),
        "error": None if resumen.get("ok") else resumen.get("detail"),
        "formato": formato,
        "imported": resumen.get("imported", 0),
        "duplicates": resumen.get("duplicates", 0),
        "no_entendidas_total": resumen.get("no_entendidas_total", 0),
        "recolocadas": resumen.get("recolocadas", 0),
        "escritura": escritura,
    }
    if resumen.get("ok"):
        account.drive_leida_at = momento
    _guardar_resultado(account, drive)
    session.commit()
    logger.info(
        "bank.drive: cuenta %s leída (%s, %s): %s nuevos, %s repetidos, %s no entendidas",
        account.id, origen, formato, drive["imported"], drive["duplicates"],
        drive["no_entendidas_total"],
    )
    return {**resumen, "drive": drive}


# --- escribir ----------------------------------------------------------------------------


def _a1(fila: int, col0: int) -> str:
    """(fila 1-based, columna 0-based) → «H54»."""
    letras = ""
    n = col0 + 1
    while n:
        n, resto = divmod(n - 1, 26)
        letras = chr(65 + resto) + letras
    return f"{letras}{fila}"


def _rango(pestana: str, fila: int, col0: int) -> str:
    return "'" + pestana.replace("'", "''") + "'!" + _a1(fila, col0)


def _columna(parsed: ParsedStatement, campo: str) -> int | None:
    """La columna (0-based) de un campo en la hoja; la última si se repite (es
    la que lee el importador)."""
    nombre = parsed.mapping.get(campo)
    if not nombre or nombre not in parsed.columns:
        return None
    return len(parsed.columns) - 1 - parsed.columns[::-1].index(nombre)


def _vacia(valor: Any) -> bool:
    return texto(valor) is None


def _igual(valor: Any, esperado: str | None) -> bool:
    return bool(esperado) and (texto(valor) or "") == esperado.strip()


def _misma_fila(vivos: list[Any], esperados: list[Any]) -> bool:
    """¿Las celdas de identidad (importe, saldo, concepto, referencia) siguen
    siendo las del movimiento? Si Bart ha insertado filas entretanto, no."""
    for vivo, esperado in zip(vivos, esperados, strict=True):
        if isinstance(esperado, float):
            try:
                if abs(parse_amount(vivo) - esperado) > 0.005:
                    return False
            except ValueError:
                return False
        elif (texto(vivo) or "") != (texto(esperado) or ""):
            return False
    return True


#: Estados que se reintentan en cada pasada: lo pendiente y lo que no se pudo
#: escribir por cómo estaba la hoja (la fila aún no estaba, estaba corrida o
#: sin columna). Una celda `ocupada` por Bart no se reintenta.
_POR_ESCRIBIR = (HOJA_PENDIENTE, HOJA_NO_ENCONTRADA, HOJA_CORRIDA, HOJA_SIN_COLUMNA)


def _fijar(
    session: Session, mov: BankMovement, version: datetime | None, estado: str | None,
) -> bool:
    """Pone el estado de la escritura SOLO si nadie lo ha tocado desde que se
    leyó (`hoja_estado_at` = `version`): si mientras tanto se reasignó o se
    reabrió el movimiento, gana eso y la siguiente pasada lo vuelve a mirar."""
    q = update(BankMovement).where(BankMovement.id == mov.id)
    q = q.where(BankMovement.hoja_estado_at == version) if version is not None else q.where(
        BankMovement.hoja_estado_at.is_(None))
    r = session.execute(
        q.values(hoja_estado=estado, hoja_estado_at=_ahora())
        .execution_options(synchronize_session=False)
    )
    return bool(r.rowcount)


def escribir_pendientes(
    session: Session, account: BankAccount, *, hoja: HojaBanco | None = None,
    contenido: bytes | None = None,
) -> dict[str, Any]:
    """Escribe en la hoja la factura y el pedido de lo conciliado pendiente
    de la cuenta, cada uno en la fila de su movimiento y solo en celdas vacías
    (o con lo que escribió el propio BoHub). Deja constancia en cada
    movimiento (`hoja_estado`) y no hace commit. `contenido`: el .xlsx recién
    bajado, si ya se tiene (se ahorra una descarga)."""
    stats: dict[str, Any] = {
        "pendientes": 0, "escritas": 0, "ocupadas": 0, "no_encontradas": 0,
        "corridas": 0, "sin_columna": 0, "movidas": 0, "error": None,
    }
    candidatos = list(session.scalars(select(BankMovement).where(
        BankMovement.account_id == account.id, BankMovement.hoja_estado.in_(_POR_ESCRIBIR),
    )))
    stats["pendientes"] = len(candidatos)
    if not candidatos or not account.drive_file_id or not account.drive_escribir:
        return stats
    version = {m.id: m.hoja_estado_at for m in candidatos}
    hoja = hoja or abrir_hoja(session, account)
    if contenido is None:
        contenido, account.drive_formato = hoja.descargar()
    if account.drive_formato == FORMATO_XLSX:
        stats["error"] = XLSX_NO_SE_ESCRIBE
        return stats

    parsed = read_statement(
        contenido, "hoja.xlsx", extra=_formatos_de_cuentas(session, account.id),
        pestana=account.drive_pestana,
    )
    movimientos, _ = movimientos_de_la_cuenta(interpretar(parsed, parsed.mapping), account)
    por_clave: dict[str, int] = {}
    for i, clave in enumerate(claves_de_movimientos(account.id, movimientos)):
        por_clave.setdefault(clave, i)
    # Si la fila ya no da la misma clave (p. ej. el concepto entró entero del
    # export del banco y en la hoja está cortado), se busca por identidad.
    identidades = Identidades(list(enumerate(movimientos)))
    for mov in candidatos:
        if mov.dedupe_key in por_clave:
            identidades.usar(por_clave[mov.dedupe_key])
    col_fac, col_ped = _columna(parsed, "factura"), _columna(parsed, "pedido")
    col_imp, col_sal = _columna(parsed, "importe"), _columna(parsed, "saldo")
    col_con, col_ref = _columna(parsed, "concepto"), _columna(parsed, "referencia1")
    valores = valores_conciliados(session, [m.id for m in candidatos])

    plan: list[tuple[BankMovement, dict[str, Any]]] = []
    for mov in candidatos:
        if mov.status != "reconciled" or mov.id not in valores:
            _fijar(session, mov, version[mov.id], None)   # ya no hay nada que escribir
            continue
        i = por_clave.get(mov.dedupe_key)
        if i is None:
            i = identidades.buscar(datos_de_identidad(mov))
        fila = movimientos[i] if i is not None else None
        if fila is None:
            if mov.hoja_estado != HOJA_NO_ENCONTRADA:
                logger.info(
                    "bank.drive: el movimiento %s no está en la hoja de la cuenta %s; "
                    "no se escribe", mov.id, account.id,
                )
            _fijar(session, mov, version[mov.id], HOJA_NO_ENCONTRADA)
            stats["no_encontradas"] += 1
            continue
        if fila["recolocada"]:
            # La fila está corrida en la hoja: su `factura` no está en su columna.
            _fijar(session, mov, version[mov.id], HOJA_CORRIDA)
            stats["corridas"] += 1
            continue
        if col_fac is None:
            _fijar(session, mov, version[mov.id], HOJA_SIN_COLUMNA)
            stats["sin_columna"] += 1
            continue
        plan.append((mov, fila))
    if not plan:
        return stats

    pestana = account.drive_pestana or hoja.pestanas()[0]
    identidad = [c for c in (col_imp, col_sal, col_con, col_ref) if c is not None]
    columnas = [col_fac, *([col_ped] if col_ped is not None else []), *identidad]
    vivos = hoja.leer_celdas([
        _rango(pestana, fila["source_row"], c) for _, fila in plan for c in columnas
    ])

    def esperado(fila: dict[str, Any], c: int) -> Any:
        if c == col_imp:
            return float(fila["importe"])
        if c == col_sal and fila["saldo"] is not None:
            return float(fila["saldo"])
        return fila["raw"].get(parsed.columns[c])

    escribir: list[tuple[str, str]] = []
    hechos: list[tuple[BankMovement, str | None, str | None, bool]] = []
    n = len(columnas)
    for i, (mov, fila) in enumerate(plan):
        celdas = dict(zip(columnas, vivos[i * n:(i + 1) * n], strict=True))
        if not _misma_fila([celdas[c] for c in identidad], [esperado(fila, c) for c in identidad]):
            stats["movidas"] += 1   # se queda como estaba: la próxima pasada la encuentra
            continue
        factura, pedido = valores[mov.id]
        vivo_fac = celdas[col_fac]
        # Lo que trae la fila en `factura`, con la de continuación (Sabadell):
        # si Bart anotó algo, aunque sea abajo, la celda no se toca.
        anotado = fila.get("factura")
        propio_o_igual = (
            (_vacia(vivo_fac) or _igual(vivo_fac, mov.hoja_factura) or _igual(vivo_fac, factura))
            and (not anotado or anotado in (mov.hoja_factura, factura))
        )
        if not propio_o_igual:
            _fijar(session, mov, version[mov.id], HOJA_OCUPADA)   # algo de Bart: no se toca
            stats["ocupadas"] += 1
            continue
        fila_hoja = fila["source_row"]
        escrita_fac: str | None = None
        if not _igual(vivo_fac, factura):
            escribir.append((_rango(pestana, fila_hoja, col_fac), factura))
            escrita_fac = factura
        escrita_ped: str | None = None
        cambia_ped = False
        if col_ped is not None:
            vivo_ped = celdas[col_ped]
            anotado_ped = fila.get("pedido")
            libre = _vacia(vivo_ped) and not anotado_ped
            propio = _igual(vivo_ped, mov.hoja_pedido)
            if (libre and pedido) or (propio and (pedido or "") != (mov.hoja_pedido or "")):
                # Vacía: el pedido. Con lo que escribió BoHub: el pedido nuevo,
                # o vacía si la factura nueva no tiene pedido en BoHub.
                escribir.append((_rango(pestana, fila_hoja, col_ped), pedido or ""))
                escrita_ped, cambia_ped = pedido, True
        hechos.append((mov, escrita_fac, escrita_ped, cambia_ped))
    hoja.escribir_celdas(escribir)
    for mov, escrita_fac, escrita_ped, cambia_ped in hechos:
        # Lo que hay ahora en la hoja es de BoHub: se guarda siempre.
        valores_hoja: dict[str, Any] = {}
        if escrita_fac:
            valores_hoja["hoja_factura"] = escrita_fac
        if cambia_ped:
            valores_hoja["hoja_pedido"] = escrita_ped
        if valores_hoja:
            session.execute(
                update(BankMovement).where(BankMovement.id == mov.id).values(**valores_hoja)
                .execution_options(synchronize_session=False)
            )
        if _fijar(session, mov, version[mov.id], HOJA_ESCRITA):
            stats["escritas"] += 1
        else:
            stats["movidas"] += 1   # se reasignó mientras se escribía: se repasa
    if stats["escritas"] or stats["ocupadas"]:
        logger.info(
            "bank.drive: cuenta %s: %s filas escritas en la hoja, %s con la celda ocupada",
            account.id, stats["escritas"], stats["ocupadas"],
        )
    return stats


def escribir_cuenta(
    session: Session, account: BankAccount, *, origen: str = ORIGEN_CONCILIAR,
    hoja: HojaBanco | None = None,
) -> dict[str, Any]:
    """Escribe lo pendiente de una cuenta (lo encola `drive_job` al confirmar)
    y lo deja anotado en la cuenta, con el error si Drive falla. Hace commit."""
    momento = _ahora()
    try:
        stats = escribir_pendientes(session, account, hoja=hoja)
    except (DriveBancoError, ParseError) as exc:
        session.rollback()
        stats = {"error": str(exc)}
        logger.info("bank.drive: cuenta %s sin escribir (%s)", account.id,
                    getattr(exc, "code", "parse_error"))
    anterior = json.loads(account.drive_resultado_json) if account.drive_resultado_json else {}
    _guardar_resultado(account, {
        **anterior, "escritura": {**stats, "origen": origen, "at": momento.isoformat()},
    })
    session.commit()
    return stats


# --- añadir a la hoja lo que trae el export del banco --------------------------------

#: Campos del extracto que se copian del export a la fila nueva de la hoja.
#: `factura` y `pedido` se quedan vacías: las rellena la conciliación.
_CAMPOS_EXPORT = (
    "fecha_oper", "concepto", "fecha_valor", "importe", "saldo", "referencia1", "referencia2",
)
_FECHAS = ("fecha_oper", "fecha_valor")
#: El día 0 de las fechas de una hoja de cálculo.
_EPOCA_HOJA = datetime(1899, 12, 30).date()


def _leer_export(
    session: Session, account: BankAccount, contenido: bytes, nombre: str,
) -> list[dict[str, Any]]:
    """Los movimientos del fichero del banco que son de esta cuenta. Lanza
    `ParseError` si no es un extracto y `DriveBancoError` si es de otra."""
    parsed = read_statement(contenido, nombre, extra=_formatos_de_cuentas(session, account.id))
    lectura = interpretar(parsed, parsed.mapping)
    numero = _cuenta_del_extracto(parsed, lectura)
    if numero and not cuenta_casa(numero, account.iban):
        raise DriveBancoError(
            "otra_cuenta",
            f"El fichero es de la cuenta {numero}, no de {account.name} ({account.iban}).",
        )
    movimientos, _ = movimientos_de_la_cuenta(lectura, account)
    return movimientos


def _leer_hoja_para_anadir(
    session: Session, account: BankAccount, hoja: HojaBanco,
) -> tuple[ParsedStatement, list[dict[str, Any]]]:
    contenido, formato = hoja.descargar()
    account.drive_formato = formato
    if formato == FORMATO_XLSX:
        raise DriveBancoError("xlsx", XLSX_NO_SE_ESCRIBE)
    parsed = read_statement(
        contenido, "hoja.xlsx", extra=_formatos_de_cuentas(session, account.id),
        pestana=account.drive_pestana,
    )
    movimientos, _ = movimientos_de_la_cuenta(interpretar(parsed, parsed.mapping), account)
    return parsed, movimientos


def _faltan(
    export: list[dict[str, Any]], en_hoja: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Los del export que no están en la hoja, de más reciente a más antiguo
    (en el mismo día, en el orden en que los da el banco)."""
    identidades = Identidades(list(enumerate(en_hoja)))
    nuevos = [m for m in export if identidades.buscar(m) is None]
    if len(export) > 1 and export[0]["fecha_oper"] < export[-1]["fecha_oper"]:
        nuevos.reverse()   # el banco los da del más antiguo al más reciente
    return sorted(nuevos, key=lambda m: m["fecha_oper"], reverse=True)


def _vista(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "fecha_oper": m["fecha_oper"].isoformat(),
        "concepto": m["concepto"],
        "importe": float(m["importe"]),
        "saldo": float(m["saldo"]) if m["saldo"] is not None else None,
    }


def _fila_nueva(
    m: dict[str, Any], parsed: ParsedStatement, muestra: dict[str, Any] | None,
) -> list[Any]:
    """La fila de la hoja para un movimiento del export, en las columnas de
    la hoja. Una fecha va como fecha de hoja (número de serie, que con el
    formato heredado se ve como las demás) salvo que la hoja las tenga como
    texto: entonces como «dd/mm/aaaa»."""
    fila: list[Any] = [None] * len(parsed.columns)
    for campo in _CAMPOS_EXPORT:
        col = _columna(parsed, campo)
        valor = m.get(campo)
        if col is None or valor is None:
            continue
        if campo in _FECHAS:
            como_texto = muestra is not None and isinstance(
                muestra.get(parsed.columns[col]), str)
            valor = (valor.strftime("%d/%m/%Y") if como_texto
                     else (valor - _EPOCA_HOJA).days)
        elif campo in ("importe", "saldo"):
            valor = float(valor)
        fila[col] = valor
    return fila


def anadir_export(
    session: Session, account: BankAccount, *, contenido: bytes, nombre: str,
    aplicar: bool = False, user_id: str | None = None, hoja: HojaBanco | None = None,
) -> dict[str, Any]:
    """Compara el fichero del banco (tal cual lo da la web) con la hoja de
    Drive de la cuenta y dice qué falta (`aplicar=False`, la vista previa) o
    lo añade (`aplicar=True`): filas nuevas encima de la primera de datos, de
    la más reciente a la más antigua, con `factura` y `pedido` vacías. Nunca
    toca una fila que ya estaba. Después lee la hoja como «Traer de Drive»,
    para que lo añadido entre en BoHub.

    Un movimiento ya está si la hoja tiene uno que es el mismo
    (`mismo_movimiento`: fecha, importe y saldo, que no dependen de que la
    hoja corte el concepto). Lanza `DriveBancoError` / `ParseError`."""
    if not account.drive_file_id:
        raise DriveBancoError("sin_hoja", f"La cuenta {account.name} no tiene hoja de Drive.")
    export = _leer_export(session, account, contenido, nombre)
    hoja = hoja or abrir_hoja(session, account)
    parsed, en_hoja = _leer_hoja_para_anadir(session, account, hoja)
    nuevos = _faltan(export, en_hoja)
    resultado: dict[str, Any] = {
        "total": len(export), "ya": len(export) - len(nuevos), "nuevos": len(nuevos),
        "movimientos": [_vista(m) for m in nuevos], "aplicado": False,
    }
    if not aplicar or not nuevos:
        return resultado
    if _columna(parsed, "fecha_oper") is None or _columna(parsed, "importe") is None:
        raise DriveBancoError("sin_columna", "La hoja no tiene las columnas de fecha e importe.")
    primera = min((m["source_row"] for m in en_hoja), default=parsed.columns_row + 1)
    muestra = next((r for r in parsed.rows if r["__row__"] == primera), None)
    pestana = account.drive_pestana or hoja.pestanas()[0]
    hoja.insertar_filas(pestana, primera, [_fila_nueva(m, parsed, muestra) for m in nuevos])
    logger.info("bank.drive: cuenta %s: %s filas añadidas a la hoja desde el export",
                account.id, len(nuevos))
    resultado["aplicado"] = True
    resultado["importacion"] = traer_de_drive(session, account, user_id=user_id, hoja=hoja)
    return resultado
