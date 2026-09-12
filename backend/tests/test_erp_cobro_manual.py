"""ERP · «Registrar cobro en FACTUSOL» (manual) desde la ficha y la bandeja.

Reutiliza el motor F-4-B tal cual (`register_invoice_collection` vía
`POST /factusol/documents/facturas/{serie}/{codigo}/collection`, cola
`factusol:writes`, solo `F_LCO` + `ESTFAC=2`). Lo nuevo es resolver la factura
del pedido (clave compuesta: el pedido guarda solo el CODFAC), su estado de
cobro EN VIVO (`GET /orders/{id}/factusol-cobro`), persistirlo para la bandeja
(indicador «Cobrado FACTUSOL» / «Pendiente de cobro» + filtro, distinto del
«Pagado» del CRM) y el refresco en bloque («Actualizar cobros»).
"""
from __future__ import annotations

import json
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
from app.erp.factusol_cobro import (
    COBRO_KEY,
    mark_orders_after_collection,
    parse_invoice_number,
    resolve_invoice_key,
)
from app.erp.models import (
    Order,
    OrderSource,
    OrderStatusHistory,
    PaymentStatus,
    StatusDomain,
)
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.jobs import register_invoice_collection_job
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users

# --- FACTUSOL simulado ------------------------------------------------------------


class FakeCobroClient:
    """F_FAC / F_LCO / F_FPA en memoria; registra escrituras (F_LCO) y
    actualizaciones (ESTFAC) como el motor real. Cuenta las lecturas para
    comprobar que el refresco en bloque no hace N+1."""

    def __init__(self, *, f_fac=None, f_lco=None, fail_write=None):
        self.default_ejercicio = "2026"
        self.f_fac = f_fac or []
        self.f_lco = f_lco or []
        self._fail_write = fail_write
        self.writes: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict]] = []
        self.loaded: list[str] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        self.loaded.append(tabla)
        if tabla == "F_FAC":
            # El motor F-4-B filtra por CODFAC en el servidor (`invoice_row`);
            # el refresco en bloque lee todo (`1=1`) y filtra en memoria.
            if filtro.startswith("CODFAC="):
                wanted = filtro.split("=", 1)[1].strip()
                return [r for r in self.f_fac if str(r.get("CODFAC")) == wanted]
            return list(self.f_fac)
        if tabla == "F_LCO":
            return list(self.f_lco)
        if tabla == "F_FPA":
            return [{"CODFPA": "002", "DESFPA": "Transferencia"},
                    {"CODFPA": "005", "DESFPA": "Paypal"}]
        return []

    def write_record(self, tabla, data, *, ejercicio=None):
        if self._fail_write == tabla:
            raise FactusolError(f"write {tabla} failed", status=500)
        self.writes.append((tabla, dict(data)))
        return {"ok": True}

    def update_record(self, tabla, data, *, ejercicio=None):
        self.updates.append((tabla, dict(data)))
        for row in self.f_fac:
            if str(row.get("CODFAC")) == str(data.get("CODFAC")) and "ESTFAC" in data:
                row["ESTFAC"] = data["ESTFAC"]
        return {"ok": True}


def _fac(serie, codigo, total, estfac="0", **over):
    row = {
        "TIPFAC": str(serie), "CODFAC": codigo, "TOTFAC": total,
        "ESTFAC": estfac, "CNOFAC": "NEONLED SL", "REFFAC": f"BOP-{codigo:06d}",
        "FOPFAC": "002", "FECFAC": "2026-08-15T00:00:00",
    }
    row.update(over)
    return row


def _lco(serie, codigo, linea, importe, cpa=8):
    return {
        "ANTLCO": 0, "CAJLCO": 0, "CFALCO": codigo, "CPALCO": cpa,
        "CPTLCO": f"COBRO FACTURA Nº: {serie} - {codigo}",
        "FALLCO": "2026-08-21T00:00:00", "FECLCO": "2026-08-20T00:00:00",
        "FPALCO": "", "FUMLCO": "1900-01-01T00:00:00", "IMPLCO": importe,
        "LINLCO": linea, "MULLCO": 51, "OBSLCO": "", "PCALCO": 0, "PROLCO": "",
        "TERLCO": 0, "TFALCO": str(serie), "TIDLCO": "", "TIPLCO": 0,
        "TPVIDLCO": "", "TRALCO": 0, "UALLCO": cpa, "UUMLCO": 0,
    }


