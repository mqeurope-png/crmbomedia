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
from datetime import UTC, date, datetime
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


def _lco(serie: int, codigo: int, importe: float) -> dict:
    return {"TFALCO": str(serie), "CFALCO": codigo, "LINLCO": 1, "IMPLCO": importe,
            "CPALCO": 6, "FECLCO": "2026-08-27T00:00:00"}


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


def _cobro_banco(http, oid: str = "o-1", role: str = "pedidos"):
    return http.get(f"/api/erp/orders/{oid}/cobro-banco", headers=auth_headers(http, role))


def _confirmar(http, mid: str = "m-1", oid: str = "o-1", fake=None, **body):
    fake = fake or FakeFactusol([_fac(2, 526081, 927.0)])
    with _patched(fake), patch(
        "app.integrations.factusol.jobs.enqueue_register_invoice_collection",
        return_value="job-1",
    ) as enq, patch("app.erp.bank.drive_job.encolar_escritura") as hoja:
        r = http.post(f"/api/erp/orders/{oid}/cobro-banco", headers=auth_headers(http, "pedidos"),
                      json={"movement_id": mid, "confirm": True, **body})
    return r, enq, hoja


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
    """El script de Bart ya registró el cobro de lo conciliado: no se duplica."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, importe=500.0, status="reconciled")
        s.add(BankReconciliation(movement_id="m-1", serie=2, codigo=526081, numero="2-526081",
                                 importe=500.0, status="confirmed"))
        s.commit()
    fake = FakeFactusol([_fac(2, 526081, 927.0)], [_lco(2, 526081, 500.0)])
    r, enq, _ = _confirmar(http, fake=fake)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "ya_registrado"
    enq.assert_not_called()


def test_conciliado_sin_cobro_registra_con_su_fecha(http, session_factory) -> None:
    """Conciliado en Conciliación pero sin cobro en FACTUSOL: la ficha lo
    registra con la fecha valor de ese movimiento (y no lo vuelve a conciliar)."""
    with session_factory() as s:
        _pedido(s)
        _movimiento(s, status="reconciled")
        s.add(BankReconciliation(movement_id="m-1", serie=2, codigo=526081, numero="2-526081",
                                 importe=927.0, status="confirmed"))
        s.commit()
    body = _cobro_banco(http).json()
    assert body["estado"] == "conciliado" and body["por_registrar"] == 927.0
    r, enq, _ = _confirmar(http)
    assert r.status_code == 202, r.text
    assert enq.call_args.args[3] == "2026-08-26T00:00:00"
    # Segunda vez desde la ficha: ya está registrado.
    r2, _, _ = _confirmar(http)
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "ya_registrado"


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
    assert keys.index("factura_enviada") == keys.index("cobro") - 1 == 5

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

    # Deshacer (un error al marcar): vuelve a estar por enviar.
    r = http.post("/api/erp/orders/o-1/factura-enviada/deshacer", headers=h)
    assert r.status_code == 200 and r.json()["invoice_sent"] is None
    assert _wf(session_factory)["queue"] == "factura_por_enviar"


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
