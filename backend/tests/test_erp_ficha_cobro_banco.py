"""La ficha del pedido (30/10/2026): el cobro que propone el banco, la factura
enviada antes del cobro y la cola «Factura por enviar».

- El paso «Cobro» busca el movimiento del banco que pagó la factura (el mismo
  casador de Conciliación, empezando por el nº de factura en el concepto) y lo
  enseña con su FECHA VALOR, importe y cuenta. Confirmar concilia el
  movimiento con la factura (igual que desde Conciliación: hoja de Drive
  incluida) y registra el cobro con la fecha valor y la contrapartida de la
  cuenta. Un importe menor es un cobro parcial.
- «Factura enviada» es el paso 6, antes del cobro: se puede marcar a mano
  («ya enviada»), con quién y cuándo en Auditoría.
- La bandeja cuenta las facturas por enviar al cliente.

FACTUSOL no se toca (cliente en memoria) y los datos del banco son
FIGURANTES: ni pagadores ni conceptos reales.
"""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.bank import service as bank_service
from app.erp.models import (
    BankAccount,
    BankMovement,
    BankReconciliation,
    Order,
    OrderSource,
)
from app.erp.workflow import order_workflow
from app.main import app
from app.models.crm import AuditLog, Company
from tests._test_helpers import auth_headers, seed_test_users

# --- FACTUSOL simulado -------------------------------------------------------------


class FakeFactusol:
    """F_FAC / F_LCO en memoria: lo justo para leer el estado de cobro."""

    def __init__(self, f_fac: list[dict], f_lco: list[dict] | None = None):
        self.default_ejercicio = "2026"
        self.f_fac = f_fac
        self.f_lco = f_lco or []
        self.writes: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla == "F_FAC":
            if filtro.startswith("CODFAC="):
                wanted = filtro.split("=", 1)[1].strip()
                return [r for r in self.f_fac if str(r.get("CODFAC")) == wanted]
            return list(self.f_fac)
        if tabla == "F_LCO":
            return list(self.f_lco)
        return []

    def write_record(self, tabla, data, *, ejercicio=None):
        self.writes.append((tabla, dict(data)))

    def update_record(self, tabla, data, *, ejercicio=None):
        self.updates.append((tabla, dict(data)))


def _fac(serie: int, codigo: int, total: float) -> dict:
    return {"TIPFAC": str(serie), "CODFAC": codigo, "TOTFAC": total, "ESTFAC": "0",
            "CNOFAC": "TALLER EJEMPLO SL", "REFFAC": f"BOP-{codigo}", "FOPFAC": "002",
            "CLIFAC": 4321}


def _lco(serie: int, codigo: int, importe: float, *, linea: int = 1,
         fecha: str = "2026-08-27", cuenta: int = 6) -> dict:
    return {"TFALCO": str(serie), "CFALCO": codigo, "LINLCO": linea, "IMPLCO": importe,
            "CPALCO": cuenta, "FECLCO": f"{fecha}T00:00:00"}


def _patched(client):
    return patch("app.integrations.factusol.client.FactusolClient.from_settings",
                 return_value=client)


