"""El extracto del banco, por Drive (30/10/2026).

Las dos hojas del banco viven en el Drive de Bart y se actualizan casi a
diario: pega arriba los movimientos nuevos y, al conciliar, escribe la factura
en su columna. BoHub las lee solo («Traer de Drive» y el tic de las 05:20) y,
al confirmar una conciliación, escribe la factura y el pedido en la fila de
ese movimiento: solo en celdas vacías, sin borrar ni reordenar nada.

Google no se toca: `HojaFalsa` es una hoja en memoria con la misma interfaz
que `GoogleHojaBanco` (bajar el libro, leer y escribir celdas sueltas). Los
datos son FIGURANTES con la estructura de los extractos reales.
"""
from __future__ import annotations

import io
import json
import logging
import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.crypto import encrypt
from app.db.session import get_session
from app.erp.bank import drive, drive_job, service
from app.erp.bank.drive import (
    FORMATO_HOJA,
    FORMATO_XLSX,
    DriveBancoError,
    GoogleHojaBanco,
)
from app.erp.bank.parsing import interpretar, read_statement
from app.erp.models import BankMovement, ErpSettings
from app.erp.models.orders import Order, OrderSource
from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID
from app.main import app
from app.models.crm import Base
from tests._test_helpers import auth_headers, seed_test_users

STREAMTEC_IBAN = "ES91 2100 0418 4502 0005 1332"
STREAMTEC_CCC = "2100-0418-45-0200051332"
MQ_IBAN = "BE68 5390 0754 7034"
CUENTA_SERVICIO = "bohub-seguimiento@ejemplo.iam.gserviceaccount.com"
HOJA_ID = "1HojaDeEjemploDelBancoSabadell0001"
PESTANA = "Movimientos"
#: Columnas de la hoja de Sabadell (0-based): H = factura, I = pedido.
COL_FACTURA, COL_PEDIDO = 7, 8


def _d(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d)


def _sabadell(datos: list[list]) -> list[list]:
    """La rejilla de «Streamtec movimientos SABADELL»: preámbulo con la cuenta
    en CCC y `factura`/`pedido` rotuladas en la primera fila, la fila de
    columnas en la quinta, cuatro vacías y los movimientos (los nuevos, arriba)."""
    return [
        ["Cuenta: ", STREAMTEC_CCC, None, None, None, None, None, "factura", "pedido"],
        ["Divisa: ", "EUR"],
        ["Titular:", "STREAMTEC S.L"],
        ["Selección:"],
        ["FECHA OPER", "CONCEPTO", "FECHA VALOR", "IMPORTE", "SALDO", "REFERENCIA 1",
         "REFERENCIA 2"],
        [], [], [], [],
        *[list(f) for f in datos],
    ]


#: Figurantes. La fila 10 es la primera de datos.
DATOS = [
    [_d(2026, 10, 23), "ABONO TRANSFERENCIA DE GRAFICAS LLOBET SL", _d(2026, 10, 23), 1840.30,
     9842.11, None, "GRAFICAS LLOBET", "5-260121", "BOPRIN-100031"],
    [_d(2026, 10, 21), "ABONO TRANSFERENCIA DE IMPRENTA SOLER", _d(2026, 10, 21), 2310.00,
     8001.81, None, "IMPRENTA SOLER SL"],
    [_d(2026, 10, 20), "ABONO TRANSFERENCIA DE ROTULOS MARTI", _d(2026, 10, 20), 408.48,
     5691.81, None, "ROTULOS MARTI SL", "falta facturar"],
    [_d(2026, 10, 17), "ABONO TRANSFERENCIA DE PAPELERIA BOSCH", _d(2026, 10, 17), 96.80,
     5283.33, None, "PAPELERIA BOSCH"],
    [_d(2026, 10, 16), "SEGUROS SOCIALES REG GENERAL", _d(2026, 10, 16), -802.34, 5186.53,
     260701.0],
    [_d(2026, 10, 15), "ABONO TRANSFERENCIA DE TALLERES ROCA", _d(2026, 10, 15), 1210.00,
     5988.87, None, "TALLERES ROCA SA"],
]


def _libro(grid: list[list], pestana: str = PESTANA) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = pestana
    for fila in grid:
        ws.append(fila)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class HojaFalsa:
    """Una hoja de Drive en memoria con la interfaz de `GoogleHojaBanco`."""

    def __init__(self, grid: list[list], *, formato: str = FORMATO_HOJA,
                 pestana: str = PESTANA):
        self.grid = [list(f) for f in grid]
        self.formato = formato
        self.pestana = pestana
        self.descargas = 0
        self.escrituras: list[tuple[str, str]] = []
        self.fallo: DriveBancoError | None = None
        #: Algo que Bart hace en la hoja entre que BoHub la baja y escribe.
        self.antes_de_leer_celdas: Any = None

    def descargar(self) -> tuple[bytes, str]:
        if self.fallo:
            raise self.fallo
        self.descargas += 1
        return _libro(self.grid, self.pestana), self.formato

    def pestanas(self) -> list[str]:
        return [self.pestana]

    def _celda(self, rango: str) -> tuple[int, int]:
        pestana, a1 = rango.rsplit("!", 1)
        assert pestana == f"'{self.pestana}'", rango
        letras, fila = re.fullmatch(r"([A-Z]+)(\d+)", a1).groups()
        col = 0
        for ch in letras:
            col = col * 26 + ord(ch) - 64
        return int(fila) - 1, col - 1

    def leer_celdas(self, rangos: list[str]) -> list[Any]:
        if self.antes_de_leer_celdas:
            self.antes_de_leer_celdas(self)
            self.antes_de_leer_celdas = None
        out = []
        for rango in rangos:
            f, c = self._celda(rango)
            fila = self.grid[f] if f < len(self.grid) else []
            out.append(fila[c] if c < len(fila) else None)
        return out

    def escribir_celdas(self, celdas: list[tuple[str, str]]) -> None:
        assert self.formato == FORMATO_HOJA, "a un .xlsx no se le escribe una celda suelta"
        for rango, valor in celdas:
            f, c = self._celda(rango)
            fila = self.grid[f]
            fila.extend([None] * (c + 1 - len(fila)))
            fila[c] = valor
            self.escrituras.append((rango, valor))

    def fila_de(self, concepto: str) -> list[Any]:
        return next(f for f in self.grid if len(f) > 1 and f[1] == concepto)

    def insertar_arriba(self, filas: list[list]) -> None:
        """Bart pega movimientos nuevos encima de los de ayer."""
        self.grid[9:9] = [list(f) for f in filas]


