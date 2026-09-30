"""ERP · Cobros — «Anular / Corregir cobro» registrado por BoHub y cobros PARCIALES.

Caso real: 5-260108 (SISTEMAS DE EMBALAJE, BOP-099940, 333,96 €) cobrado por
BoHub el 23/09/2026 con contrapartida 8. BoHub solo escribe su línea de `F_LCO`
(TRALCO=0, sin movimiento de tesorería) + `ESTFAC`, así que desde FACTUSOL no se
puede deshacer: «Anular cobro» borra EXACTAMENTE esa línea (serie + número +
LINLCO, y solo si fecha, importe y contrapartida siguen siendo los registrados)
y deja `ESTFAC` según lo que quede cobrado (0 pendiente / 1 parcial / 2
cobrada). El pedido BOPRIN-99940 vale 325,49 y la factura 333,96: el cobro
puede ser parcial y nunca por más de lo pendiente en FACTUSOL.
"""
from __future__ import annotations

import json
import re
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.factusol_cobro import ANNULLED_EVENT, REGISTERED_EVENT, bohub_collections
from app.erp.models import Order, OrderSource, OrderStatusHistory, PaymentStatus, StatusDomain
from app.integrations.factusol.collections_write import (
    annul_invoice_collection,
    register_invoice_collection,
)
from app.integrations.factusol.jobs import (
    annul_invoice_collection_job,
    register_invoice_collection_job,
)
from app.main import app
from app.models.crm import AuditLog, Company
from tests._test_helpers import auth_headers, seed_test_users

# --- FACTUSOL simulado que SÍ borra y actualiza ------------------------------------