# --- fixtures -----------------------------------------------------------------------


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    eng = create_engine("sqlite+pysqlite:///:memory:",
                        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    with factory() as s:
        seed_test_users(s)
        s.add(Company(id="c-1", name="Taller Ejemplo SL", factusol_company_id="4321",
                      country="ES"))
        s.add(BankAccount(id="acc-1", name="Banco Figurante MQ", iban="BE00 0000 0000 0001",
                          serie=2, contrapartida_codigo="6"))
        s.commit()
    yield factory
    Base.metadata.drop_all(eng)


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _pedido(s: Session, oid: str = "o-1", *, codigo: str = "526081", cobrado: bool = False,
            **over) -> Order:
    campos = {
        "id": oid, "external_source": OrderSource.WOOCOMMERCE, "external_id": oid,
        "order_number": f"BOPRIN-{oid}", "company_id": "c-1", "total_amount": 927.0,
        "currency": "EUR", "payment_status": "paid",
        "approved_at": datetime(2026, 8, 20, tzinfo=UTC),
        "placed_at": datetime(2026, 8, 20, tzinfo=UTC),
        "invoice_status": "invoiced_by_erp", "factusol_invoice_number": codigo,
        "factusol_invoice_serie": 2,
        "factusol_cobro_status": "cobrada" if cobrado else "pendiente",
    }
    o = Order(**{**campos, **over})
    s.add(o)
    s.commit()
    return o


def _movimiento(s: Session, mid: str = "m-1", *, importe: float = 927.0,
                concepto: str = "TRANSFERENCIA DE TALLER EJEMPLO SL FACTURA 2-526081",
                fecha_oper: date = date(2026, 8, 25), fecha_valor: date | None = date(2026, 8, 26),
                status: str = "pending") -> BankMovement:
    m = BankMovement(
        id=mid, account_id="acc-1", fecha_oper=fecha_oper, fecha_valor=fecha_valor,
        concepto=concepto, importe=importe, dedupe_key=f"k-{mid}", payer_name="Taller Ejemplo SL",
        status=status,
    )
    s.add(m)
    s.commit()
    return m


def _job(estado: str = "pending"):
    """El estado del job de un cobro ya mandado (sin Redis de verdad)."""
    return patch("app.erp.api.factusol._rq_quote_status", return_value={"status": estado})


def _cobro_banco(http, oid: str = "o-1", role: str = "pedidos", fake=None,
                 job: str = "pending"):
    fake = fake or FakeFactusol([_fac(2, 526081, 927.0)])
    with _patched(fake), _job(job):
        return http.get(f"/api/erp/orders/{oid}/cobro-banco", headers=auth_headers(http, role))


def _confirmar(http, mid: str = "m-1", oid: str = "o-1", fake=None, job: str = "pending",
               **body):
    fake = fake or FakeFactusol([_fac(2, 526081, 927.0)])
    with _patched(fake), _job(job), patch(
        "app.integrations.factusol.jobs.enqueue_register_invoice_collection",
        return_value="job-1",
    ) as enq, patch("app.erp.bank.drive_job.encolar_escritura") as hoja:
        r = http.post(f"/api/erp/orders/{oid}/cobro-banco", headers=auth_headers(http, "pedidos"),
                      json={"movement_id": mid, "confirm": True, **body})
    return r, enq, hoja


def _conciliado(s: Session, mid: str, importe: float, codigo: int = 526081) -> None:
    s.add(BankReconciliation(movement_id=mid, serie=2, codigo=codigo,
                             numero=f"2-{codigo}", importe=importe, status="confirmed"))
    s.commit()


# --- el cobro que propone el banco --------------------------------------------------


def test_propone_el_movimiento_con_fecha_valor_importe_y_cuenta(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s)
    r = _cobro_banco(http)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["aplica"] is True and body["estado"] == "candidatos"
    [c] = body["candidatos"]
    assert c["fecha_valor"] == "2026-08-26" and c["fecha_oper"] == "2026-08-25"
    assert c["importe"] == 927.0
    assert c["cuenta"]["nombre"] == "Banco Figurante MQ"
    assert c["cuenta"]["contrapartida_codigo"] == "6"
    assert c["confianza"] == "alta"
    assert c["motivo"] == "número de factura en el concepto + importe exacto"
    assert c["parcial"] is False and c["excede"] is False


def test_confirmar_registra_el_cobro_con_la_fecha_valor_concilia_y_escribe_la_hoja(
    http, session_factory,
) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s)
        acc = s.get(BankAccount, "acc-1")
        acc.drive_file_id, acc.drive_escribir = "hoja-figurante", True
        s.commit()
    r, enq, hoja = _confirmar(http)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued" and r.json()["parcial"] is False
    # El cobro: la factura, la contrapartida de la cuenta y la FECHA VALOR.
    serie, codigo, cuenta, fecha, importe, forma, _obs = enq.call_args.args[:7]
    assert (serie, codigo, cuenta, fecha) == (2, 526081, "6", "2026-08-26T00:00:00")
    assert importe == 927.0 and forma == "Transferencia"
    with session_factory() as s:
        mov = s.get(BankMovement, "m-1")
        assert mov.status == "reconciled"
        rec = s.scalars(select(BankReconciliation).where(
            BankReconciliation.movement_id == "m-1")).one()
        assert (rec.serie, rec.codigo, rec.status) == (2, 526081, "confirmed")
        assert rec.origen == bank_service.ORIGEN_PEDIDO
        # La hoja de Drive del banco: marcada para escribir y encolada.
        assert mov.hoja_estado == bank_service.HOJA_PENDIENTE
        audit = s.scalars(select(AuditLog).where(AuditLog.action == "erp.order_cobro_banco")).one()
        meta = json.loads(audit.metadata_json)
        assert audit.target_id == "o-1" and meta["fecha_valor"] == "2026-08-26"
        assert meta["movement_id"] == "m-1" and meta["parcial"] is False
    hoja.assert_called_once_with("acc-1")


