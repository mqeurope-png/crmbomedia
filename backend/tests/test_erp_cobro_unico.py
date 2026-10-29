# ruff: noqa: F811 — las fixtures de `test_erp_ficha_cobro_banco` se importan por nombre
"""Una sola herramienta de cobro (29/10/2026): el mismo contexto y el mismo
apunte se entre por la ficha del pedido, por Conciliación o por ERP ·
Documentos, y con el movimiento del banco identificado la factura se escribe
en la hoja de Drive de la cuenta (solo celdas vacías; un fallo de la hoja no
deshace el cobro). FACTUSOL y Google, en memoria; datos FIGURANTES."""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

from sqlalchemy import select

from app.erp.bank import drive
from app.erp.bank.drive import DriveBancoError
from app.erp.models import BankAccount
from app.models.crm import AuditLog
from tests._test_helpers import auth_headers
from tests.test_erp_bank_drive import PESTANA, HojaFalsa, _sabadell
from tests.test_erp_ficha_cobro_banco import (  # noqa: F401 — fixtures
    FakeFactusol,
    _conciliado,
    _fac,
    _job,
    _lco,
    _movimiento,
    _patched,
    _pedido,
    http,
    session_factory,
)

CONCEPTO = "TRANSFERENCIA DE TALLER EJEMPLO SL FACTURA 2-526081"
COL_FACTURA = 7


def _con_hoja(session_factory, monkeypatch, *, factura_escrita: str | None = None,
              falla: bool = False) -> HojaFalsa:
    """La cuenta escribe en su hoja (en memoria), que tiene la fila del movimiento."""
    with session_factory() as s:
        acc = s.get(BankAccount, "acc-1")
        acc.drive_file_id, acc.drive_escribir = "hoja-figurante", True
        acc.drive_pestana, acc.drive_formato = PESTANA, "hoja"
        s.commit()
    fila = [datetime(2026, 8, 25), CONCEPTO, datetime(2026, 8, 26), 927.0, None, None,
            "TALLER EJEMPLO SL"]
    if factura_escrita:
        fila += [factura_escrita]
    hoja = HojaFalsa(_sabadell([fila]))

    def abrir(_s, _a):
        if falla:
            raise DriveBancoError("drive_caido", "Drive no responde ahora (HTTP 503).")
        return hoja
    monkeypatch.setattr(drive, "abrir_hoja", abrir)
    return hoja


def _info(http, codigo: int = 526081, fake=None, **params):
    fake = fake or FakeFactusol([_fac(2, codigo, 927.0)])
    with _patched(fake):
        r = http.get(f"/api/erp/factusol/documents/facturas/2/{codigo}/cobro",
                     params=params, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    return r.json()


def _cobrar(http, url: str, mid: str = "m-1", fake=None, **body):
    fake = fake or FakeFactusol([_fac(2, 526081, 927.0), _fac(2, 526099, 927.0)])
    with _patched(fake), _job(), patch(
        "app.integrations.factusol.jobs.enqueue_register_invoice_collection",
        return_value="job-1",
    ) as enq, patch("app.erp.bank.drive_job.encolar_escritura"):
        r = http.post(url, headers=auth_headers(http, "pedidos"),
                      json={"movement_id": mid, "confirm": True, **body})
    return r, enq


DOCUMENTOS = "/api/erp/factusol/documents/facturas/2/526081/cobro-banco"
FICHA = "/api/erp/orders/o-1/cobro-banco"


def test_el_dialogo_sabe_el_movimiento_la_fecha_valor_y_la_hoja(
    http, session_factory, monkeypatch,
) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, status="reconciled")
        _conciliado(s, "m-1", 927.0)
    _con_hoja(session_factory, monkeypatch)
    info = _info(http)
    assert info["externa"] is False
    mov = info["movimiento"]
    assert (mov["id"], mov["fecha_valor"], mov["importe_factura"]) == ("m-1", "2026-08-26", 927.0)
    assert mov["cuenta"]["contrapartida_codigo"] == "6"
    assert info["hoja"] == {"cuenta": "Banco Figurante MQ", "pestana": PESTANA,
                            "factura": "2-526081"}
    # Desde la ficha, lo mismo.
    with _patched(FakeFactusol([_fac(2, 526081, 927.0)])):
        ficha = http.get("/api/erp/orders/o-1/factusol-cobro",
                         headers=auth_headers(http, "pedidos")).json()
    assert ficha["movimiento"]["id"] == "m-1" and ficha["hoja"] == info["hoja"]


def test_sin_movimiento_identificado_no_hay_hoja_ni_aviso(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
    info = _info(http)
    assert info["movimiento"] is None and info["hoja"] is None


def test_una_factura_externa_lo_dice(http, session_factory) -> None:
    info = _info(http, codigo=526099)
    assert info["externa"] is True


def test_cobrada_ensena_contra_que_se_cobro(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, status="reconciled")
        _conciliado(s, "m-1", 927.0)
    fake = FakeFactusol([_fac(2, 526081, 927.0)], [_lco(2, 526081, 927.0, fecha="2026-08-26")])
    info = _info(http, fake=fake)
    assert info["status"] == "cobrada"
    assert info["movimiento"] is None                          # nada que cobrar
    [linea] = info["cobrado_con"]
    assert (linea["fecha"], linea["importe"]) == ("2026-08-26", 927.0)
    assert linea["movimiento"]["id"] == "m-1"


def test_cobrar_desde_documentos_registra_y_escribe_la_factura_en_la_hoja(
    http, session_factory, monkeypatch,
) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s)
    hoja = _con_hoja(session_factory, monkeypatch)
    r, enq = _cobrar(http, DOCUMENTOS)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued" and body["parcial"] is False
    serie, codigo, cuenta, fecha = enq.call_args.args[:4]
    assert (serie, codigo, cuenta, fecha) == (2, 526081, "6", "2026-08-26T00:00:00")
    assert body["hoja"]["estado"] == "escrita"
    assert hoja.fila_de(CONCEPTO)[COL_FACTURA] == "2-526081"
    with session_factory() as s:
        log = s.scalars(select(AuditLog).where(AuditLog.action == "erp.order_cobro_banco")).one()
        assert (log.target_type, log.target_id) == ("order", "o-1")