class FakeFactusol:
    """F_FAC / F_LCO en memoria: `EscribirRegistro` añade la línea,
    `ActualizarRegistro` cambia ESTFAC y `BorrarRegistros` borra por el filtro
    `TFALCO='s' AND CFALCO=c AND LINLCO=l` (como la API real). Guarda todo lo
    que se escribe/borra para comprobar que no se toca nada más."""

    default_ejercicio = "2026"

    def __init__(self, *, f_fac, f_lco=None):
        self.f_fac = [dict(r) for r in f_fac]
        self.f_lco = [dict(r) for r in (f_lco or [])]
        self.writes: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict]] = []
        self.deletes: list[tuple[str, str]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla == "F_FAC":
            if filtro.startswith("CODFAC="):
                wanted = filtro.split("=", 1)[1].strip()
                return [dict(r) for r in self.f_fac if str(r["CODFAC"]) == wanted]
            return [dict(r) for r in self.f_fac]
        if tabla == "F_LCO":
            return [dict(r) for r in self.f_lco]
        if tabla == "F_FPA":
            return [{"CODFPA": "002", "DESFPA": "Transferencia"}]
        return []

    def write_record(self, tabla, data, *, ejercicio=None):
        self.writes.append((tabla, dict(data)))
        if tabla == "F_LCO":
            self.f_lco.append(dict(data))
        return {"ok": True}

    def update_record(self, tabla, data, *, ejercicio=None):
        self.updates.append((tabla, dict(data)))
        for r in self.f_fac:
            if str(r["TIPFAC"]) == str(data["TIPFAC"]) and str(r["CODFAC"]) == str(data["CODFAC"]):
                r["ESTFAC"] = data["ESTFAC"]
        return {"ok": True}

    def delete_records(self, tabla, filtro, *, ejercicio=None):
        self.deletes.append((tabla, filtro))
        m = re.fullmatch(r"TFALCO='(\d+)' AND CFALCO=(\d+) AND LINLCO=(\d+)", filtro)
        assert m, f"filtro inesperado: {filtro}"
        s, c, line = m.groups()
        self.f_lco = [
            r for r in self.f_lco
            if not (str(r["TFALCO"]) == s and str(r["CFALCO"]) == c and str(r["LINLCO"]) == line)
        ]
        return {"ok": True}

    def estfac(self, serie, codigo):
        return next(str(r["ESTFAC"]) for r in self.f_fac
                    if str(r["TIPFAC"]) == str(serie) and r["CODFAC"] == codigo)

    def lineas(self, serie, codigo):
        return sorted(
            (int(r["LINLCO"]), float(r["IMPLCO"])) for r in self.f_lco
            if str(r["TFALCO"]) == str(serie) and int(r["CFALCO"]) == codigo
        )


def _fac(serie, codigo, total, estfac="0"):
    return {"TIPFAC": str(serie), "CODFAC": codigo, "TOTFAC": total, "ESTFAC": estfac,
            "CNOFAC": "SISTEMAS DE EMBALAJE", "REFFAC": "BOP-099940", "FOPFAC": "002",
            "CLIFAC": "55555", "FECFAC": "2026-09-30T00:00:00"}


def _lco(serie, codigo, linea, importe, *, fecha="2026-09-23", cpa=8, tralco=0, mullco=0):
    return {
        "ANTLCO": 0, "CAJLCO": 0, "CFALCO": codigo, "CPALCO": cpa,
        "CPTLCO": f"COBRO FACTURA Nº: {serie} - {codigo}",
        "FALLCO": f"{fecha}T00:00:00", "FECLCO": f"{fecha}T00:00:00",
        "FPALCO": "", "FUMLCO": "1900-01-01T00:00:00", "IMPLCO": importe,
        "LINLCO": linea, "MULLCO": mullco, "OBSLCO": "", "PCALCO": 0, "PROLCO": "",
        "TERLCO": 0, "TFALCO": str(serie), "TIDLCO": "", "TIPLCO": 0,
        "TPVIDLCO": "", "TRALCO": tralco, "UALLCO": 8, "UUMLCO": 0,
    }


# Otra factura con su cobro: nunca se toca.
OTRA = _lco(1, 260729, 1, 72.60, cpa=6, fecha="2026-09-10", mullco=51)


# --- fixtures ---------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session_factory(engine) -> Generator[sessionmaker, None, None]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(Company(id="acme", name="Sistemas de Embalaje", factusol_company_id="55555"))
        seed.add(Order(
            id="o-940", external_source=OrderSource.WOOCOMMERCE, external_id="99940",
            order_number="BOPRIN-99940", company_id="acme", total_amount=325.49,
            payment_status=PaymentStatus.PAID, invoice_status="invoiced_by_erp",
            factusol_invoice_number="260108", factusol_invoice_serie=5,
        ))
        seed.commit()
    yield factory


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


def _patched(fake, engine):
    return (
        patch("app.integrations.factusol.client.FactusolClient.from_settings",
              return_value=fake),
        patch("app.db.session.get_engine", return_value=engine),
    )


def _run(fn, fake, engine, *args, **kwargs):
    a, b = _patched(fake, engine)
    with a, b:
        return fn(*args, **kwargs)


# --- motor: anular ---------------------------------------------------------------------


def test_anular_borra_solo_esa_linea_y_la_factura_vuelve_a_pendiente(db) -> None:
    """5-260108: la línea de BoHub desaparece, ESTFAC=2 → 0 y el cobro de OTRA
    factura sigue ahí; el borrado va por serie + número + LINLCO."""
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96, estfac="2"), _fac(1, 260729, 72.60, "2")],
                        f_lco=[_lco(5, 260108, 1, 333.96), OTRA])
    r = annul_invoice_collection(
        fake, db, serie=5, codigo=260108, linlco=1,
        esperado={"fecha": "2026-09-23", "importe": 333.96, "contrapartida": "8"},
        ejercicio="2026",
    )
    assert r["annulled"] is True and r["status"] == "annulled"
    assert fake.deletes == [("F_LCO", "TFALCO='5' AND CFALCO=260108 AND LINLCO=1")]
    assert fake.lineas(5, 260108) == []
    assert fake.lineas(1, 260729) == [(1, 72.60)]          # la otra, intacta
    assert fake.estfac(5, 260108) == "0"
    assert fake.updates == [("F_FAC", {"TIPFAC": 5, "CODFAC": 260108, "ESTFAC": "0"})]
    assert r["saldo_pendiente"] == 333.96 and r["estfac_antes"] == "2"
    assert fake.writes == []                                # nada más