def test_un_importe_menor_es_un_cobro_parcial(http, session_factory) -> None:
    """Pagado en dos veces: el primer movimiento no da la factura por cobrada."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, importe=500.0)
    body = _cobro_banco(http).json()
    [c] = body["candidatos"]
    assert c["parcial"] is True and c["confianza"] == "media"
    r, enq, _ = _confirmar(http)
    assert r.status_code == 202, r.text
    assert r.json()["parcial"] is True
    assert enq.call_args.args[4] == 500.0   # se registra ESE importe, no el saldo


def test_sin_movimiento_no_hay_nada_que_ensenar(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
        # Uno que no es: otro importe y sin la factura en el concepto.
        _movimiento(s, importe=1234.56, concepto="TRANSFERENCIA DE OTRO CLIENTE FIGURANTE")
    body = _cobro_banco(http).json()
    assert body["estado"] == "nada" and body["candidatos"] == []


def test_un_movimiento_que_nombra_otra_factura_no_se_propone_por_el_importe(
    http, session_factory,
) -> None:
    """Mismo importe, pero el concepto nombra OTRA factura de la serie: es el
    cobro de esa, no de esta."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, concepto="TRANSFERENCIA DE TALLER EJEMPLO SL FACTURA 2-526099")
        _movimiento(s, "m-2", concepto="STORTING VAN OTRO FIGURANTE 526098")
    body = _cobro_banco(http).json()
    assert body["estado"] == "nada" and body["candidatos"] == []


def test_lo_conciliado_que_cubre_la_factura_no_deja_candidatos(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, status="reconciled")
        s.add(BankReconciliation(movement_id="m-1", serie=2, codigo=526081, numero="2-526081",
                                 importe=927.0, status="confirmed"))
        # Otro movimiento del mismo importe, sin nada que lo una a la factura.
        _movimiento(s, "m-2", concepto="TRANSFERENCIA DE OTRO FIGURANTE")
        s.commit()
    body = _cobro_banco(http).json()
    assert body["estado"] == "conciliado" and body["candidatos"] == []


def test_pagada_en_dos_veces_lo_conciliado_y_el_resto(http, session_factory) -> None:
    """Conciliado un primer pago parcial: el segundo, que nombra la factura,
    sigue saliendo como candidato (parcial también)."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, importe=500.0, status="reconciled")
        s.add(BankReconciliation(movement_id="m-1", serie=2, codigo=526081, numero="2-526081",
                                 importe=500.0, status="confirmed"))
        _movimiento(s, "m-2", importe=427.0,
                    concepto="SEGUNDO PAGO TALLER EJEMPLO SL FACTURA 2-526081",
                    fecha_oper=date(2026, 9, 2), fecha_valor=date(2026, 9, 2))
        s.commit()
    body = _cobro_banco(http).json()
    assert body["estado"] == "conciliado"
    assert [c["id"] for c in body["candidatos"]] == ["m-2"]
    assert body["candidatos"][0]["parcial"] is True


def test_ya_cobrada_no_propone_nada(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s, cobrado=True)
        _movimiento(s)
    body = _cobro_banco(http).json()
    assert body["estado"] == "cobrada" and body["candidatos"] == []


def test_ya_cobrada_ensena_con_que_movimiento(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s, cobrado=True)
        _movimiento(s, status="reconciled")
        s.add(BankReconciliation(movement_id="m-1", serie=2, codigo=526081, numero="2-526081",
                                 importe=927.0, status="confirmed"))
        s.commit()
    body = _cobro_banco(http).json()
    assert body["estado"] == "cobrada"
    assert [c["id"] for c in body["conciliados"]] == ["m-1"]


def test_conciliado_en_conciliacion_y_cobro_ya_registrado_no_se_registra_otra_vez(
    http, session_factory,
) -> None:
    """El script de Bart ya registró el cobro de lo conciliado (una línea de
    cobro con ese importe y fecha cercana): no se duplica."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, importe=500.0, status="reconciled")
        _conciliado(s, "m-1", 500.0)
    fake = FakeFactusol([_fac(2, 526081, 927.0)], [_lco(2, 526081, 500.0)])
    body = _cobro_banco(http, fake=fake).json()
    [c] = body["conciliados"]
    assert c["registrado"] == "2026-08-27" and c["en_cola"] is False
    assert body["por_registrar"] == 0.0
    r, enq, _ = _confirmar(http, fake=fake)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "already" and r.json()["fecha_factusol"] == "2026-08-27"
    enq.assert_not_called()