# --- infraestructura -------------------------------------------------------------------


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(ErpSettings(
            id=ERP_SETTINGS_SINGLETON_ID,
            drive_service_account_json_encrypted=encrypt(json.dumps({
                "client_email": CUENTA_SERVICIO, "private_key": "clave-de-ejemplo",
            })),
        ))
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def cuenta(session_factory) -> str:
    with session_factory() as s:
        acc = service.create_account(s, {
            "name": "Sabadell Streamtec", "iban": STREAMTEC_IBAN, "serie": 5,
            "drive_file_id": f"https://docs.google.com/spreadsheets/d/{HOJA_ID}/edit#gid=0",
            "drive_pestana": PESTANA,
        })
        acc.contrapartida_codigo = "8"
        s.commit()
        return acc.id


@pytest.fixture()
def hoja(monkeypatch) -> HojaFalsa:
    """La hoja de Drive de la cuenta (en memoria) y la cola de escrituras
    anotada en vez de Redis."""
    h = HojaFalsa(_sabadell(DATOS))
    monkeypatch.setattr(drive, "abrir_hoja", lambda _s, _a: h)
    h.encoladas = []  # type: ignore[attr-defined]
    monkeypatch.setattr(drive_job, "encolar_escritura", h.encoladas.append)
    return h