def test_anular_un_parcial_deja_el_resto_y_parcial(db) -> None:
    """Dos parciales (200 + 100 de 333,96): anular el de 100 → la línea 1
    sigue, saldo 133,96 y ESTFAC=1 (parcial)."""
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96, estfac="1")],
                        f_lco=[_lco(5, 260108, 1, 200.0),
                               _lco(5, 260108, 2, 100.0, fecha="2026-09-25")])
    r = annul_invoice_collection(
        fake, db, serie=5, codigo=260108, linlco=2,
        esperado={"fecha": "2026-09-25", "importe": 100.0, "contrapartida": "8"},
        ejercicio="2026",
    )
    assert r["annulled"] is True
    assert fake.lineas(5, 260108) == [(1, 200.0)]
    assert r["saldo_pendiente"] == 133.96 and r["estado_real"] == "parcial"
    assert fake.estfac(5, 260108) == "1" and fake.updates == []   # ya era 1


@pytest.mark.parametrize(("linea", "motivo"), [
    (_lco(5, 260108, 1, 333.96, fecha="2026-09-24"), "fecha 2026-09-24"),
    (_lco(5, 260108, 1, 330.00), "importe 330.00"),
    (_lco(5, 260108, 1, 333.96, cpa=2), "contrapartida 2"),
    (_lco(5, 260108, 1, 333.96, tralco=1), "traspasada a tesorería"),
])
def test_linea_que_no_coincide_no_se_borra(db, linea, motivo) -> None:
    """Si la editaron (o la traspasaron a tesorería) en FACTUSOL: no se borra
    NADA y el error dice qué se encontró."""
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96, estfac="2")], f_lco=[linea])
    r = annul_invoice_collection(
        fake, db, serie=5, codigo=260108, linlco=1,
        esperado={"fecha": "2026-09-23", "importe": 333.96, "contrapartida": "8"},
        ejercicio="2026",
    )
    assert r["annulled"] is False and r["status"] == "mismatch"
    assert motivo in r["motivo"] and "No se ha borrado nada" in r["motivo"]
    assert fake.deletes == [] and fake.updates == [] and fake.writes == []


def test_linea_ya_borrada_solo_corrige_estfac(db) -> None:
    """Caso 5-260108 tal como quedó el 30/09: la línea ya se borró por la API
    pero ESTFAC seguía en 2. Anular no borra nada y deja ESTFAC=0."""
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96, estfac="2")], f_lco=[OTRA])
    r = annul_invoice_collection(
        fake, db, serie=5, codigo=260108, linlco=1,
        esperado={"fecha": "2026-09-23", "importe": 333.96, "contrapartida": "8"},
        ejercicio="2026",
    )
    assert r["annulled"] is True and r["status"] == "line_missing"
    assert fake.deletes == []
    assert fake.estfac(5, 260108) == "0" and r["saldo_pendiente"] == 333.96


# --- motor: cobros parciales -----------------------------------------------------------


def test_cobro_parcial_y_segundo_cobro_la_deja_cobrada(db) -> None:
    """BOPRIN-99940: 325,49 sobre una factura de 333,96 → línea de 325,49,
    ESTFAC=1 y 8,47 pendientes; un segundo cobro de 8,47 → ESTFAC=2."""
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96)], f_lco=[OTRA])
    r1 = register_invoice_collection(
        fake, db, serie=5, codigo=260108, contrapartida="8", fecha="2026-09-30",
        importe=325.49, ejercicio="2026",
    )
    assert r1["registered"] is True and r1["parcial"] is True
    assert r1["saldo_pendiente"] == 8.47 and r1["estfac"] == "1"
    assert fake.estfac(5, 260108) == "1"
    nueva = [rec for t, rec in fake.writes if t == "F_LCO"][0]
    assert nueva["IMPLCO"] == 325.49 and nueva["TRALCO"] == 0 and nueva["MULLCO"] == 0
    r2 = register_invoice_collection(
        fake, db, serie=5, codigo=260108, contrapartida="8", fecha="2026-10-01",
        ejercicio="2026",
    )
    assert r2["registered"] is True and r2["importe"] == 8.47 and r2["linlco"] == 2
    assert r2["cobrada"] is True and fake.estfac(5, 260108) == "2"
    assert fake.lineas(5, 260108) == [(1, 325.49), (2, 8.47)]


