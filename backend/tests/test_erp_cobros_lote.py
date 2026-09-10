"""ERP-F4-B — registrar cobros en FACTUSOL (F_COB + F_LCO + ESTFAC) en lote.

Diseño basado en el discovery `--collections` en producción y en dos intentos
fallidos en producción (PR #383/#384): DELSOL exige la CABECERA `F_COB` antes de
aceptar la línea `F_LCO`; el enlace es la clave de la factura (TFACOB/CFACOB ↔
TFALCO/CFALCO). LINLCO 1..N por factura, FALLCO = vencimiento, CPALCO/CPACOB =
contrapartida, FPALCO = forma de pago. Aquí se prueba el ORDEN de escritura, que
se escribe SOLO eso con los campos correctos, que es idempotente, que nunca se
escribe a ciegas y que el nombre de cuenta del Excel casa con la contrapartida.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.contrapartidas import resolve_contrapartida_code
from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.collections_write import (
    LCO_COLUMNS,
    collection_status,
    concepto_cobro,
    factusol_datetime,
    pick_template_row,
    register_invoice_collection,
)
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users


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
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db(session_factory) -> Generator[Session, None, None]:
    with session_factory() as s:
        yield s


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _set_cfg(s: Session, **payload: Any) -> None:
    cfg = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is None:
        cfg = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
        s.add(cfg)
    cfg.factusol_series_json = json.dumps(payload)
    s.commit()


class FakeCobroClient:
    """FACTUSOL simulado: sirve F_FAC, F_LCO y F_COB, registra escrituras (en
    ORDEN) y actualizaciones (F_FAC.ESTFAC). `fail_write` hace fallar el
    `EscribirRegistro` de ESA tabla."""

    def __init__(self, *, f_fac=None, f_lco=None, f_cob=None, fail_write=None):
        self.default_ejercicio = "2026"
        self._f_fac = f_fac or []
        self._f_lco = f_lco or []
        self._f_cob = f_cob or []
        self._fail_write = fail_write
        self.writes: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla == "F_FAC":
            return list(self._f_fac)
        if tabla == "F_LCO":
            return list(self._f_lco)
        if tabla == "F_COB":
            return list(self._f_cob)
        return []

    def write_record(self, tabla, data, *, ejercicio=None):
        if self._fail_write == tabla:
            raise FactusolError(f"write {tabla} failed", status=500)
        self.writes.append((tabla, data))
        return {"ok": True}

    def update_record(self, tabla, data, *, ejercicio=None):
        self.updates.append((tabla, data))
        return {"ok": True}


def _fac(serie, codigo, total, estfac="0", **over):
    row = {
        "TIPFAC": str(serie), "CODFAC": codigo, "TOTFAC": total,
        "ESTFAC": estfac, "CNOFAC": "NEONLED SL", "REFFAC": f"BOM-{codigo}",
        "FOPFAC": "002", "FECFAC": "2026-08-15T00:00:00",
    }
    row.update(over)
    return row


def _cobro(serie, codigo, linea, importe):
    return {
        "TFALCO": str(serie), "CFALCO": codigo, "LINLCO": linea,
        "FECLCO": "2026-08-20T00:00:00", "IMPLCO": importe,
        "CPALCO": "6", "CPTLCO": f"COBRO FACTURA Nº: {serie} - {codigo}",
    }


def _lco_real(serie, codigo, linea, importe, **over):
    """Fila REAL de F_LCO con las 23 columnas (como las devuelve DELSOL),
    incluidas las que no describen el cobro (TIPLCO, UALLCO…) y que dejan de
    estar vacías al usarla de plantilla."""
    row = {
        "ANTLCO": 0, "CAJLCO": "", "CFALCO": codigo, "CPALCO": "8",
        "CPTLCO": f"COBRO FACTURA Nº: {serie} - {codigo}",
        "FALLCO": "2026-08-14T00:00:00", "FECLCO": "2026-08-12T00:00:00",
        "FPALCO": "002", "FUMLCO": "2026-08-12T10:15:00", "IMPLCO": importe,
        "LINLCO": linea, "MULLCO": 0, "OBSLCO": "", "PCALCO": 0, "PROLCO": 0,
        "TERLCO": "", "TFALCO": serie, "TIDLCO": 0, "TIPLCO": 1,
        "TPVIDLCO": "", "TRALCO": 0, "UALLCO": "BART", "UUMLCO": "BART",
    }
    row.update(over)
    return row


def _cob_real(serie, codigo, importe, **over):
    """Cabecera REAL de F_COB (convención DELSOL: mismo campo que F_LCO con
    sufijo COB). Sin LINCOB: la cabecera no tiene nº de línea."""
    row = {
        "TFACOB": serie, "CFACOB": codigo, "IMPCOB": importe,
        "FECCOB": "2026-08-12T00:00:00", "FALCOB": "2026-08-14T00:00:00",
        "CPACOB": "8", "CPTCOB": f"COBRO FACTURA Nº: {serie} - {codigo}",
        "FPACOB": "002", "OBSCOB": "", "TIPCOB": 1,
        "FUMCOB": "2026-08-12T10:15:00", "UALCOB": "BART", "UUMCOB": "BART",
    }
    row.update(over)
    return row


def _writes(client, tabla):
    return [rec for t, rec in client.writes if t == tabla]


# --- mapping cuenta (Excel) → contrapartida ----------------------------------


def test_mapping_cuenta_a_banco(db) -> None:
    """Los nombres de la columna CUENTA del Excel casan con las contrapartidas
    de FACTUSOL (catálogo F-5); también se acepta el código directo; lo
    desconocido NO se adivina."""
    _set_cfg(db)  # catálogo por defecto (las 14 de Bart)
    esperado = {
        "Bomedia (Sabadell)": "6",
        "Streamtec (Sabadell)": "8",
        "MQ Europe (Belfius)": "2",
        "Paypal Streamtec": "14",
        "Paypal MQ Europe": "12",
    }
    for nombre, codigo in esperado.items():
        assert resolve_contrapartida_code(db, nombre) == codigo, nombre
    assert resolve_contrapartida_code(db, "SABADELL bomedia") == "6"
    assert resolve_contrapartida_code(db, "006") == "6"
    assert resolve_contrapartida_code(db, "Banco Inventado") is None
    assert resolve_contrapartida_code(db, "99") is None
    assert resolve_contrapartida_code(db, "") is None


# --- registro del cobro: ORDEN F_COB → F_LCO → ESTFAC ------------------------


def test_registro_cobro_escribe_cabecera_f_cob_antes_de_la_linea(db) -> None:
    """BUGFIX (BDEscribirRegistroError con las 23 columnas): DELSOL exige la
    CABECERA F_COB de la factura antes de aceptar la línea F_LCO. Caso real
    5-260082 (Rocío Bueno, 70,18 €, contrapartida 8, 24/08/2026): se escribe
    F_COB (clave de la factura, importe, fecha, contrapartida, concepto) y
    DESPUÉS la línea; ambas sobre una fila real, y al final ESTFAC=2."""
    _set_cfg(db)
    client = FakeCobroClient(
        f_fac=[_fac(5, 260082, 70.18, CNOFAC="ROCIO BUENO")],
        f_lco=[_lco_real(5, 260001, 1, 100.0)],
        f_cob=[_cob_real(1, 260004, 50.0, CPACOB="6"),   # otra serie
               _cob_real(5, 260001, 100.0)],             # misma serie+cpa ← plantilla
    )
    result = register_invoice_collection(
        client, db, serie=5, codigo=260082, contrapartida="8",
        fecha="24/08/2026", forma="Transferencia", ejercicio="2026",
    )
    assert result["registered"] is True and result["cob_written"] is True
    # ORDEN: primero la cabecera, luego la línea.
    assert [t for t, _ in client.writes] == ["F_COB", "F_LCO"]
    cob, = _writes(client, "F_COB")
    # Clave de la FACTURA (retag TFALCO→TFACOB…), con el tipo de la plantilla.
    assert cob["TFACOB"] == 5 and isinstance(cob["TFACOB"], int)
    assert cob["CFACOB"] == 260082
    assert cob["IMPCOB"] == 70.18 and cob["CPACOB"] == "8"
    assert cob["FECCOB"] == "2026-08-24" == cob["FALCOB"]
    assert cob["CPTCOB"] == "COBRO FACTURA Nº: 5 - 260082 (Transferencia)"
    assert cob["FPACOB"] == "002" and cob["OBSCOB"] == ""
    # Hereda lo que no describe el cobro y NUNCA inventa columnas (sin LINCOB).
    assert cob["TIPCOB"] == 1 and cob["UALCOB"] == "BART"
    assert "LINCOB" not in cob and set(cob) == set(_cob_real(5, 260001, 100.0))
    # Nunca hereda la clave de la plantilla.
    assert (cob["TFACOB"], cob["CFACOB"]) != (5, 260001)
    lco, = _writes(client, "F_LCO")
    assert (lco["TFALCO"], lco["CFALCO"], lco["LINLCO"]) == (5, 260082, 1)
    assert lco["IMPLCO"] == 70.18 and lco["FECLCO"] == "2026-08-24"
    assert client.updates == [("F_FAC", {"TIPFAC": 5, "CODFAC": 260082, "ESTFAC": "2"})]


def test_registro_cobro_no_duplica_cabecera_si_ya_existe(db) -> None:
    """Cobro parcial previo: la factura ya tiene cabecera F_COB → solo se
    añade la línea (LINLCO=2, importe = saldo), sin segunda cabecera."""
    _set_cfg(db)
    client = FakeCobroClient(
        f_fac=[_fac(5, 260082, 100.0, estfac="1")],
        f_lco=[_lco_real(5, 260082, 1, 40.0)],
        f_cob=[_cob_real(5, 260082, 40.0)],
    )
    result = register_invoice_collection(
        client, db, serie=5, codigo=260082, contrapartida="8",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert result["registered"] is True and result["cob_written"] is False
    assert [t for t, _ in client.writes] == ["F_LCO"]
    assert result["linlco"] == 2 and result["importe"] == 60.0


def test_registro_cobro_sin_esquema_f_cob_no_escribe_nada(db) -> None:
    """Si la fila real de F_COB no trae la clave de factura (TFACOB/CFACOB), la
    convención no casa → NO se escribe NADA (ni cabecera, ni línea, ni flag) y
    se devuelve un diagnóstico con las columnas vistas."""
    _set_cfg(db)
    raro = FakeCobroClient(
        f_fac=[_fac(5, 260082, 70.18)], f_lco=[_lco_real(5, 260001, 1, 100.0)],
        f_cob=[{"CODCOB": 1, "IMPCOB": 100.0, "CPACOB": "8"}],
    )
    r = register_invoice_collection(
        raro, db, serie=5, codigo=260082, contrapartida="8",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert r["registered"] is False and r["status"] == "cob_schema_unknown"
    assert "TFACOB" in r["motivo"] and "CODCOB" in r["motivo"]
    assert raro.writes == [] and raro.updates == []
    # F_COB vacía: tampoco se escribe a ciegas.
    vacio = FakeCobroClient(f_fac=[_fac(5, 260082, 70.18)])
    r = register_invoice_collection(
        vacio, db, serie=5, codigo=260082, contrapartida="8",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert r["status"] == "cob_schema_unknown" and vacio.writes == []


def test_registro_cobro_fallo_cabecera_no_escribe_linea(db) -> None:
    """Si falla F_COB no se escribe la línea ni se marca; si falla F_LCO se
    informa de que la cabecera SÍ quedó y tampoco se marca."""
    _set_cfg(db)
    base = dict(
        f_fac=[_fac(5, 260082, 70.18)], f_lco=[_lco_real(5, 260001, 1, 100.0)],
        f_cob=[_cob_real(5, 260001, 100.0)],
    )
    cob_ko = FakeCobroClient(**base, fail_write="F_COB")
    r = register_invoice_collection(
        cob_ko, db, serie=5, codigo=260082, contrapartida="8",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert r["status"] == "cob_write_failed" and cob_ko.writes == []
    assert cob_ko.updates == []
    lco_ko = FakeCobroClient(**base, fail_write="F_LCO")
    r = register_invoice_collection(
        lco_ko, db, serie=5, codigo=260082, contrapartida="8",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert r["status"] == "write_failed" and r["cob_written"] is True
    assert "cabecera F_COB SÍ quedó" in r["motivo"]
    assert [t for t, _ in lco_ko.writes] == ["F_COB"] and lco_ko.updates == []


def test_registro_cobro_factusol(db) -> None:
    """Caso de prueba de Bart: 1-260729 Neonled 72,60 € por transferencia a
    Bomedia Sabadell. Sin plantilla de línea (F_LCO vacía) la línea va con el
    mínimo; la cabecera siempre sobre una fila real; después ESTFAC=2."""
    _set_cfg(db)
    client = FakeCobroClient(
        f_fac=[_fac(1, 260729, 72.60)], f_cob=[_cob_real(1, 260004, 50.0, CPACOB="6")],
    )
    result = register_invoice_collection(
        client, db, serie=1, codigo=260729, contrapartida="6",
        fecha="10/09/2026", forma="Transferencia", ejercicio="2026",
    )
    assert result["registered"] is True and result["status"] == "registered"
    assert result["importe"] == 72.60            # = total de la factura
    assert result["estfac_marked"] is True
    assert [t for t, _ in client.writes] == ["F_COB", "F_LCO"]
    lco, = _writes(client, "F_LCO")
    assert lco == {
        "TFALCO": "1", "CFALCO": 260729, "LINLCO": 1,
        "FECLCO": "2026-09-10", "FALLCO": "2026-09-10",
        "IMPLCO": 72.60, "CPALCO": "6",
        "CPTLCO": "COBRO FACTURA Nº: 1 - 260729 (Transferencia)",
        "FPALCO": "002",                         # forma de pago de la factura
        "OBSLCO": "",
    }
    assert set(lco) <= LCO_COLUMNS
    assert client.updates == [
        ("F_FAC", {"TIPFAC": 1, "CODFAC": 260729, "ESTFAC": "2"}),
    ]


def test_registro_cobro_usa_plantilla_de_fila_real(db) -> None:
    """La línea se construye sobre una fila REAL de F_LCO (misma serie y
    contrapartida) para no dejar vacía ninguna columna, respetando el tipo con
    el que DELSOL devuelve cada una (PR #384)."""
    _set_cfg(db)
    client = FakeCobroClient(
        f_fac=[_fac(5, 260082, 70.18, CNOFAC="ROCIO BUENO")],
        f_lco=[
            _lco_real(1, 260004, 1, 100.0, CPALCO="6"),   # otra serie
            _lco_real(5, 260004, 1, 420.74),               # misma serie+cpa 8 ← plantilla
        ],
        f_cob=[_cob_real(5, 260004, 420.74)],
    )
    result = register_invoice_collection(
        client, db, serie=5, codigo=260082, contrapartida="8",
        fecha="24/08/2026", forma="Transferencia", ejercicio="2026",
    )
    assert result["registered"] is True
    rec, = _writes(client, "F_LCO")
    assert set(rec) == LCO_COLUMNS
    assert rec["TIPLCO"] == 1 and rec["UALLCO"] == "BART" and rec["TIDLCO"] == 0
    assert rec["TFALCO"] == 5 and isinstance(rec["TFALCO"], int)
    assert rec["CFALCO"] == 260082 and rec["LINLCO"] == 1
    assert rec["FECLCO"] == "2026-08-24" == rec["FALLCO"]
    assert rec["IMPLCO"] == 70.18 and rec["CPALCO"] == "8"
    assert rec["CPTLCO"] == "COBRO FACTURA Nº: 5 - 260082 (Transferencia)"
    assert rec["FPALCO"] == "002" and rec["OBSLCO"] == ""
    assert (rec["TFALCO"], rec["CFALCO"], rec["LINLCO"]) != (5, 260004, 1)
    assert client.updates == [("F_FAC", {"TIPFAC": 5, "CODFAC": 260082, "ESTFAC": "2"})]


def test_plantilla_prefiere_misma_serie_y_contrapartida() -> None:
    rows = [
        _lco_real(1, 1, 1, 1.0, CPALCO="6"),
        _lco_real(5, 2, 1, 1.0, CPALCO="14"),
        _lco_real(5, 3, 1, 1.0, CPALCO="8"),
    ]
    assert pick_template_row(rows, serie=5, contrapartida="8")["CFALCO"] == 3
    assert pick_template_row(rows, serie=5, contrapartida="99")["CFALCO"] == 2
    assert pick_template_row(rows, serie=2, contrapartida="2")["CFALCO"] == 1
    assert pick_template_row([], serie=5, contrapartida="8") is None


def test_cobro_idempotente(db) -> None:
    """Ya cobrada (saldo 0 o ESTFAC=2) → `already`, sin escribir NADA."""
    _set_cfg(db)
    saldada = FakeCobroClient(
        f_fac=[_fac(1, 260729, 72.60)], f_lco=[_cobro(1, 260729, 1, 72.60)],
        f_cob=[_cob_real(1, 260729, 72.60)],
    )
    r1 = register_invoice_collection(
        saldada, db, serie=1, codigo=260729, contrapartida="6",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert r1["registered"] is False and r1["status"] == "already"
    assert saldada.writes == [] and saldada.updates == []
    marcada = FakeCobroClient(f_fac=[_fac(1, 260729, 72.60, estfac="2")])
    r2 = register_invoice_collection(
        marcada, db, serie=1, codigo=260729, contrapartida="6",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert r2["status"] == "already"
    assert marcada.writes == [] and marcada.updates == []


def test_cobro_no_existe_no_escribe(db) -> None:
    _set_cfg(db)
    vacio = FakeCobroClient()
    r = register_invoice_collection(
        vacio, db, serie=1, codigo=1, contrapartida="6",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert r["status"] == "invoice_not_found" and vacio.writes == []


def test_fecha_y_concepto() -> None:
    """Las fechas que fijamos van en YYYY-MM-DD: el formato con el que la
    emisión escribe FECFAC (inserts probados en DELSOL)."""
    assert factusol_datetime("2026-09-10") == "2026-09-10"
    assert factusol_datetime("2026-09-10T00:00:00") == "2026-09-10"
    assert factusol_datetime("10/09/2026") == "2026-09-10"
    assert factusol_datetime("10-09-2026") == "2026-09-10"
    with pytest.raises(ValueError):
        factusol_datetime("ayer")
    assert concepto_cobro(5, 260082, "TPV") == "COBRO FACTURA Nº: 5 - 260082 (TPV)"
    assert concepto_cobro(5, 260082, None) == "COBRO FACTURA Nº: 5 - 260082"


def test_collection_status_en_vivo(db) -> None:
    client = FakeCobroClient(
        f_fac=[_fac(5, 260082, 70.18)], f_lco=[_cobro(5, 260082, 1, 20.0)],
    )
    st = collection_status(client, serie=5, codigo=260082, ejercicio="2026")
    assert st["total"] == 70.18 and st["total_cobrado"] == 20.0
    assert st["saldo_pendiente"] == 50.18 and st["next_linlco"] == 2
    assert st["ya_cobrada"] is False and st["fopfac"] == "002"
    assert collection_status(client, serie=2, codigo=260082, ejercicio="2026") is None


# --- endpoint -----------------------------------------------------------------


def _patched_client(client):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=client,
    )


def test_status_routes_no_colisionan_con_el_detalle(http, session_factory) -> None:
    """`collection-status/{job_id}` y `payment-status/{job_id}` (F-3) caían en
    la genérica `/documents/{doc_type}/{serie}/{codigo}` (serie:int) → 422.
    Deben resolverse como rutas de estado (200, `pending` sin Redis)."""
    _ = session_factory
    for path in ("collection-status/job-x", "payment-status/job-x"):
        r = http.get(f"/api/erp/factusol/documents/facturas/{path}",
                     headers=auth_headers(http, "admin"))
        assert r.status_code == 200, f"{path} → {r.status_code} {r.text[:120]}"
        assert r.json()["status"] == "pending"


def test_endpoint_requires_confirmation_and_edit(http, session_factory) -> None:
    _ = session_factory
    body = {"confirm": False, "cuenta": "Bomedia (Sabadell)", "fecha": "2026-09-10"}
    r = http.post("/api/erp/factusol/documents/facturas/1/260729/collection",
                  headers=auth_headers(http, "admin"), json=body)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "confirmation_required"
    r = http.post("/api/erp/factusol/documents/facturas/1/260729/collection",
                  headers=auth_headers(http, "manager"),
                  json={**body, "confirm": True})
    assert r.status_code == 403


def test_endpoint_rejects_unknown_account(http, session_factory) -> None:
    _ = session_factory
    r = http.post("/api/erp/factusol/documents/facturas/1/260729/collection",
                  headers=auth_headers(http, "admin"),
                  json={"confirm": True, "cuenta": "Banco Inventado",
                        "fecha": "2026-09-10"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "unknown_account"


def test_endpoint_queues_or_reports_already(http, session_factory) -> None:
    """Pre-chequeo en vivo: factura pendiente → encola (202 + job_id) con el
    importe previsto = total y la fecha ya normalizada (YYYY-MM-DD); ya
    cobrada → `already` sin encolar."""
    _ = session_factory
    pendiente = FakeCobroClient(f_fac=[_fac(1, 260729, 72.60)])
    with _patched_client(pendiente), patch(
        "app.integrations.factusol.jobs.enqueue_register_invoice_collection",
        return_value="job-1",
    ) as enq:
        r = http.post(
            "/api/erp/factusol/documents/facturas/1/260729/collection",
            headers=auth_headers(http, "admin"),
            json={"confirm": True, "cuenta": "Bomedia (Sabadell)",
                  "fecha": "10/09/2026", "forma": "Transferencia"},
        )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued" and body["job_id"] == "job-1"
    assert body["importe"] == 72.60 and body["contrapartida"]["codigo"] == "6"
    assert body["contrapartida"]["nombre"] == "Bomedia Sabadell"
    assert enq.call_args.args[:4] == (1, 260729, "6", "2026-09-10")

    cobrada = FakeCobroClient(f_fac=[_fac(1, 260729, 72.60, estfac="2")])
    with _patched_client(cobrada), patch(
        "app.integrations.factusol.jobs.enqueue_register_invoice_collection",
    ) as enq:
        r = http.post(
            "/api/erp/factusol/documents/facturas/1/260729/collection",
            headers=auth_headers(http, "admin"),
            json={"confirm": True, "cuenta": "6", "fecha": "2026-09-10"},
        )
    assert r.status_code == 202 and r.json()["status"] == "already"
    enq.assert_not_called()