def test_conciliado_sin_cobro_registra_con_su_fecha(http, session_factory) -> None:
    """Conciliado en Conciliación pero sin cobro en FACTUSOL: la ficha lo
    registra con la fecha valor de ese movimiento (y no lo vuelve a conciliar)."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, status="reconciled")
        _conciliado(s, "m-1", 927.0)
    body = _cobro_banco(http).json()
    assert body["estado"] == "conciliado" and body["por_registrar"] == 927.0
    assert body["conciliados"][0]["registrado"] is None
    r, enq, _ = _confirmar(http)
    assert r.status_code == 202, r.text
    assert enq.call_args.args[3] == "2026-08-26T00:00:00"
    with session_factory() as s:
        meta = json.loads(s.scalars(select(AuditLog.metadata_json).where(
            AuditLog.action == "erp.order_cobro_banco")).one())
        assert meta["registrado"] is True and meta["job_id"] == "job-1"


def test_un_cobro_en_cola_no_se_manda_dos_veces_y_uno_fallido_se_reintenta(
    http, session_factory,
) -> None:
    """Mandado desde la ficha y aún en cola: ni se ofrece ni se deja mandar
    otra vez. Si el job falló, sí (no hay línea de cobro en FACTUSOL). Pasada
    media hora, «en cola» deja de valer (sin Redis, el job dice «pendiente»
    para siempre)."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, status="reconciled")
        _conciliado(s, "m-1", 927.0)
    r, _, _ = _confirmar(http)
    assert r.status_code == 202, r.text
    # En cola: la ficha no lo ofrece y el segundo intento se para.
    body = _cobro_banco(http, job="pending").json()
    assert body["conciliados"][0]["en_cola"] is True and body["por_registrar"] == 0.0
    r2, enq2, _ = _confirmar(http, job="pending")
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "en_cola"
    enq2.assert_not_called()
    # El job falló: se puede volver a mandar.
    body = _cobro_banco(http, job="failed").json()
    assert body["conciliados"][0]["en_cola"] is False and body["por_registrar"] == 927.0
    r3, enq3, _ = _confirmar(http, job="failed")
    assert r3.status_code == 202, r3.text
    enq3.assert_called_once()
    # Hace más de media hora: aunque el job siga «pendiente», ya no bloquea.
    with session_factory() as s:
        for log in s.scalars(select(AuditLog).where(AuditLog.action == "erp.order_cobro_banco")):
            log.created_at = datetime.now(UTC) - timedelta(minutes=31)
        s.commit()
    r4, enq4, _ = _confirmar(http, job="pending")
    assert r4.status_code == 202, r4.text
    enq4.assert_called_once()


def test_sin_cola_el_movimiento_queda_conciliado_y_se_avisa(http, session_factory) -> None:
    """La cola (Redis) no responde al mandar el cobro: error claro, y el
    movimiento queda conciliado."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s)
    with _patched(FakeFactusol([_fac(2, 526081, 927.0)])), _job(), patch(
        "app.integrations.factusol.jobs.enqueue_register_invoice_collection",
        side_effect=ConnectionError("redis caído"),
    ), patch("app.erp.bank.drive_job.encolar_escritura"):
        r = http.post("/api/erp/orders/o-1/cobro-banco", headers=auth_headers(http, "pedidos"),
                      json={"movement_id": "m-1", "confirm": True})
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "cobro_no_encolado"
    assert "queda conciliado" in detail["detail"]
    with session_factory() as s:
        assert s.get(BankMovement, "m-1").status == "reconciled"


def test_efectivo_y_transferencia_la_transferencia_se_registra(http, session_factory) -> None:
    """427 € cobrados en efectivo (apuntados a mano) y 500 € por el banco: la
    línea de 427 no es la de la transferencia, así que se registra."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, importe=500.0, status="reconciled")
        _conciliado(s, "m-1", 500.0)
    fake = FakeFactusol([_fac(2, 526081, 927.0)],
                        [_lco(2, 526081, 427.0, fecha="2026-08-24", cuenta=1)])
    body = _cobro_banco(http, fake=fake).json()
    assert body["conciliados"][0]["registrado"] is None and body["por_registrar"] == 500.0
    r, enq, _ = _confirmar(http, fake=fake)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    assert enq.call_args.args[4] == 500.0      # el saldo que queda, entero