def _patched(client):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=client,
    )


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
        seed.add(Company(id="acme", name="Acme SL", factusol_company_id="55555"))
        seed.commit()
    yield factory


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _order(s: Session, oid: str, number: str, *, invoice: str | None = None,
           source: OrderSource = OrderSource.WOOCOMMERCE,
           payment: PaymentStatus = PaymentStatus.PENDING,
           serie: int | None = None, cobro_status: str | None = None,
           albaran: str | None = None, total: float = 72.6) -> Order:
    o = Order(
        id=oid, external_source=source, external_id=number.split("-")[-1],
        order_number=number, company_id="acme", total_amount=total,
        payment_status=payment, factusol_invoice_number=invoice,
        factusol_invoice_serie=serie, factusol_cobro_status=cobro_status,
        factusol_albaran_number=albaran,
        invoice_status="invoiced_by_erp" if invoice else "not_invoiced",
    )
    s.add(o)
    return o


def _get_cobro(http, oid: str, role: str = "user"):
    return http.get(f"/api/erp/orders/{oid}/factusol-cobro", headers=auth_headers(http, role))


# --- ficha ------------------------------------------------------------------------


def test_registrar_cobro_manual_desde_ficha(http, session_factory, engine) -> None:
    """Ficha: el pedido tiene factura pendiente → `GET factusol-cobro` la
    resuelve (serie + código, saldo, forma de pago, cuenta sugerida) y la
    deja persistida; el cobro se registra con el MISMO endpoint F-4-B de la
    factura (202 + job) y el job (motor tal cual: F_LCO + ESTFAC=2) deja el
    pedido «cobrada» en la bandeja."""
    with session_factory() as s:
        _order(s, "o-1", "BOPRIN-99930", invoice="260729")   # CODFAC desnudo
        s.commit()
    fake = FakeCobroClient(f_fac=[_fac(1, 260729, 72.60)])
    with _patched(fake):
        r = _get_cobro(http, "o-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pendiente"
    assert body["invoice"] == {"serie": 1, "codigo": 260729, "numero": "1-260729"}
    assert body["total"] == 72.6 and body["saldo_pendiente"] == 72.6
    assert body["cobros"] == 0 and body["warnings"] == []
    assert body["forma_pago_nombre"] == "Transferencia"
    # Serie 1 = Bomedia → «Bomedia Sabadell» (6) como cuenta por defecto.
    assert body["suggested_cuenta"] == {"codigo": "6", "nombre": "Bomedia Sabadell"}
    assert body["persisted_status"] == "pendiente"
    with session_factory() as s:
        o = s.get(Order, "o-1")
        assert o.factusol_cobro_status == "pendiente"
        assert o.factusol_invoice_serie == 1
        assert json.loads(o.packing_json)[COBRO_KEY]["numero"] == "1-260729"
        detail = http.get("/api/erp/orders/o-1", headers=auth_headers(http, "user")).json()
        assert detail["factusol_cobro_status"] == "pendiente"
        assert detail["factusol_cobro"]["saldo_pendiente"] == 72.6

    # Registrar: el endpoint F-4-B de la factura, con la clave resuelta.
    with _patched(fake), patch(
        "app.integrations.factusol.jobs.enqueue_register_invoice_collection",
        return_value="job-1",
    ) as enq:
        r = http.post(
            "/api/erp/factusol/documents/facturas/1/260729/collection",
            headers=auth_headers(http, "admin"),
            json={"confirm": True, "cuenta": "6", "fecha": "2026-09-12",
                  "forma": "Transferencia", "observaciones": "cobro manual ficha"},
        )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued" and r.json()["importe"] == 72.6
    assert enq.call_args.args[:4] == (1, 260729, "6", "2026-09-12T00:00:00")

    # El job: SOLO F_LCO + ESTFAC=2, y el pedido queda «cobrada».
    with _patched(fake), patch("app.db.session.get_engine", return_value=engine):
        result = register_invoice_collection_job(
            1, 260729, "6", "2026-09-12T00:00:00", None, "Transferencia",
            "cobro manual ficha", actor_user_id=None,
            meta={"numero": "1-260729", "referencia": "BOP-260729"},
        )
    assert result["registered"] is True and result["status"] == "registered"
    assert [t for t, _ in fake.writes] == ["F_LCO"]
    assert fake.writes[0][1]["IMPLCO"] == 72.6
    assert str(fake.writes[0][1]["CPALCO"]) == "6"
    assert fake.updates and fake.updates[0][1]["ESTFAC"] == "2"
    assert result["orders_updated"] == ["o-1"]
    with session_factory() as s:
        o = s.get(Order, "o-1")
        assert o.factusol_cobro_status == "cobrada"
        block = json.loads(o.packing_json)[COBRO_KEY]
        assert block["cobrada"] is True and block["saldo_pendiente"] == 0.0
        assert block["source"] == "manual"
        hist = s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == "o-1",
            OrderStatusHistory.domain == StatusDomain.PAYMENT,
        )).all()
        assert any("registrado en FACTUSOL" in h.reason for h in hist)
        # El «Pagado» del CRM NO cambia: es otro estado.
        assert o.payment_status == PaymentStatus.PENDING

    # Re-chequeo en vivo tras el cobro (saldo ≈ 0 / ESTFAC=2) → «cobrada».
    fake.f_lco.append(_lco(1, 260729, 1, 72.6, cpa=6))
    with _patched(fake):
        r = _get_cobro(http, "o-1")
    assert r.json()["status"] == "cobrada" and r.json()["saldo_pendiente"] == 0.0