def _traer(http: TestClient, cuenta: str) -> dict[str, Any]:
    r = http.post(f"/api/erp/bank/drive/traer?account_id={cuenta}&run_match=false",
                  headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    [resultado] = r.json()["resultados"]
    return resultado


def _mov(session_factory, concepto: str) -> BankMovement:
    with session_factory() as s:
        mov = s.scalar(select(BankMovement).where(BankMovement.concepto == concepto))
        assert mov is not None, concepto
        s.expunge(mov)
        return mov


def _elegir(http: TestClient, mov_id: str, codigo: int, importe: float) -> None:
    """«Elegir / repartir»: la persona concilia con una factura (serie 5)."""
    r = http.post(f"/api/erp/bank/movements/{mov_id}/reassign", json={"targets": [{
        "serie": 5, "codigo": codigo, "numero": f"5-{codigo}", "importe": importe,
        "cliente_nombre": "Cliente figurante",
    }]}, headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text


def _escribir(session_factory, cuenta: str) -> dict[str, Any]:
    """Lo que hace el trabajo encolado al confirmar."""
    with session_factory() as s:
        return drive.escribir_cuenta(s, s.get(service.BankAccount, cuenta))


# --- 1 · leer ------------------------------------------------------------------------------


def test_traer_de_drive_importa_la_hoja_y_no_duplica(http, session_factory, cuenta, hoja):
    r = _traer(http, cuenta)
    assert r["ok"], r
    assert (r["account_name"], r["imported"], r["duplicates"]) == ("Sabadell Streamtec", 6, 0)
    assert r["drive"]["formato"] == FORMATO_HOJA
    assert r["anotadas"]["confirmadas"] == 1          # «5-260121» venía anotado
    r = _traer(http, cuenta)
    assert (r["imported"], r["duplicates"]) == (0, 6)
    with session_factory() as s:
        assert s.scalar(select(func.count(BankMovement.id))) == 6
        acc = s.get(service.BankAccount, cuenta)
        assert acc.drive_file_id == HOJA_ID                 # del enlace, el identificador
        assert acc.drive_formato == FORMATO_HOJA and acc.drive_leida_at is not None
        assert json.loads(acc.drive_resultado_json)["duplicates"] == 6


def test_las_filas_nuevas_por_arriba_entran_y_no_mueven_lo_importado(
    http, session_factory, cuenta, hoja,
):
    _traer(http, cuenta)
    antes = {m.dedupe_key: m.id for m in _todos(session_factory)}
    hoja.insertar_arriba([
        [_d(2026, 10, 27), "ABONO TRANSFERENCIA DE CRISTALERIA PUIG", _d(2026, 10, 27), 650.00,
         12000.00, None, "CRISTALERIA PUIG"],
        [_d(2026, 10, 26), "ABONO TRANSFERENCIA DE LLIBRERIA COSTA", _d(2026, 10, 26), 1507.59,
         11350.00, None, "LLIBRERIA COSTA"],
    ])
    r = _traer(http, cuenta)
    assert (r["imported"], r["duplicates"]) == (2, 6)
    despues = {m.dedupe_key: m.id for m in _todos(session_factory)}
    assert all(despues[k] == v for k, v in antes.items())   # los de ayer, intactos
    # Y la escritura encuentra el de ayer en su fila nueva (dos más abajo).
    soler = _mov(session_factory, "ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    _elegir(http, soler.id, 260090, 2310.00)
    assert _escribir(session_factory, cuenta)["escritas"] == 1
    assert hoja.escrituras == [(f"'{PESTANA}'!H13", "5-260090")]


def _todos(session_factory) -> list[BankMovement]:
    with session_factory() as s:
        out = list(s.scalars(select(BankMovement)))
        for m in out:
            s.expunge(m)
        return out


# --- 2 · escribir ----------------------------------------------------------------------------


def test_confirmar_escribe_factura_y_pedido_en_la_fila_del_movimiento(
    http, session_factory, cuenta, hoja,
):
    _traer(http, cuenta)
    with session_factory() as s:
        s.add(Order(external_source=OrderSource.WOOCOMMERCE, order_number="BOPRIN-100044",
                    factusol_invoice_number="260090", factusol_invoice_serie=5))
        s.commit()
    antes = [list(f) for f in hoja.grid]
    soler = _mov(session_factory, "ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    _elegir(http, soler.id, 260090, 2310.00)
    assert hoja.encoladas == [cuenta]                     # se encola al confirmar
    assert _mov(session_factory, soler.concepto).hoja_estado == "pendiente"

    stats = _escribir(session_factory, cuenta)
    assert (stats["escritas"], stats["ocupadas"], stats["no_encontradas"]) == (1, 0, 0)
    fila = hoja.fila_de("ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    assert (fila[COL_FACTURA], fila[COL_PEDIDO]) == ("5-260090", "BOPRIN-100044")
    # El resto de la hoja, intacto: mismas filas, mismo orden, mismas celdas.
    assert len(hoja.grid) == len(antes)
    for i, (a, b) in enumerate(zip(antes, hoja.grid, strict=True)):
        if b is fila:
            continue
        assert (a + [None] * 9)[:9] == (b + [None] * 9)[:9], f"fila {i + 1}"
    mov = _mov(session_factory, soler.concepto)
    assert (mov.hoja_estado, mov.hoja_factura, mov.hoja_pedido) == (
        "escrita", "5-260090", "BOPRIN-100044")
    # La pantalla lo enseña.
    r = http.get(f"/api/erp/bank/movements?account_id={cuenta}&status=reconciled",
                 headers=auth_headers(http, "admin"))
    item = next(m for m in r.json()["items"] if m["id"] == soler.id)
    assert (item["hoja_estado"], item["hoja_factura"]) == ("escrita", "5-260090")


def test_una_celda_factura_con_algo_escrito_no_se_toca(http, session_factory, cuenta, hoja):
    _traer(http, cuenta)
    marti = _mov(session_factory, "ABONO TRANSFERENCIA DE ROTULOS MARTI")
    assert (marti.status, marti.anotacion_factura) == ("pending", "falta facturar")
    _elegir(http, marti.id, 260117, 408.48)            # la conciliación dice otra cosa
    stats = _escribir(session_factory, cuenta)
    assert (stats["escritas"], stats["ocupadas"]) == (0, 1)
    fila = hoja.fila_de("ABONO TRANSFERENCIA DE ROTULOS MARTI")
    assert fila[COL_FACTURA] == "falta facturar"       # la nota de Bart, intacta
    assert len(fila) <= COL_PEDIDO or fila[COL_PEDIDO] is None
    assert hoja.escrituras == []
    assert _mov(session_factory, marti.concepto).hoja_estado == "ocupada"


def test_si_la_fila_ya_no_esta_no_se_escribe_y_queda_constancia(
    http, session_factory, cuenta, hoja, caplog,
):
    _traer(http, cuenta)
    roca = _mov(session_factory, "ABONO TRANSFERENCIA DE TALLERES ROCA")
    _elegir(http, roca.id, 260101, 1210.00)
    hoja.grid.remove(hoja.fila_de("ABONO TRANSFERENCIA DE TALLERES ROCA"))   # Bart la borró
    with caplog.at_level(logging.INFO, logger="app.erp.bank.drive"):
        stats = _escribir(session_factory, cuenta)
    assert (stats["escritas"], stats["no_encontradas"]) == (0, 1)
    assert hoja.escrituras == []
    assert _mov(session_factory, roca.concepto).hoja_estado == "no_encontrada"
    assert roca.id in caplog.text
    # Ni nombres de pagadores ni conceptos en los registros.
    assert "ROCA" not in caplog.text and "TALLERES" not in caplog.text


def test_con_la_escritura_desactivada_se_lee_pero_no_se_escribe(
    http, session_factory, cuenta, hoja,
):
    r = http.patch(f"/api/erp/bank/accounts/{cuenta}", json={"drive_escribir": False},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200 and r.json()["drive_escribir"] is False
    assert _traer(http, cuenta)["imported"] == 6
    soler = _mov(session_factory, "ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    _elegir(http, soler.id, 260090, 2310.00)
    assert _mov(session_factory, soler.concepto).hoja_estado is None
    assert hoja.encoladas == []
    _traer(http, cuenta)
    _escribir(session_factory, cuenta)
    assert hoja.escrituras == []
    assert hoja.fila_de(soler.concepto)[COL_FACTURA:COL_FACTURA + 1] in ([], [None])


def test_si_drive_no_responde_la_pantalla_lo_dice_y_el_fichero_a_mano_sigue(
    http, session_factory, cuenta, hoja,
):
    hoja.fallo = DriveBancoError(
        "drive_caido", "Drive no responde ahora (HTTP 503). Prueba en un rato; mientras, el "
        "fichero a mano sigue funcionando.")
    r = _traer(http, cuenta)
    assert (r["ok"], r["code"]) == (False, "drive_caido")
    assert r["detail"].startswith("Drive no responde ahora")
    with session_factory() as s:
        resultado = json.loads(s.get(service.BankAccount, cuenta).drive_resultado_json)
        assert (resultado["ok"], resultado["code"]) == (False, "drive_caido")
    r = http.get("/api/erp/bank/accounts", headers=auth_headers(http, "admin"))
    assert r.json()["items"][0]["drive_resultado"]["error"].startswith("Drive no responde")
    # El fichero de siempre entra igual.
    with patch("app.integrations.factusol.client.FactusolClient.from_settings",
               side_effect=RuntimeError("sin FACTUSOL")):
        r = http.post("/api/erp/bank/import", files={"file": (
            "extracto.xlsx", _libro(_sabadell(DATOS)), "application/octet-stream")},
            headers=auth_headers(http, "admin"))
    assert r.status_code == 201, r.text
    assert r.json()["imported"] == 6


def test_un_xlsx_de_drive_se_lee_pero_no_se_escribe(http, session_factory, cuenta, hoja):
    hoja.formato = FORMATO_XLSX
    assert _traer(http, cuenta)["drive"]["formato"] == FORMATO_XLSX
    soler = _mov(session_factory, "ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    _elegir(http, soler.id, 260090, 2310.00)
    stats = _escribir(session_factory, cuenta)
    assert stats["error"].startswith("El fichero de Drive es un .xlsx subido")
    assert hoja.escrituras == []
    # Lo pendiente se queda: se escribe cuando la hoja sea de Google.
    assert _mov(session_factory, soler.concepto).hoja_estado == "pendiente"


def test_lo_que_escribio_bohub_no_vuelve_como_anotacion_de_bart(
    http, session_factory, cuenta, hoja,
):
    """Reabrir un movimiento cuya factura escribió BoHub no lo vuelve a casar
    al leer la hoja otra vez: eso no lo anotó Bart."""
    _traer(http, cuenta)
    soler = _mov(session_factory, "ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    _elegir(http, soler.id, 260090, 2310.00)
    _escribir(session_factory, cuenta)
    r = http.post(f"/api/erp/bank/movements/{soler.id}/reopen",
                  headers=auth_headers(http, "admin"))
    assert r.status_code == 200
    r = _traer(http, cuenta)
    assert r["anotadas"]["nuevas_en_reimportados"] == 0
    mov = _mov(session_factory, soler.concepto)
    assert (mov.status, mov.anotacion_factura) == ("pending", None)
    # Si se vuelve a conciliar con otra, BoHub sustituye lo suyo (solo lo suyo).
    _elegir(http, soler.id, 260095, 2310.00)
    _escribir(session_factory, cuenta)
    assert hoja.fila_de(soler.concepto)[COL_FACTURA] == "5-260095"


def test_si_la_fila_se_mueve_entre_leer_y_escribir_no_se_escribe(
    http, session_factory, cuenta, hoja,
):
    _traer(http, cuenta)
    soler = _mov(session_factory, "ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    _elegir(http, soler.id, 260090, 2310.00)
    # Justo después de bajar la hoja, Bart pega un movimiento arriba.
    hoja.antes_de_leer_celdas = lambda h: h.insertar_arriba([
        [_d(2026, 10, 28), "ABONO TRANSFERENCIA DE NUEVO", _d(2026, 10, 28), 10.0, 1.0],
    ])
    stats = _escribir(session_factory, cuenta)
    assert (stats["escritas"], stats["movidas"]) == (0, 1)
    assert hoja.escrituras == []
    assert _mov(session_factory, soler.concepto).hoja_estado == "pendiente"
    # La siguiente pasada la encuentra en su sitio nuevo.
    assert _escribir(session_factory, cuenta)["escritas"] == 1
    assert hoja.fila_de(soler.concepto)[COL_FACTURA] == "5-260090"


# --- configuración y cuenta de servicio ------------------------------------------------------


def test_la_pantalla_sabe_con_quien_compartir_la_hoja(http, cuenta, hoja):
    r = http.get("/api/erp/bank/drive", headers=auth_headers(http, "admin"))
    assert r.json() == {"configured": True, "service_account_email": CUENTA_SERVICIO,
                        "hora": "05:20"}
    r = http.get("/api/erp/bank/accounts", headers=auth_headers(http, "admin"))
    acc = r.json()["items"][0]
    assert (acc["drive_file_id"], acc["drive_pestana"], acc["drive_escribir"]) == (
        HOJA_ID, PESTANA, True)
    assert acc["drive_pendientes"] == 0
    # Lo que no es un enlace de Drive no se guarda.
    r = http.patch(f"/api/erp/bank/accounts/{cuenta}", json={"drive_file_id": "mi hoja"},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 409
    assert "no parece el enlace" in r.json()["detail"]["detail"]
    assert service.drive_file_id(
        "https://drive.google.com/file/d/1EjemploDeFicheroXlsxSubido00/view?usp=sharing",
    ) == "1EjemploDeFicheroXlsxSubido00"
    # Sin cuenta con hoja, «Traer de Drive» lo dice.
    http.patch(f"/api/erp/bank/accounts/{cuenta}", json={"drive_file_id": ""},
               headers=auth_headers(http, "admin"))
    r = http.post("/api/erp/bank/drive/traer", headers=auth_headers(http, "admin"))
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "sin_hoja")
    # Y quien no concilia no lo usa.
    r = http.post("/api/erp/bank/drive/traer", headers=auth_headers(http, "user"))
    assert r.status_code == 403


class _Resp:
    def __init__(self, status: int, body: Any = None, content: bytes = b""):
        self.status_code = status
        self._body = body or {}
        self.content = content
        self.text = json.dumps(self._body)

    def json(self) -> Any:
        return self._body


def test_el_cliente_de_google_baja_la_hoja_y_dice_los_errores_en_castellano(monkeypatch):
    llamadas: list[tuple[str, str, Any]] = []
    respuestas: list[_Resp] = []

    def request(method, url, **kw):
        llamadas.append((method, url, kw.get("params")))
        return respuestas.pop(0)

    monkeypatch.setattr("requests.request", request)
    monkeypatch.setattr("app.erp.drive_sheets._service_account_token", lambda *_a: "token")
    h = GoogleHojaBanco({"client_email": CUENTA_SERVICIO, "private_key": "x"}, HOJA_ID)

    # Una hoja de Google se exporta a .xlsx…
    respuestas[:] = [_Resp(200, {"mimeType": drive.MIME_HOJA}), _Resp(200, content=b"xlsx")]
    assert h.descargar() == (b"xlsx", FORMATO_HOJA)
    assert llamadas[-1][1].endswith(f"/{HOJA_ID}/export")
    # …y un .xlsx subido se baja tal cual.
    respuestas[:] = [_Resp(200, {"mimeType": drive.MIME_XLSX}), _Resp(200, content=b"x2")]
    assert h.descargar() == (b"x2", FORMATO_XLSX)
    assert llamadas[-1][2]["alt"] == "media"

    secreto = {"error": {"message": "Nombre Del Pagador y su IBAN ES00 0000"}}
    for status, code, texto in (
        (404, "no_existe", f"Drive no encuentra la hoja {HOJA_ID}"),
        (403, "no_compartida", f"{CUENTA_SERVICIO} no puede abrir la hoja"),
        (503, "drive_caido", "Drive no responde ahora (HTTP 503)"),
    ):
        respuestas[:] = [_Resp(status, secreto)]
        with pytest.raises(DriveBancoError) as exc:
            h.descargar()
        assert exc.value.code == code and str(exc.value).startswith(texto)
        assert "Pagador" not in str(exc.value)
    respuestas[:] = [_Resp(403, secreto)]
    with pytest.raises(DriveBancoError) as exc:
        h.escribir_celdas([(f"'{PESTANA}'!H10", "5-260090")])
    assert exc.value.code == "solo_lectura" and "como Editor" in str(exc.value)


# --- el tic diario y la cola ------------------------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):  # noqa: ARG002
        if nx and key in self.keys:
            return False
        self.keys[key] = value
        return True

    def delete(self, key):
        self.keys.pop(key, None)

    def get(self, key):
        v = self.keys.get(key)
        return v.encode() if isinstance(v, str) else v


class _FakeQueue:
    encolados: list[tuple[str, timedelta, Any, tuple]] = []

    def __init__(self, name, connection=None, default_timeout=None):  # noqa: ARG002
        self.name = name

    def enqueue_in(self, delay, fn, *args):
        _FakeQueue.encolados.append((self.name, delay, fn, args))


def test_el_tic_diario_se_arma_a_las_0520_de_madrid_en_la_cola_bank_drive():
    conn = _FakeRedis()
    _FakeQueue.encolados = []
    ahora = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    with patch("app.workers.queues.redis_connection", return_value=conn), \
            patch("rq.Queue", _FakeQueue):
        drive_job.schedule_daily(ahora)
        drive_job.schedule_daily(ahora)                  # dos veces: un solo tic
    [(cola, retraso, fn, _)] = _FakeQueue.encolados
    assert (cola, fn) == ("bank:drive", drive_job._daily_runner)
    assert retraso == timedelta(hours=16, minutes=20)   # 05:20 de Madrid = 04:20 UTC


def test_confirmar_varias_seguidas_encola_una_sola_escritura():
    conn = _FakeRedis()
    _FakeQueue.encolados = []
    with patch("app.workers.queues.redis_connection", return_value=conn), \
            patch("rq.Queue", _FakeQueue):
        for _ in range(3):
            assert drive_job.encolar_escritura("cuenta-1") is True
    [(cola, retraso, fn, args)] = _FakeQueue.encolados
    assert (cola, fn, args) == ("bank:drive", drive_job._escribir_runner, ("cuenta-1",))
    assert retraso == drive_job.ESPERA_ESCRITURA
    with patch("app.workers.queues.redis_connection", side_effect=RuntimeError("sin Redis")):
        assert drive_job.encolar_escritura("cuenta-1") is False


def test_el_tic_diario_lee_cada_cuenta_y_una_que_falla_no_para_a_las_demas(
    session_factory, cuenta, monkeypatch,
):
    with session_factory() as s:
        otra = service.create_account(s, {"name": "Belfius MQ Europe", "iban": MQ_IBAN,
                                          "drive_file_id": "1OtraHojaDeEjemploBelfius0001"})
        otra_id = otra.id
    buena = HojaFalsa(_sabadell(DATOS))
    rota = HojaFalsa([])
    rota.fallo = DriveBancoError("no_compartida", "no compartida")
    monkeypatch.setattr(drive, "abrir_hoja",
                        lambda _s, a: buena if a.id == cuenta else rota)
    monkeypatch.setattr("app.erp.api.bank._run_match", lambda _s, _a: {"ok": True})
    with session_factory() as s:
        assert drive_job.run_daily(s) == {otra_id: "no_compartida", cuenta: "ok"}
    with session_factory() as s:
        assert s.scalar(select(func.count(BankMovement.id))) == 6
        resultado = json.loads(s.get(service.BankAccount, cuenta).drive_resultado_json)
        assert (resultado["origen"], resultado["imported"]) == ("programada", 6)


# --- las filas corridas del Belfius -----------------------------------------------------------


BELFIUS_COLUMNAS = [
    "Rekening", "Boekingsdatum", None, "Transactienummer", "Rekening tegenpartij",
    "Naam tegenpartij bevat", "Straat en nummer", "Postcode en plaats", "Transactie",
    "Valutadatum", "Bedrag", "Devies", "BIC", "Landcode", "Comments", None, None, None,
    "factura", "pedido",
]


def _belfius_fila(numero: float, nombre: list[str], transactie: list[str], importe: list[Any],
                  *, fecha: datetime = _d(2026, 9, 21), calle: list[str] | None = None,
                  valuta: Any = None, factura: str | None = None) -> list:
    """Una fila de Belfius con el nombre, la calle, el texto y el importe
    partidos en las celdas que se digan (como al pegar un CSV partido por las
    comas). `factura`: lo que Bart anota a mano en su columna (no se corre)."""
    fila = [MQ_IBAN, fecha, 29.0, numero, "DE89 3704 0044 0532 0130 00", *nombre,
            *(calle or ["Calle Ejemplo 1"]), "08000 Barcelona", *transactie,
            fecha if valuta is None else valuta, *importe, "EUR", "GKCCBEBB", "BE"]
    if factura:
        fila += [None] * (18 - len(fila)) + [factura]
    return fila


def _leer(grid: list[list]) -> Any:
    parsed = read_statement(_libro(grid), "b.xlsx")
    return interpretar(parsed, parsed.mapping)


def test_las_filas_con_el_nombre_partido_por_una_coma_se_recolocan():
    texto_ok = "STORTING VAN DE89 3704 0044 0532 0130 00 STREAMTEC, SL REF. : 1 VAL. 21-09"
    texto_3 = "STORTING VAN DE89 STREAMTEC SL REF. : 3"
    lectura = _leer([
        BELFIUS_COLUMNAS, [],
        # 3: en su sitio
        _belfius_fila(1.0, ["Ejemplo SL"], ["STORTING VAN DE89 EJEMPLO REF. : 2"], [100.0]),
        # 4: el nombre partido («Streamtec, Sl»): una columna corrida. La factura
        # que Bart anotó en su columna no se mueve.
        _belfius_fila(2.0, ["Streamtec", " Sl"], [texto_3], [288.5], factura="2-526140"),
        # 5: el nombre y el texto partidos: dos columnas corridas
        _belfius_fila(3.0, ["Streamtec", " Sl"], texto_ok.split(","), [-2000.0]),
        # 6: además, el importe partido por la coma decimal: no se adivinan los céntimos
        _belfius_fila(4.0, ["Streamtec", " Sl"], ["STORTING VAN DE89 X REF. : 5"], [-90.0, 75.0]),
        # 7: la coma estaba en la calle: el nombre se queda entero
        _belfius_fila(5.0, ["Streamtec SL"], [texto_3], [50.0], calle=["Aribau", " 12"]),
        # 8: el nombre partido no está en el texto: no se sabe de qué celda es la coma
        _belfius_fila(6.0, ["Otra", " Cosa"], ["STORTING VAN DE89 NADA REF. : 9"], [10.0]),
        # 9: importe partido con la fecha valor ilegible («##»)
        _belfius_fila(7.0, ["Ejemplo SL"], ["STORTING VAN DE89 EJEMPLO REF. : 8"], [-5.0, 5.0],
                      valuta="##"),
    ])
    movs = {m["source_row"]: m for m in lectura.movimientos}
    assert sorted(movs) == [3, 4, 5, 7]
    assert lectura.recolocadas == 3
    assert (movs[4]["importe"], movs[4]["referencia2"], movs[4]["recolocada"]) == (
        288.5, "Streamtec, Sl", 1)
    assert (movs[4]["concepto"], movs[4]["factura"]) == (texto_3, "2-526140")
    assert (movs[5]["importe"], movs[5]["concepto"], movs[5]["recolocada"]) == (
        -2000.0, texto_ok, 2)
    assert movs[5]["fecha_valor"].isoformat() == "2026-09-21"
    assert movs[3]["recolocada"] == 0
    assert (movs[7]["referencia2"], movs[7]["raw"]["Straat en nummer"], movs[7]["importe"]) == (
        "Streamtec SL", "Aribau, 12", 50.0)
    motivos = {x["fila"]: x["motivo"] for x in lectura.no_entendidas}
    assert motivos == {
        6: "importe partido en dos columnas («-90» y «75»), y la fila corrida 1 columna más: "
           "corrígelo en el fichero o vuelve a exportar ese tramo",
        8: "fila corrida 1 columna (la divisa está en «BIC»): no se sabe qué texto se partió; "
           "corrígela en el fichero",
        9: "importe partido en dos columnas («-5» y «5»): corrígelo en el fichero o vuelve a "
           "exportar ese tramo",
    }


def test_en_una_fila_recolocada_no_se_escribe(session_factory, monkeypatch):
    """Su `factura` no está en su columna: escribir ahí sería en la celda de al lado."""
    grid = [
        BELFIUS_COLUMNAS, [],
        _belfius_fila(2.0, ["Streamtec", " Sl"], ["STORTING VAN DE89 STREAMTEC SL REF. : 3"],
                      [288.5]),
    ]
    h = HojaFalsa(grid, pestana="Blad1")
    monkeypatch.setattr(drive, "abrir_hoja", lambda _s, _a: h)
    with session_factory() as s:
        acc = service.create_account(s, {"name": "Belfius MQ Europe", "iban": MQ_IBAN,
                                          "drive_file_id": "1OtraHojaDeEjemploBelfius0001"})
        r = drive.traer_de_drive(s, acc)
        assert (r["imported"], r["recolocadas"]) == (1, 1)
        mov = s.scalar(select(BankMovement))
        service.reassign_movement(s, mov.id, [{"serie": 2, "codigo": 526140, "importe": 288.5}],
                                  "u-bart")
        stats = drive.escribir_cuenta(s, acc)
        assert (stats["escritas"], stats["corridas"]) == (0, 1)
        assert s.get(BankMovement, mov.id).hoja_estado == "corrida"
    assert h.escrituras == []


# --- lo que salió de la revisión --------------------------------------------------------------


def test_si_falla_la_escritura_la_lectura_cuenta_igual(http, session_factory, cuenta, hoja):
    """La hoja compartida como Lector y la escritura encendida: lo leído se
    importa, se casa y queda anotado; el error de escritura se dice aparte."""
    _traer(http, cuenta)
    soler = _mov(session_factory, "ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    _elegir(http, soler.id, 260090, 2310.00)

    def solo_lectura(_celdas):
        raise DriveBancoError("solo_lectura", "solo puede leer la hoja: compártela como Editor")

    hoja.escribir_celdas = solo_lectura
    hoja.insertar_arriba([[_d(2026, 10, 29), "ABONO TRANSFERENCIA DE NUEVA", _d(2026, 10, 29),
                           77.0, 99.0, None, "NUEVA"]])
    r = _traer(http, cuenta)
    assert (r["ok"], r["imported"]) == (True, 1)
    assert r["drive"]["escritura"]["error"].startswith("solo puede leer")
    with session_factory() as s:
        acc = s.get(service.BankAccount, cuenta)
        resultado = json.loads(acc.drive_resultado_json)
        assert (resultado["ok"], resultado["imported"]) == (True, 1)
        assert resultado["escritura"]["at"]
    assert _mov(session_factory, soler.concepto).hoja_estado == "pendiente"


def test_al_cambiar_de_factura_bohub_cambia_lo_suyo_tambien_el_pedido(
    http, session_factory, cuenta, hoja,
):
    _traer(http, cuenta)
    with session_factory() as s:
        s.add(Order(external_source=OrderSource.WOOCOMMERCE, order_number="BOPRIN-100044",
                    factusol_invoice_number="260090", factusol_invoice_serie=5))
        s.commit()
    soler = _mov(session_factory, "ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    _elegir(http, soler.id, 260090, 2310.00)
    _escribir(session_factory, cuenta)
    fila = hoja.fila_de(soler.concepto)
    assert (fila[COL_FACTURA], fila[COL_PEDIDO]) == ("5-260090", "BOPRIN-100044")
    _elegir(http, soler.id, 260095, 2310.00)           # otra factura, sin pedido en BoHub
    _escribir(session_factory, cuenta)
    assert (fila[COL_FACTURA], fila[COL_PEDIDO]) == ("5-260095", "")
    mov = _mov(session_factory, soler.concepto)
    assert (mov.hoja_factura, mov.hoja_pedido) == ("5-260095", None)


def test_lo_que_no_estaba_en_la_hoja_se_escribe_cuando_llega(http, session_factory, cuenta, hoja):
    """Conciliado desde el fichero a mano antes de que Bart pegue la fila en
    su hoja: cuando la pega, BoHub lo escribe."""
    nuevo = [_d(2026, 10, 30), "ABONO TRANSFERENCIA DE LLEGA TARDE", _d(2026, 10, 30), 321.0,
             10321.0, None, "LLEGA TARDE"]
    _traer(http, cuenta)
    with patch("app.integrations.factusol.client.FactusolClient.from_settings",
               side_effect=RuntimeError("sin FACTUSOL")):
        r = http.post("/api/erp/bank/import", files={"file": (
            "extracto.xlsx", _libro(_sabadell([nuevo, *DATOS])), "application/octet-stream")},
            headers=auth_headers(http, "admin"))
    assert r.json()["imported"] == 1
    tarde = _mov(session_factory, "ABONO TRANSFERENCIA DE LLEGA TARDE")
    _elegir(http, tarde.id, 260130, 321.0)
    assert _escribir(session_factory, cuenta)["no_encontradas"] == 1
    hoja.insertar_arriba([nuevo])                    # Bart la pega al día siguiente
    assert _traer(http, cuenta)["drive"]["escritura"]["escritas"] == 1
    assert hoja.fila_de(tarde.concepto)[COL_FACTURA] == "5-260130"


def test_dos_gemelos_seguidos_no_se_confunden_si_la_hoja_se_mueve(
    http, session_factory, cuenta, monkeypatch,
):
    """Mismo día, importe y concepto: en Sabadell solo los distingue el saldo."""
    gemelos = [
        [_d(2026, 10, 22), "ABONO TRANSFERENCIA DE GEMELO", _d(2026, 10, 22), 500.0, 9000.0],
        [_d(2026, 10, 22), "ABONO TRANSFERENCIA DE GEMELO", _d(2026, 10, 22), 500.0, 8500.0],
    ]
    h = HojaFalsa(_sabadell(gemelos))
    monkeypatch.setattr(drive, "abrir_hoja", lambda _s, _a: h)
    monkeypatch.setattr(drive_job, "encolar_escritura", lambda _a: True)
    _traer(http, cuenta)
    with session_factory() as s:
        segundo = s.scalar(select(BankMovement).where(BankMovement.saldo == 8500.0))
        mov_id = segundo.id
    _elegir(http, mov_id, 260140, 500.0)
    # Bart pega una fila justo después de que BoHub baje la hoja: el gemelo
    # de arriba baja a la fila 11, donde estaba el que se concilió.
    h.antes_de_leer_celdas = lambda hh: hh.insertar_arriba([
        [_d(2026, 10, 29), "ABONO TRANSFERENCIA DE OTRO", _d(2026, 10, 29), 1.0, 9001.0],
    ])
    assert _escribir(session_factory, cuenta)["movidas"] == 1
    assert h.escrituras == []
    assert _escribir(session_factory, cuenta)["escritas"] == 1
    [fila] = [f for f in h.grid if len(f) > 4 and f[4] == 8500.0]
    assert fila[COL_FACTURA] == "5-260140"


def test_una_nota_en_la_fila_de_continuacion_tambien_cuenta(session_factory, monkeypatch):
    """Sabadell parte el movimiento en dos filas: si Bart anotó en la de
    abajo, la de arriba no se rellena."""
    datos = [
        [_d(2026, 10, 21), "ABONO TRANSFERENCIA DE CLARA", _d(2026, 10, 21), 145.2, 955.25,
         271815809.0],
        [None, None, None, None, 956.46, None, "CLARA FIGUERAS M", "pendiente de abono"],
    ]
    h = HojaFalsa(_sabadell(datos))
    monkeypatch.setattr(drive, "abrir_hoja", lambda _s, _a: h)
    with session_factory() as s:
        acc = service.create_account(s, {"name": "Sabadell Streamtec", "iban": STREAMTEC_IBAN,
                                          "drive_file_id": HOJA_ID})
        drive.traer_de_drive(s, acc)
        mov = s.scalar(select(BankMovement))
        service.reassign_movement(s, mov.id, [{"serie": 5, "codigo": 260119, "importe": 145.2}],
                                  "u-bart")
        assert drive.escribir_cuenta(s, acc)["ocupadas"] == 1
    assert h.escrituras == []


def test_si_se_reasigna_mientras_se_escribe_gana_la_reasignacion(
    http, session_factory, cuenta, hoja,
):
    _traer(http, cuenta)
    soler = _mov(session_factory, "ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    _elegir(http, soler.id, 260090, 2310.00)

    def reasignar_entretanto(_h):
        with session_factory() as otra:
            m = otra.get(BankMovement, soler.id)
            m.hoja_estado, m.hoja_estado_at = "pendiente", datetime(2030, 1, 1)
            otra.commit()

    hoja.antes_de_leer_celdas = reasignar_entretanto
    stats = _escribir(session_factory, cuenta)
    assert (stats["escritas"], stats["movidas"]) == (0, 1)
    assert _mov(session_factory, soler.concepto).hoja_estado == "pendiente"


def test_al_cambiar_de_hoja_se_reintenta_lo_que_no_se_pudo_escribir(
    http, session_factory, cuenta, hoja,
):
    _traer(http, cuenta)
    roca = _mov(session_factory, "ABONO TRANSFERENCIA DE TALLERES ROCA")
    _elegir(http, roca.id, 260101, 1210.00)
    hoja.grid.remove(hoja.fila_de(roca.concepto))
    _escribir(session_factory, cuenta)
    assert _mov(session_factory, roca.concepto).hoja_estado == "no_encontrada"
    r = http.patch(f"/api/erp/bank/accounts/{cuenta}", json={
        "drive_file_id": "https://docs.google.com/spreadsheets/d/1HojaConvertidaDeEjemplo000002/edit",
    }, headers=auth_headers(http, "admin"))
    assert r.status_code == 200
    assert _mov(session_factory, roca.concepto).hoja_estado == "pendiente"