def test_registrado_a_mano_sin_conciliar_se_concilia_sin_registrar(
    http, session_factory,
) -> None:
    """La transferencia ya se apuntó a mano en FACTUSOL (en otra cuenta) pero
    no se concilió: la ficha la concilia y no registra nada más."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, importe=500.0)
    fake = FakeFactusol([_fac(2, 526081, 927.0)],
                        [_lco(2, 526081, 500.0, fecha="2026-08-28", cuenta=9)])
    body = _cobro_banco(http, fake=fake).json()
    [c] = body["candidatos"]
    assert c["ya_en_factusol"] == "2026-08-28"
    # Apuntado en OTRA cuenta que la del banco: la ficha lo dice (puede ser
    # otro pago del mismo importe).
    assert c["ya_en_factusol_cuenta"] == "9 · Caja Bomedia"
    assert c["ya_en_factusol_otra_cuenta"] is True
    r, enq, hoja = _confirmar(http, fake=fake)
    assert r.status_code == 202, r.text
    res = r.json()
    assert res["status"] == "already" and res["fecha_factusol"] == "2026-08-28"
    assert res["cuenta_factusol"] == "9 · Caja Bomedia" and res["otra_cuenta"] is True
    assert res["importe"] == 500.0 and res["parcial"] is True
    assert res["movimiento"]["id"] == "m-1"
    enq.assert_not_called()
    with session_factory() as s:
        assert s.get(BankMovement, "m-1").status == "reconciled"
        assert bank_service.conciliado_con_factura(s, 2, 526081) == 500.0
        audit = s.scalars(select(AuditLog).where(AuditLog.action == "erp.order_cobro_banco")).one()
        meta = json.loads(audit.metadata_json)
        assert meta["registrado"] is False and meta["ya_en_factusol"] == "2026-08-28"
        assert meta["movement_id"] == "m-1"


def test_dos_pagos_iguales_cada_uno_con_su_linea(http, session_factory) -> None:
    """Dos pagos de 500 € de una factura de 1.000 €: una línea de cobro de 500
    en FACTUSOL solo vale para uno (el más cercano por fecha), no para los dos."""
    with session_factory() as s:
        _pedido(s, total_amount=1000.0)
        _movimiento(s, importe=500.0, status="reconciled")
        _conciliado(s, "m-1", 500.0)
        _movimiento(s, "m-2", importe=500.0, concepto="SEGUNDO PAGO FACTURA 2-526081",
                    fecha_oper=date(2026, 9, 1), fecha_valor=date(2026, 9, 1))
    una = FakeFactusol([_fac(2, 526081, 1000.0)], [_lco(2, 526081, 500.0)])
    body = _cobro_banco(http, fake=una).json()
    assert body["conciliados"][0]["registrado"] == "2026-08-27"
    assert body["candidatos"][0]["ya_en_factusol"] is None
    r, enq, _ = _confirmar(http, "m-2", fake=una)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    enq.assert_called_once()
    # Con las dos líneas, cada movimiento tiene la suya.
    dos = FakeFactusol([_fac(2, 526081, 1000.0)],
                       [_lco(2, 526081, 500.0),
                        _lco(2, 526081, 500.0, linea=2, fecha="2026-09-02")])
    body = _cobro_banco(http, fake=dos).json()
    assert {c["id"]: c["registrado"] for c in body["conciliados"]} == {
        "m-1": "2026-08-27", "m-2": "2026-09-02",
    }
    assert body["por_registrar"] == 0.0
    r, enq, _ = _confirmar(http, "m-2", fake=dos)
    assert r.json()["status"] == "already" and r.json()["fecha_factusol"] == "2026-09-02"
    enq.assert_not_called()


def test_confirmar_exige_confirmacion_y_permiso(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
        _movimiento(s)
    r, enq, _ = _confirmar(http, confirm=False)
    assert r.status_code == 400
    enq.assert_not_called()
    r = http.post("/api/erp/orders/o-1/cobro-banco", headers=auth_headers(http, "comercial"),
                  json={"movement_id": "m-1", "confirm": True})
    assert r.status_code == 403


def test_csv_de_cobros_lleva_fecha_valor_y_la_contrapartida_de_la_cuenta(session_factory) -> None:
    with session_factory() as s:
        _movimiento(s, status="reconciled")
        s.add(BankReconciliation(movement_id="m-1", serie=2, codigo=526081, numero="2-526081",
                                 importe=927.0, status="confirmed"))
        s.commit()
        texto, stats = bank_service.export_cobros_csv(s, saldos={(2, 526081): 927.0})
    filas = list(csv.DictReader(io.StringIO(texto.lstrip("﻿"))))
    assert stats["incluidas"] == 1
    assert filas[0]["fecha"] == "26/08/2026"      # fecha valor, no la de operación
    assert filas[0]["cuenta"] == "6"


def test_csv_tras_un_cobro_parcial_desde_la_ficha_no_registra_lo_que_se_debe(
    session_factory,
) -> None:
    """500 de 927 registrados desde la ficha (saldo 427 en FACTUSOL): lo
    conciliado (500) ya está cobrado, así que la factura NO entra en el CSV
    (el script registraría los 427 que el cliente aún debe). Cuando llega y
    se concilia el resto, entra, con la fecha valor del último pago."""
    clave = (2, 526081)
    with session_factory() as s:
        _movimiento(s, importe=500.0, status="reconciled")
        _conciliado(s, "m-1", 500.0)
        texto, stats = bank_service.export_cobros_csv(
            s, saldos={clave: 427.0}, totales={clave: 927.0},
        )
        assert stats["incluidas"] == 0 and stats["parciales"] == 1
        assert texto.count("\n") == 1                 # solo la cabecera
        _movimiento(s, "m-2", importe=427.0, concepto="RESTO FACTURA 2-526081",
                    fecha_oper=date(2026, 9, 3), fecha_valor=date(2026, 9, 4), status="reconciled")
        _conciliado(s, "m-2", 427.0)
        texto, stats = bank_service.export_cobros_csv(
            s, saldos={clave: 427.0}, totales={clave: 927.0},
        )
    filas = list(csv.DictReader(io.StringIO(texto.lstrip("﻿"))))
    assert stats["incluidas"] == 1 and stats["parciales"] == 0
    assert [(f["codigo"], f["fecha"]) for f in filas] == [("526081", "04/09/2026")]


def test_csv_con_filtro_de_fechas_tras_un_parcial_incluye_el_resto(session_factory) -> None:
    """Exportando solo septiembre: el resto (427) pagado en septiembre entra,
    aunque el primer pago (500, registrado desde la ficha) sea de agosto."""
    clave = (2, 526081)
    with session_factory() as s:
        _movimiento(s, importe=500.0, status="reconciled")
        _conciliado(s, "m-1", 500.0)
        _movimiento(s, "m-2", importe=427.0, concepto="RESTO FACTURA 2-526081",
                    fecha_oper=date(2026, 9, 3), fecha_valor=date(2026, 9, 4), status="reconciled")
        _conciliado(s, "m-2", 427.0)
        texto, stats = bank_service.export_cobros_csv(
            s, saldos={clave: 427.0}, totales={clave: 927.0}, desde=date(2026, 9, 1),
        )
    assert stats["incluidas"] == 1 and stats["parciales"] == 0


def test_el_endpoint_del_csv_pasa_los_totales(http, session_factory) -> None:
    """El CSV que descarga Conciliación usa el total de cada factura: tras un
    cobro parcial desde la ficha no sale la factura (lo que falta se debe)."""
    with session_factory() as s:
        _movimiento(s, importe=500.0, status="reconciled")
        _conciliado(s, "m-1", 500.0)
    pendientes = [{"serie": 2, "codigo": 526081, "numero": "2-526081", "total": 927.0,
                   "saldo_pendiente": 427.0}]
    with _patched(FakeFactusol([])), patch.object(bank_service, "pending_invoices",
                                                  return_value=pendientes):
        r = http.get("/api/erp/bank/export-cobros", headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    assert r.headers["X-Cobros-Incluidos"] == "0" and r.headers["X-Cobros-Parciales"] == "1"


def test_un_cobro_igual_apuntado_a_mano_con_otra_fecha_no_se_registra_sin_confirmar(
    http, session_factory,
) -> None:
    """El pago de 427 € (fecha valor 26/08) se apuntó a mano en FACTUSOL el
    20/09 (a más de 7 días): la ficha lo enseña como cobro suelto, avisa de un
    posible doble cobro y no registra otro sin que la persona diga que es otro
    pago."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, importe=427.0)
    fake = FakeFactusol([_fac(2, 526081, 927.0)],
                        [_lco(2, 526081, 427.0, fecha="2026-09-20", cuenta=6)])
    body = _cobro_banco(http, fake=fake).json()
    [c] = body["candidatos"]
    assert c["ya_en_factusol"] is None and c["posible_doble"] is True
    assert body["cobros_sueltos"] == [
        {"fecha": "2026-09-20", "importe": 427.0, "cuenta": "6 · Bomedia Sabadell"},
    ]
    r, enq, _ = _confirmar(http, fake=fake)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "posible_doble_cobro"
    assert "20/09/2026" in r.json()["detail"]["detail"]
    enq.assert_not_called()
    with session_factory() as s:
        assert s.get(BankMovement, "m-1").status == "pending"     # no se ha hecho nada
    r, enq, _ = _confirmar(http, fake=fake, forzar=True)
    assert r.status_code == 202, r.text
    assert enq.call_args.args[4] == 427.0