def test_cobro_manual_idempotente_no_doble(http, session_factory) -> None:
    """Ya cobrada (ESTFAC=2 / saldo 0) → `GET` dice «cobrada» y el endpoint
    F-4-B responde `already` SIN encolar nada. Con una línea de cobro previa
    sin llegar al total (anticipo / posible doble cobro) se AVISA pero se
    permite registrar el saldo (control manual, no se bloquea)."""
    with session_factory() as s:
        _order(s, "o-cobrada", "BOPRIN-99931", invoice="260730", payment=PaymentStatus.PAID)
        _order(s, "o-parcial", "BOPRIN-99932", invoice="260731")
        s.commit()
    fake = FakeCobroClient(
        f_fac=[_fac(1, 260730, 50.0, estfac="2"), _fac(1, 260731, 100.0, estfac="1")],
        f_lco=[_lco(1, 260730, 1, 50.0), _lco(1, 260731, 1, 40.0)],
    )
    with _patched(fake):
        r = _get_cobro(http, "o-cobrada")
    assert r.json()["status"] == "cobrada" and r.json()["cobros"] == 1
    with _patched(fake), patch(
        "app.integrations.factusol.jobs.enqueue_register_invoice_collection",
    ) as enq:
        r = http.post(
            "/api/erp/factusol/documents/facturas/1/260730/collection",
            headers=auth_headers(http, "admin"),
            json={"confirm": True, "cuenta": "6", "fecha": "2026-09-12"},
        )
    assert r.status_code == 202 and r.json()["status"] == "already"
    enq.assert_not_called()
    assert fake.writes == []

    # Anticipo: 40 € de 100 → pendiente con aviso; el importe previsto es el
    # saldo (60 €), nunca se sobrepaga.
    with _patched(fake):
        r = _get_cobro(http, "o-parcial")
    body = r.json()
    assert body["status"] == "pendiente"
    assert body["cobros"] == 1 and body["saldo_pendiente"] == 60.0
    assert any("posible doble cobro" in w for w in body["warnings"])
    assert any("cobro parcial" in w for w in body["warnings"])
    with _patched(fake), patch(
        "app.integrations.factusol.jobs.enqueue_register_invoice_collection",
        return_value="job-2",
    ) as enq:
        r = http.post(
            "/api/erp/factusol/documents/facturas/1/260731/collection",
            headers=auth_headers(http, "admin"),
            json={"confirm": True, "cuenta": "6", "fecha": "2026-09-12"},
        )
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert r.json()["importe"] == 60.0
    enq.assert_called_once()
    # Solo con permiso de edición ERP.
    r = http.post(
        "/api/erp/factusol/documents/facturas/1/260731/collection",
        headers=auth_headers(http, "manager"),
        json={"confirm": True, "cuenta": "6", "fecha": "2026-09-12"},
    )
    assert r.status_code == 403