def test_importe_mayor_que_lo_pendiente_se_rechaza(db) -> None:
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96)], f_lco=[_lco(5, 260108, 1, 325.49)])
    r = register_invoice_collection(
        fake, db, serie=5, codigo=260108, contrapartida="8", fecha="2026-10-01",
        importe=100.0, ejercicio="2026",
    )
    assert r["registered"] is False and r["status"] == "amount_exceeds_pending"
    assert "8.47" in r["motivo"] and fake.writes == [] and fake.updates == []


# --- jobs: registrar → anular → corregir, con auditoría y pedido ------------------------


def test_anular_por_job_anota_historial_y_deja_el_pedido_pendiente(
    session_factory, engine,
) -> None:
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96)], f_lco=[OTRA])
    reg = _run(register_invoice_collection_job, fake, engine,
               5, 260108, "8", "2026-09-23T00:00:00", None, None, None,
               actor_user_id=None, meta={"numero": "5-260108", "referencia": "BOP-099940"})
    assert reg["registered"] is True
    with session_factory() as s:
        assert s.get(Order, "o-940").factusol_cobro_status == "cobrada"
        (cobro,) = bohub_collections(s, serie=5, codigo=260108)
        assert cobro["anulable"] and cobro["linlco"] == 1 and cobro["importe"] == 333.96
    res = _run(annul_invoice_collection_job, fake, engine, cobro["id"])
    assert res["annulled"] is True, res
    assert fake.lineas(5, 260108) == [] and fake.estfac(5, 260108) == "0"
    assert fake.lineas(1, 260729) == [(1, 72.60)]
    with session_factory() as s:
        o = s.get(Order, "o-940")
        assert o.factusol_cobro_status == "pendiente"
        hist = s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == "o-940",
            OrderStatusHistory.domain == StatusDomain.PAYMENT,
        )).all()
        assert any(h.reason == "Cobro de 333,96 € del 23/09/2026 (contrapartida 8) anulado"
                   for h in hist)
        ev = s.scalars(select(AuditLog).where(AuditLog.action == ANNULLED_EVENT)).one()
        assert json.loads(ev.metadata_json)["registered_event_id"] == cobro["id"]
        (cobro,) = bohub_collections(s, serie=5, codigo=260108)
        assert cobro["anulado"] is True and cobro["anulable"] is False
    # Una segunda anulación no hace nada.
    again = _run(annul_invoice_collection_job, fake, engine, cobro["id"])
    assert again["status"] == "already_annulled" and len(fake.deletes) == 1


def test_corregir_anula_y_registra_el_nuevo(session_factory, engine) -> None:
    """Corregir = anular + registrar con fecha / contrapartida nuevas: al final
    UNA sola línea en F_LCO, la nueva."""
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96)], f_lco=[OTRA])
    _run(register_invoice_collection_job, fake, engine,
         5, 260108, "8", "2026-09-23T00:00:00", None, None, None,
         actor_user_id=None, meta={"numero": "5-260108"})
    with session_factory() as s:
        (cobro,) = bohub_collections(s, serie=5, codigo=260108)
    res = _run(annul_invoice_collection_job, fake, engine, cobro["id"],
               correction={"contrapartida": "2", "fecha": "2026-09-30T00:00:00",
                           "importe": 325.49})
    assert res["annulled"] is True and res["correccion"]["registered"] is True
    lineas = [r for r in fake.f_lco if int(r["CFALCO"]) == 260108]
    assert len(lineas) == 1
    assert (lineas[0]["IMPLCO"], str(lineas[0]["CPALCO"]), lineas[0]["FECLCO"]) == (
        325.49, "2", "2026-09-30T00:00:00")
    assert fake.estfac(5, 260108) == "1"            # 325,49 de 333,96: parcial
    with session_factory() as s:
        assert s.get(Order, "o-940").factusol_cobro_status == "pendiente"
        cobros = bohub_collections(s, serie=5, codigo=260108)
        assert [(c["anulado"], c["importe"]) for c in cobros] == [(True, 333.96), (False, 325.49)]


# --- API ---------------------------------------------------------------------------------