def test_una_proforma_en_el_concepto_no_cuenta_como_otra_factura(http, session_factory) -> None:
    """«PROFORMA 526090» es otro documento, no otra factura: el pago se propone."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, concepto="TRANSFERENCIA DE TALLER EJEMPLO SL PROFORMA 526090")
    [c] = _cobro_banco(http).json()["candidatos"]
    assert c["id"] == "m-1"


def test_csv_pagada_desde_dos_cuentas_se_deja_fuera(session_factory) -> None:
    """Dos pagos de la misma factura en cuentas con contrapartidas distintas:
    el script registra una sola línea en una cuenta, así que no entra."""
    with session_factory() as s:
        s.add(BankAccount(id="acc-2", name="Banco Figurante Dos", iban="ES00 0000 0000 0002",
                          serie=2, contrapartida_codigo="8"))
        s.commit()
        _movimiento(s, importe=500.0, status="reconciled")
        _conciliado(s, "m-1", 500.0)
        m2 = _movimiento(s, "m-2", importe=427.0, concepto="RESTO FACTURA 2-526081",
                         fecha_oper=date(2026, 9, 3), fecha_valor=date(2026, 9, 4),
                         status="reconciled")
        m2.account_id = "acc-2"
        s.commit()
        _conciliado(s, "m-2", 427.0)
        _, stats = bank_service.export_cobros_csv(
            s, saldos={(2, 526081): 927.0}, totales={(2, 526081): 927.0},
        )
    assert stats["incluidas"] == 0 and stats["no_cuadran"] == 1


def test_las_lineas_se_reparten_para_casar_el_maximo(session_factory) -> None:
    """Dos pagos de 500 (01/08 y 05/08) y dos líneas de 500 en FACTUSOL (28/07
    y 04/08): cada movimiento se queda una, aunque la más cercana al primero
    sea la del 04/08 (si se la quedara, el segundo se registraría otra vez)."""
    lineas = [
        {"linea": 1, "importe": 500.0, "fecha": "2026-08-04", "contrapartida": "6"},
        {"linea": 2, "importe": 500.0, "fecha": "2026-07-28", "contrapartida": "6"},
    ]
    casados = bank_service.casar_lineas_de_cobro([
        ("m-1", 500.0, (date(2026, 8, 1), date(2026, 8, 1)), "6"),
        ("m-2", 500.0, (date(2026, 8, 5), date(2026, 8, 5)), "6"),
    ], lineas)
    assert casados["m-1"]["linea"] == 2 and casados["m-2"]["linea"] == 1


def test_las_lineas_de_la_misma_cuenta_van_primero(session_factory) -> None:
    """Una transferencia de 500 y dos líneas de 500 el mismo día: una en la
    caja (efectivo) y otra en la cuenta del banco. Se casa con la del banco."""
    lineas = [
        {"linea": 1, "importe": 500.0, "fecha": "2026-08-26", "contrapartida": "1"},
        {"linea": 2, "importe": 500.0, "fecha": "2026-08-26", "contrapartida": "2"},
    ]
    casados = bank_service.casar_lineas_de_cobro(
        [("m-1", 500.0, (date(2026, 8, 26), date(2026, 8, 26)), "2")], lineas,
    )
    assert casados["m-1"]["linea"] == 2


def test_del_movimiento_al_pedido(http, session_factory) -> None:
    """La vuelta: desde Conciliación, el movimiento lleva al pedido."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, status="reconciled")
        s.add(BankReconciliation(movement_id="m-1", serie=2, codigo=526081, numero="2-526081",
                                 importe=927.0, status="confirmed"))
        s.commit()
    r = http.get("/api/erp/bank/movements?movimiento=m-1",
                 headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    [m] = r.json()["items"]
    assert m["fecha_valor"] == "2026-08-26"
    assert m["reconciled"][0]["pedido"] == {"id": "o-1", "order_number": "BOPRIN-o-1"}


# --- «Factura enviada», paso 6, y la bandeja ----------------------------------------


def _wf(session_factory, oid: str = "o-1") -> dict:
    with session_factory() as s:
        return order_workflow(s, s.get(Order, oid))


def test_factura_enviada_va_antes_del_cobro_y_se_marca_a_mano(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
    wf = _wf(session_factory)
    assert wf["queue"] == "factura_por_enviar" and wf["next_action"] == "enviar_factura"
    keys = [st["key"] for st in wf["steps"]]
    assert keys.index("factura_enviada") == keys.index("cobro") - 1 == 4

    h = auth_headers(http, "pedidos")
    assert http.post("/api/erp/orders/o-1/factura-enviada", headers=h,
                     json={"medio": "papel"}).status_code == 400   # sin confirmar
    r = http.post("/api/erp/orders/o-1/factura-enviada", headers=h,
                  json={"confirm": True, "fecha": "2026-08-22", "medio": "papel",
                        "nota": "con el albarán"})
    assert r.status_code == 200, r.text
    sent = r.json()["invoice_sent"]
    assert sent["via"] == "manual" and sent["at"] == "2026-08-22"
    assert sent["by"] == "pedidos@example.com" and sent["medio"] == "papel"
    wf = _wf(session_factory)
    steps = {st["key"]: st for st in wf["steps"]}
    assert steps["factura_enviada"]["state"] == "done"
    assert wf["queue"] == "por_cobrar"
    with session_factory() as s:
        log = s.scalars(select(AuditLog).where(AuditLog.action == "erp.invoice_marked_sent")).one()
        assert log.actor_email == "pedidos@example.com" and log.target_id == "o-1"
        assert json.loads(log.metadata_json)["fecha"] == "2026-08-22"
    # En el historial del pedido, con su título.
    tl = http.get("/api/erp/orders/o-1/timeline", headers=h).json()["items"]
    assert any(e["title"] == "Factura marcada como enviada al cliente" for e in tl)

    # Deshacer (un error al marcar): vuelve a estar por enviar. Dice qué marca
    # quita.
    r = http.post("/api/erp/orders/o-1/factura-enviada/deshacer", headers=h)
    assert r.status_code == 200 and r.json()["invoice_sent"] is None
    assert _wf(session_factory)["queue"] == "factura_por_enviar"
    with session_factory() as s:
        marca = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.invoice_marked_sent")).one()
        deshacer = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.invoice_marked_unsent")).one()
        assert json.loads(deshacer.metadata_json)["anula"] == marca.id


def test_una_muestra_no_tiene_factura_que_enviar(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s, factusol_invoice_number=None, invoice_status="not_invoiced",
                order_kind="sample")
    r = http.post("/api/erp/orders/o-1/factura-enviada", headers=auth_headers(http, "pedidos"),
                  json={"confirm": True, "medio": "papel"})
    assert r.status_code == 409
    wf = _wf(session_factory)
    assert wf["queue"] == "listo"
    assert {st["key"]: st["state"] for st in wf["steps"]}["factura_enviada"] == "skipped"


def test_la_bandeja_cuenta_las_facturas_por_enviar(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s, "a", codigo="526081")
        _pedido(s, "b", codigo="526082")
        _pedido(s, "c", codigo="526083")
        s.add(AuditLog(action="erp.invoice_emailed", target_type="order", target_id="c"))
        s.commit()
    h = auth_headers(http, "pedidos")
    r = http.get("/api/erp/orders", headers=h).json()
    assert r["queue_counts"]["factura_por_enviar"] == 2
    assert "por_enviar" not in r["queue_counts"]
    solo = http.get("/api/erp/orders?queue=factura_por_enviar", headers=h).json()
    assert {i["id"] for i in solo["items"]} == {"a", "b"}
    # El filtro «Factura enviada · No enviada» cuenta la marca a mano como enviada.
    http.post("/api/erp/orders/a/factura-enviada", headers=h,
              json={"confirm": True, "medio": "gmail"})
    no_env = http.get("/api/erp/orders?invoice_email=no_enviada", headers=h).json()
    assert {i["id"] for i in no_env["items"]} == {"b"}
    # Los contadores cuentan lo que la lista enseña con ese filtro…
    assert no_env["queue_counts"]["factura_por_enviar"] == 1
    assert sum(no_env["queue_counts"].values()) == 1
    enviadas = http.get("/api/erp/orders?invoice_email=enviada", headers=h).json()
    assert {i["id"] for i in enviadas["items"]} == {"a", "c"}
    assert sum(enviadas["queue_counts"].values()) == 2
    assert enviadas["queue_counts"]["factura_por_enviar"] == 0
    # …y elegir una cola no los cambia.
    con_cola = http.get("/api/erp/orders?invoice_email=enviada&queue=por_cobrar",
                        headers=h).json()
    assert con_cola["queue_counts"] == enviadas["queue_counts"]