def test_cobro_manual_sin_factura_boton_deshabilitado(http, session_factory) -> None:
    """Sin factura: `GET` responde 200 `sin_factura` (el botón se deshabilita
    con tooltip «emite la factura primero», nada de error rojo) SIN tocar
    FACTUSOL, y la bandeja no lleva estado de cobro. Con CODFAC que no está en
    F_FAC (o ambiguo entre series) tampoco se adivina: `unresolved`."""
    with session_factory() as s:
        _order(s, "o-sin", "BOPRIN-99940")
        _order(s, "o-perdida", "BOPRIN-99941", invoice="999999")
        _order(s, "o-ambigua", "BOPRIN-99942", invoice="260750")
        s.commit()
    with patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        side_effect=AssertionError("no debe consultar FACTUSOL"),
    ):
        r = _get_cobro(http, "o-sin")
    assert r.status_code == 200
    assert r.json()["status"] == "sin_factura" and r.json()["invoice"] is None
    assert "emite la factura" in r.json()["detail"]
    rows = http.get("/api/erp/orders", headers=auth_headers(http, "user")).json()["items"]
    by_id = {x["id"]: x for x in rows}
    assert by_id["o-sin"]["factusol_cobro_status"] is None
    assert by_id["o-sin"]["factusol_cobro"] is None

    fake = FakeCobroClient(f_fac=[
        _fac(1, 260750, 10.0, REFFAC="X-1"), _fac(5, 260750, 20.0, REFFAC="Y-2"),
    ])
    with _patched(fake):
        perdida = _get_cobro(http, "o-perdida").json()
        ambigua = _get_cobro(http, "o-ambigua").json()
    assert perdida["status"] == "unresolved" and "999999" in perdida["detail"]
    assert ambigua["status"] == "unresolved" and "varias series" in ambigua["detail"]
    with session_factory() as s:
        assert s.get(Order, "o-ambigua").factusol_cobro_status is None


# --- bandeja ---------------------------------------------------------------------


def test_registrar_cobro_manual_desde_bandeja(http, session_factory) -> None:
    """Bandeja: «Actualizar cobros FACTUSOL» comprueba de una vez todos los
    pedidos con factura (F_FAC y F_LCO se leen UNA vez, sin N+1), persiste el
    estado y devuelve las filas; el filtro `cobro=` localiza los que faltan
    por registrar. La factura precargada en el modal es la misma clave que
    resuelve la ficha (serie por REFFAC del pedido web si hay homónimos, o
    por la serie del albarán de la Fase 2)."""
    with session_factory() as s:
        _order(s, "o-a", "BOPRIN-99950", invoice="260760")               # pendiente
        _order(s, "o-b", "BOPRIN-99951", invoice="5-260761",              # cobrada
               payment=PaymentStatus.PENDING)
        _order(s, "o-c", "BOPRIN-99952")                                  # sin factura
        # Homónimo: 260762 existe en la serie 1 y en la 5; el pedido web es
        # BOPRIN-99953 → REFFAC BOP-099953 en la serie 5.
        _order(s, "o-d", "BOPRIN-99953", invoice="260762")
        # Fase 2: factura desde el albarán 1-100327 → serie 1.
        _order(s, "o-e", "PRO-004352", invoice="260763", source=OrderSource.FACTUSOL_PROFORMA,
               albaran="1-100327")
        s.commit()
    fake = FakeCobroClient(
        f_fac=[
            _fac(1, 260760, 30.0),
            _fac(5, 260761, 40.0, estfac="2"),
            _fac(1, 260762, 11.0, REFFAC="OTRA-1"), _fac(5, 260762, 12.0, REFFAC="BOP-099953"),
            _fac(1, 260763, 13.0), _fac(5, 260763, 14.0, REFFAC="ZZZ"),
        ],
        f_lco=[_lco(5, 260761, 1, 40.0)],
    )
    before = http.get("/api/erp/orders?cobro=sin_comprobar",
                      headers=auth_headers(http, "user")).json()["items"]
    assert {x["id"] for x in before} == {"o-a", "o-b", "o-d", "o-e"}

    with _patched(fake):
        r = http.post("/api/erp/orders/factusol-cobros/refresh", json={},
                      headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checked"] == 4
    assert fake.loaded.count("F_FAC") == 1 and fake.loaded.count("F_LCO") == 1
    by_id = {x["id"]: x for x in body["items"]}
    assert by_id["o-a"]["factusol_cobro_status"] == "pendiente"
    assert by_id["o-a"]["cobro"]["invoice"]["numero"] == "1-260760"
    assert by_id["o-b"]["factusol_cobro_status"] == "cobrada"
    assert by_id["o-b"]["factusol_invoice_serie"] == 5
    assert by_id["o-d"]["cobro"]["invoice"] == {"serie": 5, "codigo": 260762, "numero": "5-260762"}
    assert by_id["o-e"]["cobro"]["invoice"]["numero"] == "1-260763"
    assert "o-c" not in by_id

    cobradas = http.get("/api/erp/orders?cobro=cobrada",
                        headers=auth_headers(http, "user")).json()["items"]
    assert [x["id"] for x in cobradas] == ["o-b"]
    pendientes = http.get("/api/erp/orders?cobro=pendiente",
                          headers=auth_headers(http, "user")).json()["items"]
    assert {x["id"] for x in pendientes} == {"o-a", "o-d", "o-e"}
    assert http.get("/api/erp/orders?cobro=sin_comprobar",
                    headers=auth_headers(http, "user")).json()["items"] == []

    # Solo los ids pedidos (una fila tras cerrar el modal), y FACTUSOL caído
    # → 502 controlado, sin tocar lo persistido.
    with _patched(fake):
        r = http.post("/api/erp/orders/factusol-cobros/refresh", json={"ids": ["o-a"]},
                      headers=auth_headers(http, "user"))
    assert r.json()["checked"] == 1 and r.json()["items"][0]["id"] == "o-a"
    with patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        side_effect=FactusolError("caído", status=500),
    ):
        r = http.post("/api/erp/orders/factusol-cobros/refresh", json={},
                      headers=auth_headers(http, "user"))
    assert r.status_code == 502 and r.json()["detail"]["code"] == "factusol_unreachable"