def test_desde_la_ficha_es_el_mismo_cobro_y_el_mismo_apunte(
    http, session_factory, monkeypatch,
) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s)
    hoja = _con_hoja(session_factory, monkeypatch)
    r, enq = _cobrar(http, FICHA)
    assert r.status_code == 202, r.text
    assert enq.call_args.args[:4] == (2, 526081, "6", "2026-08-26T00:00:00")
    assert r.json()["hoja"]["estado"] == "escrita"
    assert hoja.fila_de(CONCEPTO)[COL_FACTURA] == "2-526081"
    with session_factory() as s:
        log = s.scalars(select(AuditLog).where(AuditLog.action == "erp.order_cobro_banco")).one()
        assert (log.target_type, log.target_id) == ("order", "o-1")


def test_una_celda_con_algo_no_se_toca(http, session_factory, monkeypatch) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s)
    hoja = _con_hoja(session_factory, monkeypatch, factura_escrita="pendiente de Bart")
    r, enq = _cobrar(http, DOCUMENTOS)
    assert r.status_code == 202 and enq.called
    assert r.json()["hoja"]["estado"] == "ocupada"
    assert hoja.fila_de(CONCEPTO)[COL_FACTURA] == "pendiente de Bart"
    assert hoja.escrituras == []


def test_si_la_hoja_falla_el_cobro_sigue_registrado_y_se_avisa(
    http, session_factory, monkeypatch,
) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s)
    _con_hoja(session_factory, monkeypatch, falla=True)
    r, enq = _cobrar(http, DOCUMENTOS)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued" and enq.called      # el cobro, mandado
    assert r.json()["hoja"]["estado"] == "error"
    assert "Drive no responde" in r.json()["hoja"]["detalle"]
    with session_factory() as s:                                 # queda constancia
        acc = s.get(BankAccount, "acc-1")
        assert "Drive no responde" in (acc.drive_resultado_json or "")


def test_una_externa_se_cobra_con_su_apunte_a_la_factura(http, session_factory) -> None:
    with session_factory() as s:
        _movimiento(s, concepto="TRANSFERENCIA FIGURANTE FACTURA 2-526099")
    r, enq = _cobrar(http, "/api/erp/factusol/documents/facturas/2/526099/cobro-banco")
    assert r.status_code == 202, r.text
    assert enq.called and r.json()["hoja"] is None              # la cuenta no tiene hoja
    with session_factory() as s:
        log = s.scalars(select(AuditLog).where(AuditLog.action == "erp.order_cobro_banco")).one()
        assert (log.target_type, log.target_id) == ("document", "2-526099")


def test_por_menos_de_lo_pendiente_es_cobro_parcial(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, importe=500.0, fecha_oper=date(2026, 8, 25))
    r, enq = _cobrar(http, DOCUMENTOS)
    assert r.status_code == 202, r.text
    assert r.json()["parcial"] is True
    assert enq.call_args.args[4] == 500.0


def test_un_movimiento_cuyo_cobro_ya_esta_en_factusol_solo_se_concilia(
    http, session_factory,
) -> None:
    """Pedido sin conciliar aún, pero con su línea de cobro ya en FACTUSOL: el
    diálogo no promete escribir otra línea."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s)
    fake = FakeFactusol([_fac(2, 526081, 1854.0)],
                        [_lco(2, 526081, 927.0, fecha="2026-08-26", cuenta=6)])
    info = _info(http, fake=fake, movimiento="m-1")
    assert info["movimiento"]["id"] == "m-1"
    assert info["movimiento"]["ya_en_factusol"] is True


def test_si_la_hoja_falla_de_forma_inesperada_queda_constancia(
    http, session_factory, monkeypatch,
) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s)
    _con_hoja(session_factory, monkeypatch)

    def revienta(*a, **k):
        raise RuntimeError("timeout figurante")
    monkeypatch.setattr(drive, "escribir_cuenta", revienta)
    r, enq = _cobrar(http, DOCUMENTOS)
    assert r.status_code == 202 and enq.called
    assert r.json()["hoja"]["estado"] == "error"
    with session_factory() as s:
        assert "se reintenta" in (s.get(BankAccount, "acc-1").drive_resultado_json or "")


def test_un_movimiento_ya_conciliado_sin_marcar_para_la_hoja_tambien_se_escribe(
    http, session_factory, monkeypatch,
) -> None:
    """Conciliado antes de que la cuenta escribiera en su hoja: al cobrar
    desde Conciliación, la factura se escribe igual."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, status="reconciled")
        _conciliado(s, "m-1", 927.0)
    hoja = _con_hoja(session_factory, monkeypatch)
    r, enq = _cobrar(http, DOCUMENTOS)
    assert r.status_code == 202, r.text
    assert r.json()["hoja"]["estado"] == "escrita"
    assert hoja.fila_de(CONCEPTO)[COL_FACTURA] == "2-526081"
