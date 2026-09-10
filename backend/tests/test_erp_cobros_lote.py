"""ERP-F4-B — registrar cobros en FACTUSOL (F_LCO + ESTFAC) en lote.

Diseño basado en el discovery `--collections` en producción: F_LCO es la
línea de cobro por factura (clave compuesta TFALCO/CFALCO/LINLCO, LINLCO 1..N
por factura, FALLCO = vencimiento, CPALCO = contrapartida, FPALCO = forma de
pago). Aquí se prueba que se escribe SOLO eso, con los campos correctos, que es
idempotente y que el nombre de cuenta del Excel casa con la contrapartida.
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
    """FACTUSOL simulado: sirve F_FAC y F_LCO, registra escrituras (F_LCO) y
    actualizaciones (F_FAC.ESTFAC). Puede fallar al escribir."""

    def __init__(self, *, f_fac=None, f_lco=None, fail_write=False):
        self.default_ejercicio = "2026"
        self._f_fac = f_fac or []
        self._f_lco = f_lco or []
        self._fail_write = fail_write
        self.writes: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla == "F_FAC":
            return list(self._f_fac)
        if tabla == "F_LCO":
            return list(self._f_lco)
        return []

    def write_record(self, tabla, data, *, ejercicio=None):
        if self._fail_write:
            raise FactusolError("write failed", status=500)
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
    # Orden de palabras y mayúsculas indiferentes; código directo con ceros.
    assert resolve_contrapartida_code(db, "SABADELL bomedia") == "6"
    assert resolve_contrapartida_code(db, "006") == "6"
    assert resolve_contrapartida_code(db, "Banco Inventado") is None
    assert resolve_contrapartida_code(db, "99") is None
    assert resolve_contrapartida_code(db, "") is None


# --- registro del cobro -------------------------------------------------------


def test_registro_cobro_factusol(db) -> None:
    """Caso de prueba de Bart: 1-260729 Neonled 72,60 € por transferencia a
    Bomedia Sabadell. Escribe UNA línea en F_LCO con los campos reales y
    después marca ESTFAC=2 por clave compuesta. No toca nada más."""
    _set_cfg(db)
    client = FakeCobroClient(f_fac=[_fac(1, 260729, 72.60)])
    result = register_invoice_collection(
        client, db, serie=1, codigo=260729, contrapartida="6",
        fecha="10/09/2026", forma="Transferencia", ejercicio="2026",
    )
    assert result["registered"] is True and result["status"] == "registered"
    assert result["importe"] == 72.60            # = total de la factura
    assert result["estfac_marked"] is True
    assert client.writes == [("F_LCO", {
        "TFALCO": "1", "CFALCO": 260729, "LINLCO": 1,
        "FECLCO": "2026-09-10T00:00:00", "FALLCO": "2026-09-10T00:00:00",
        "IMPLCO": 72.60, "CPALCO": "6",
        "CPTLCO": "COBRO FACTURA Nº: 1 - 260729 (Transferencia)",
        "FPALCO": "002",                         # forma de pago de la factura
        "OBSLCO": "",
    })]
    # Todo lo escrito son columnas REALES de F_LCO (gotcha nº 13).
    assert set(client.writes[0][1]) <= LCO_COLUMNS
    # Y después el flag, con el escritor único de F-3 (clave compuesta).
    assert client.updates == [
        ("F_FAC", {"TIPFAC": 1, "CODFAC": 260729, "ESTFAC": "2"}),
    ]


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


def test_registro_cobro_usa_plantilla_de_fila_real(db) -> None:
    """BUGFIX BDEscribirRegistroError: el registro se construye sobre una fila
    REAL de F_LCO (misma serie y contrapartida) para no dejar vacía ninguna
    columna obligatoria (TIPLCO, UALLCO, UUMLCO, FUMLCO…), como hace la
    emisión al copiar F_LPC→F_LFA. Solo se sobreescribe lo que identifica
    ESTE cobro, respetando el tipo con el que DELSOL devuelve cada columna."""
    _set_cfg(db)
    # Plantilla de OTRA factura de la serie 5 cobrada en la 8 (TFALCO como int,
    # como lo devuelve esta instalación) + un ruido de otra serie/contrapartida.
    client = FakeCobroClient(
        f_fac=[_fac(5, 260082, 70.18, CNOFAC="ROCIO BUENO")],
        f_lco=[
            _lco_real(1, 260004, 1, 100.0, CPALCO="6"),   # otra serie
            _lco_real(5, 260004, 1, 420.74),               # misma serie+cpa 8 ← plantilla
        ],
    )
    result = register_invoice_collection(
        client, db, serie=5, codigo=260082, contrapartida="8",
        fecha="24/08/2026", forma="Transferencia", ejercicio="2026",
    )
    assert result["registered"] is True
    (tabla, rec), = client.writes
    assert tabla == "F_LCO"
    # Las 23 columnas van rellenas (heredadas de la plantilla)…
    assert set(rec) == LCO_COLUMNS
    assert rec["TIPLCO"] == 1 and rec["UALLCO"] == "BART" and rec["TIDLCO"] == 0
    # …y las de ESTE cobro sobreescritas, con el tipo de la plantilla
    # (TFALCO int porque la plantilla lo trae int; CFALCO int; IMPLCO float).
    assert rec["TFALCO"] == 5 and isinstance(rec["TFALCO"], int)
    assert rec["CFALCO"] == 260082 and rec["LINLCO"] == 1
    assert rec["FECLCO"] == "2026-08-24T00:00:00" == rec["FALLCO"]
    assert rec["IMPLCO"] == 70.18 and rec["CPALCO"] == "8"
    assert rec["CPTLCO"] == "COBRO FACTURA Nº: 5 - 260082 (Transferencia)"
    assert rec["FPALCO"] == "002" and rec["OBSLCO"] == ""
    # Nunca hereda la clave de la plantilla (sería un duplicado 5-260004/L1).
    assert (rec["TFALCO"], rec["CFALCO"], rec["LINLCO"]) != (5, 260004, 1)
    # Y marca cobrada después.
    assert client.updates == [("F_FAC", {"TIPFAC": 5, "CODFAC": 260082, "ESTFAC": "2"})]


def test_plantilla_prefiere_misma_serie_y_contrapartida() -> None:
    from app.integrations.factusol.collections_write import pick_template_row

    rows = [
        _lco_real(1, 1, 1, 1.0, CPALCO="6"),
        _lco_real(5, 2, 1, 1.0, CPALCO="14"),
        _lco_real(5, 3, 1, 1.0, CPALCO="8"),
    ]
    assert pick_template_row(rows, serie=5, contrapartida="8")["CFALCO"] == 3
    assert pick_template_row(rows, serie=5, contrapartida="99")["CFALCO"] == 2   # misma serie
    assert pick_template_row(rows, serie=2, contrapartida="2")["CFALCO"] == 1    # cualquiera
    assert pick_template_row([], serie=5, contrapartida="8") is None


def test_registro_cobro_linlco_correlativo_y_saldo(db) -> None:
    """Con un cobro parcial previo, la nueva línea es LINLCO=2 y el importe por
    defecto es el SALDO (nunca se sobrepaga)."""
    _set_cfg(db)
    client = FakeCobroClient(
        f_fac=[_fac(5, 260082, 100.0, estfac="1")],
        f_lco=[_cobro(5, 260082, 1, 40.0)],
    )
    result = register_invoice_collection(
        client, db, serie=5, codigo=260082, contrapartida="8",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert result["registered"] is True
    assert result["linlco"] == 2 and result["importe"] == 60.0


def test_cobro_idempotente(db) -> None:
    """Ya cobrada (saldo 0 o ESTFAC=2) → `already`, sin escribir NADA."""
    _set_cfg(db)
    # a) saldo 0 por cobros previos.
    saldada = FakeCobroClient(
        f_fac=[_fac(1, 260729, 72.60)], f_lco=[_cobro(1, 260729, 1, 72.60)],
    )
    r1 = register_invoice_collection(
        saldada, db, serie=1, codigo=260729, contrapartida="6",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert r1["registered"] is False and r1["status"] == "already"
    assert saldada.writes == [] and saldada.updates == []
    # b) marcada cobrada (ESTFAC=2) aunque F_LCO no tenga la línea.
    marcada = FakeCobroClient(f_fac=[_fac(1, 260729, 72.60, estfac="2")])
    r2 = register_invoice_collection(
        marcada, db, serie=1, codigo=260729, contrapartida="6",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert r2["status"] == "already"
    assert marcada.writes == [] and marcada.updates == []


def test_cobro_no_existe_o_falla_no_marca(db) -> None:
    """Factura inexistente → no se escribe; fallo al escribir F_LCO → NO se
    marca ESTFAC (nunca queda «cobrada» sin cobro)."""
    _set_cfg(db)
    vacio = FakeCobroClient()
    r = register_invoice_collection(
        vacio, db, serie=1, codigo=1, contrapartida="6",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert r["status"] == "invoice_not_found" and vacio.writes == []
    roto = FakeCobroClient(f_fac=[_fac(1, 260729, 72.60)], fail_write=True)
    r = register_invoice_collection(
        roto, db, serie=1, codigo=260729, contrapartida="6",
        fecha="2026-09-10", ejercicio="2026",
    )
    assert r["registered"] is False and r["status"] == "write_failed"
    assert roto.updates == []          # ESTFAC intacto


def test_fecha_y_concepto() -> None:
    assert factusol_datetime("2026-09-10") == "2026-09-10T00:00:00"
    assert factusol_datetime("10/09/2026") == "2026-09-10T00:00:00"
    assert factusol_datetime("10-09-2026") == "2026-09-10T00:00:00"
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
    """BUG secundario: `/documents/facturas/collection-status/{job_id}` (y el
    `payment-status` de F-3, misma forma) caían en la ruta genérica
    `/documents/{doc_type}/{serie}/{codigo}` (serie:int) declarada antes →
    422. Deben resolverse como rutas de estado (200, `pending` sin Redis)."""
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
    importe previsto = total; ya cobrada → `already` sin encolar."""
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
    assert enq.call_args.args[:4] == (1, 260729, "6", "2026-09-10T00:00:00")

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