def test_bandeja_indicador_cobro_factusol(http, session_factory) -> None:
    """La fila lleva el estado de cobro EN FACTUSOL («cobrada» /
    «pendiente» / null sin factura), que es independiente del «Pagado» del
    CRM: un pedido pagado en el CRM puede seguir pendiente de cobro en
    FACTUSOL y al revés. El job de cobro lo deja «cobrada» aunque el modal se
    cierre (enganche `mark_orders_after_collection`)."""
    with session_factory() as s:
        _order(s, "o-1", "BOPRIN-99960", invoice="260770", payment=PaymentStatus.PAID,
               serie=1, cobro_status="pendiente")
        _order(s, "o-2", "BOPRIN-99961", invoice="260771", payment=PaymentStatus.PENDING,
               serie=1, cobro_status="cobrada")
        _order(s, "o-3", "BOPRIN-99962", payment=PaymentStatus.PAID)
        s.commit()
    rows = http.get("/api/erp/orders", headers=auth_headers(http, "user")).json()["items"]
    by_id = {x["id"]: x for x in rows}
    assert by_id["o-1"]["payment_status"] == "paid"
    assert by_id["o-1"]["factusol_cobro_status"] == "pendiente"
    assert by_id["o-2"]["payment_status"] == "pending"
    assert by_id["o-2"]["factusol_cobro_status"] == "cobrada"
    assert by_id["o-3"]["factusol_cobro_status"] is None
    assert by_id["o-3"]["factusol_invoice_number"] is None

    with session_factory() as s:
        updated = mark_orders_after_collection(
            s, serie=1, codigo=260770,
            result={"registered": True, "status": "registered", "importe": 30.0,
                    "total": 30.0, "linlco": 1, "contrapartida": "6",
                    "estfac_marked": True, "cobros": 0},
        )
        s.commit()
        assert [o.id for o in updated] == ["o-1"]
        assert s.get(Order, "o-1").factusol_cobro_status == "cobrada"
        # Homónimo de otra serie no se toca.
        assert s.get(Order, "o-2").factusol_cobro_status == "cobrada"


def test_resolver_clave_factura() -> None:
    assert parse_invoice_number("5-260086") == (5, 260086)
    assert parse_invoice_number("260086") == (None, 260086)
    assert parse_invoice_number("") == (None, None)
    assert parse_invoice_number("x") == (None, None)


def test_resolver_clave_sin_factusol(session_factory) -> None:
    """Con la serie ya guardada no hace falta consultar F_FAC."""
    with session_factory() as s:
        o = _order(s, "o-k", "BOPRIN-99970", invoice="260780", serie=5)
        s.commit()
        key = resolve_invoice_key(s, o, fac_rows=[])
        assert key["serie"] == 5 and key["codigo"] == 260780
        assert key["numero"] == "5-260780" and key["row"] is None