def test_api_lista_cobros_de_bohub_aviso_de_descuadre_y_anular(
    http, session_factory, engine,
) -> None:
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96)], f_lco=[OTRA])
    _run(register_invoice_collection_job, fake, engine,
         5, 260108, "8", "2026-09-23T00:00:00", None, None, None,
         actor_user_id=None, meta={"numero": "5-260108"})
    a, b = _patched(fake, engine)
    with a, b:
        info = http.get("/api/erp/orders/o-940/factusol-cobro",
                        headers=auth_headers(http, "user")).json()
    assert info["status"] == "cobrada"
    (cobro,) = info["bohub_cobros"]
    assert cobro["anulable"] is True and cobro["contrapartida"] == "8"
    assert info["total_mismatch"] == {"pedido": 325.49, "factura": 333.96, "diferencia": 8.47}
    admin = auth_headers(http, "admin")
    url = f"/api/erp/orders/o-940/factusol-cobros/{cobro['id']}"
    assert http.post(f"{url}/anular", json={}, headers=admin).status_code == 400
    assert http.post("/api/erp/orders/o-940/factusol-cobros/no-existe/anular",
                     json={"confirm": True}, headers=admin).status_code == 404
    r = http.post(f"{url}/corregir", json={"confirm": True, "cuenta": "99",
                                          "fecha": "2026-09-30"}, headers=admin)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "unknown_account"
    with patch("app.integrations.factusol.jobs.enqueue_annul_invoice_collection",
               return_value="job-a") as enq:
        r = http.post(f"{url}/anular", json={"confirm": True}, headers=admin)
    assert r.status_code == 202 and r.json()["job_id"] == "job-a"
    assert enq.call_args.args[0] == cobro["id"]
    # Sin permiso de cobro (comercial) no se anula.
    assert http.post(f"{url}/anular", json={"confirm": True},
                     headers=auth_headers(http, "manager")).status_code == 403


def test_api_importe_mayor_que_lo_pendiente_400(http, engine) -> None:
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96)], f_lco=[_lco(5, 260108, 1, 325.49)])
    a, b = _patched(fake, engine)
    with a, b, patch("app.integrations.factusol.jobs.enqueue_register_invoice_collection") as enq:
        r = http.post("/api/erp/factusol/documents/facturas/5/260108/collection",
                      headers=auth_headers(http, "admin"),
                      json={"confirm": True, "cuenta": "8", "fecha": "2026-10-01",
                            "importe": 20.0})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "amount_exceeds_pending"
    assert "8.47" in r.json()["detail"]["detail"]
    enq.assert_not_called()


def test_estado_de_cobro_en_los_dos_sentidos(http, session_factory, engine) -> None:
    """El pedido constaba «cobrada», pero en FACTUSOL ya no hay líneas (y
    ESTFAC se quedó en 2): al comprobar vuelve a «pendiente» y se avisa."""
    with session_factory() as s:
        s.get(Order, "o-940").factusol_cobro_status = "cobrada"
        s.commit()
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96, estfac="2")], f_lco=[OTRA])
    a, b = _patched(fake, engine)
    with a, b:
        info = http.get("/api/erp/orders/o-940/factusol-cobro",
                        headers=auth_headers(http, "user")).json()
    assert info["status"] == "pendiente" and info["saldo_pendiente"] == 333.96
    assert any("ESTFAC=2" in w for w in info["warnings"])
    with session_factory() as s:
        assert s.get(Order, "o-940").factusol_cobro_status == "pendiente"


def test_cobro_no_registrado_por_bohub_no_se_puede_anular(http, session_factory, engine) -> None:
    """Un cobro hecho a mano en FACTUSOL no está en la auditoría de BoHub: no
    aparece como anulable y el endpoint no lo encuentra."""
    fake = FakeFactusol(f_fac=[_fac(5, 260108, 333.96, estfac="2")],
                        f_lco=[_lco(5, 260108, 1, 333.96, tralco=1)])
    a, b = _patched(fake, engine)
    with a, b:
        info = http.get("/api/erp/orders/o-940/factusol-cobro",
                        headers=auth_headers(http, "user")).json()
    assert info["status"] == "cobrada" and info["bohub_cobros"] == []
    with session_factory() as s:
        assert s.scalars(select(AuditLog).where(AuditLog.action == REGISTERED_EVENT)).all() == []
