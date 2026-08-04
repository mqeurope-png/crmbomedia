"""BoHub ERP Fase C · C-2-fix2 — mapper + servicio FACTUSOL (F_PCL → F_FAC,
detección de factura/albarán existente, auto-vinculación).

El cliente FACTUSOL se sustituye por un fake en memoria — sin red. La BD es
SQLite in-memory.
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.erp.models import InvoiceStatus, Order
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.mapper import (
    FacturaOptions,
    lpc_row_to_lfa_payload,
    pcl_row_to_fac_payload,
)
from app.integrations.factusol.service import (
    check_factusol_status,
    emit_invoice,
    find_pcl_by_order,
    get_and_link_factusol_status,
    next_codfac,
)


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


class FakeFactusol:
    """Cliente FACTUSOL simulado: sirve F_PCL/F_LPC/F_FAC/F_ALB y registra
    escrituras. Distingue las dos consultas a F_FAC: por REFFAC (¿existe ya la
    factura?) vs. ORDER BY CODFAC DESC (siguiente número)."""

    def __init__(self, *, pcl_row=None, lpc_rows=None, f_fac_last=None,
                 fac_by_ref=None, alb_by_ref=None, fail_on_line=None):
        self.default_ejercicio = "2026"
        self.writes: list[tuple[str, dict]] = []
        self.deletes: list[tuple[str, str]] = []
        self.pcl_filters: list[str] = []
        self.fac_filters: list[str] = []
        self.alb_filters: list[str] = []
        self._pcl_row = pcl_row
        self._lpc_rows = lpc_rows or []
        self._f_fac_last = f_fac_last
        self._fac_by_ref = fac_by_ref or []
        self._alb_by_ref = alb_by_ref or []
        self._fail_on_line = fail_on_line
        self._line_calls = 0

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla == "F_FAC":
            if "REFFAC" in filtro:
                self.fac_filters.append(filtro)
                return list(self._fac_by_ref)
            # next_codfac: 1=1 ORDER BY CODFAC DESC
            return [{"CODFAC": self._f_fac_last}] if self._f_fac_last is not None else []
        if tabla == "F_ALB":
            self.alb_filters.append(filtro)
            return list(self._alb_by_ref)
        if tabla == "F_PCL":
            self.pcl_filters.append(filtro)
            return [self._pcl_row] if self._pcl_row is not None else []
        if tabla == "F_LPC":
            return list(self._lpc_rows)
        return []

    def write_record(self, tabla, data, *, ejercicio=None):
        if tabla == "F_LFA":
            self._line_calls += 1
            if self._fail_on_line and self._line_calls == self._fail_on_line:
                raise FactusolError("line write failed", status=500)
        self.writes.append((tabla, data))
        return {"ok": True}

    def delete_records(self, tabla, filtro, *, ejercicio=None):
        self.deletes.append((tabla, filtro))
        return {"ok": True}


def _pcl_row(**over) -> dict:
    base = {
        "CODPCL": 2765, "REFPCL": "BOP-099866", "CLIPCL": 2458,
        "CNOPCL": "DUPLICODER, S.L.", "TOTPCL": 186.34, "FECPCL": "2026-08-01",
        "NET1PCL": 100.0, "PIVA1PCL": 21.0, "IIVA1PCL": 21.0,
        "NET2PCL": 40.0, "PIVA2PCL": 10.0, "IIVA2PCL": 4.0,
        "NET3PCL": 10.0, "PIVA3PCL": 4.0, "IIVA3PCL": 0.4,
        # columnas de pedido que NO deben copiarse:
        "ESTPCL": "S", "USUPCL": "admin", "HORPCL": "10:00",
    }
    base.update(over)
    return base


def _order(s: Session, *, number="BOPRIN-99866", invoice_status=None) -> str:
    o = Order(order_number=number, total_amount=186.34)
    if invoice_status is not None:
        o.invoice_status = invoice_status
    s.add(o)
    s.commit()
    return o.id


# --- next_codfac ------------------------------------------------------------


def test_next_codfac_empty_f_fac_returns_1():
    assert next_codfac(FakeFactusol(f_fac_last=None), "2026") == "1"


def test_next_codfac_is_last_plus_one():
    assert next_codfac(FakeFactusol(f_fac_last=526066), "2026") == "526067"


# --- find_pcl_by_order ------------------------------------------------------


def test_find_pcl_by_order_hit_composes_refpcl():
    order = Order(order_number="BOPRIN-99866")
    client = FakeFactusol(pcl_row=_pcl_row())
    row = find_pcl_by_order(client, order, "2026")
    assert row is not None and row["CODPCL"] == 2765
    # REFPCL compuesto: prefijo BOP + nº Woo con padding 6.
    assert client.pcl_filters == ["REFPCL='BOP-099866'"]


def test_find_pcl_by_order_miss_returns_none():
    order = Order(order_number="BOPRIN-99866")
    assert find_pcl_by_order(FakeFactusol(pcl_row=None), order, "2026") is None


# --- check_factusol_status --------------------------------------------------


def test_check_factusol_status_detects_existing_factura():
    order = Order(order_number="BOPRIN-99866")
    client = FakeFactusol(
        fac_by_ref=[{"CODFAC": 260695, "REFFAC": "BOP-099866", "TOTFAC": 186.34}],
    )
    info = check_factusol_status(client, order, "2026")
    assert info["has_factura"] is True
    assert info["factura"]["CODFAC"] == 260695
    assert info["has_albaran"] is False
    assert info["ref"] == "BOP-099866"
    # Filtra por la referencia común.
    assert client.fac_filters == ["REFFAC='BOP-099866'"]
    assert client.alb_filters == ["REFALB='BOP-099866'"]


def test_check_factusol_status_detects_albaran_only():
    order = Order(order_number="BOPRIN-99866")
    client = FakeFactusol(alb_by_ref=[{"CODALB": 5001, "REFALB": "BOP-099866"}])
    info = check_factusol_status(client, order, "2026")
    assert info["has_factura"] is False
    assert info["has_albaran"] is True
    assert info["albaran"]["CODALB"] == 5001


def test_check_factusol_status_neither():
    order = Order(order_number="BOPRIN-99866")
    info = check_factusol_status(FakeFactusol(), order, "2026")
    assert info["has_factura"] is False and info["has_albaran"] is False


# --- get_and_link_factusol_status (auto-vinculación) ------------------------


def test_get_and_link_auto_links_existing_factura(session_factory):
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(
            fac_by_ref=[{"CODFAC": 260695, "REFFAC": "BOP-099866"}],
        )
        order = s.get(Order, oid)
        result = get_and_link_factusol_status(s, order, client, "2026")
        assert result == {"status": "invoiced", "codfac": "260695",
                          "ref": "BOP-099866", "auto_linked": True}
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.invoice_status == InvoiceStatus.INVOICED_BY_ERP
        assert o.factusol_invoice_number == "260695"
        hist = [h for h in o.status_history
                if h.to_status == InvoiceStatus.INVOICED_BY_ERP.value]
        assert len(hist) == 1
        meta = json.loads(hist[0].metadata_json)
        assert meta["source"] == "auto_linked_from_factusol"
        assert meta["factusol_codfac"] == "260695"


def test_get_and_link_reports_albaran(session_factory):
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(alb_by_ref=[{"CODALB": 5001, "REFALB": "BOP-099866"}])
        order = s.get(Order, oid)
        result = get_and_link_factusol_status(s, order, client, "2026")
        assert result["status"] == "albaran"
        assert result["albaran_codigo"] == "5001"
    with session_factory() as s:
        # Un albarán NO marca el pedido como facturado.
        assert s.get(Order, oid).factusol_invoice_number is None


def test_get_and_link_pending_when_nothing(session_factory):
    with session_factory() as s:
        oid = _order(s)
        order = s.get(Order, oid)
        result = get_and_link_factusol_status(s, order, FakeFactusol(), "2026")
        assert result["status"] == "pending" and result["ref"] == "BOP-099866"


# --- mapper: F_PCL → F_FAC / F_LPC → F_LFA -----------------------------------


def test_pcl_row_to_fac_payload_maps_all_iva_bands_and_injects():
    fac = pcl_row_to_fac_payload(
        _pcl_row(), "526067", "2026", fecha_emision="2026-08-04",
    )
    # Bandas de IVA copiadas por sufijo (*PCL → *FAC).
    assert fac["NET1FAC"] == 100.0 and fac["PIVA1FAC"] == 21.0 and fac["IIVA1FAC"] == 21.0
    assert fac["NET2FAC"] == 40.0 and fac["IIVA2FAC"] == 4.0
    assert fac["NET3FAC"] == 10.0 and fac["IIVA3FAC"] == 0.4
    assert fac["CLIFAC"] == 2458 and fac["TOTFAC"] == 186.34
    assert fac["REFFAC"] == "BOP-099866"          # REFPCL → REFFAC (el enlace)
    # Inyecciones.
    assert fac["CODFAC"] == "526067"
    assert fac["EJEFAC"] == "2026"
    assert fac["TIPFAC"] == "1"                    # factura ordinaria (string)
    assert fac["FECFAC"] == "2026-08-04"          # fecha de emisión, no la del pedido
    # C-2-fix2: NO se inyecta PEDFAC (la factura real lo tiene vacío).
    assert "PEDFAC" not in fac
    # Columnas de estado del pedido NO copiadas.
    assert "ESTFAC" not in fac and "USUFAC" not in fac and "HORFAC" not in fac
    # El CODPCL NO se copia como CODFAC (se inyecta el nuevo).
    assert fac["CODFAC"] != 2765


def test_pcl_row_to_fac_payload_applies_operator_options():
    opts = FacturaOptions(tipfac="4", serfac="A", fecfac="2026-07-29",
                          fopfac="03", comfac="Pago a 30 días")
    fac = pcl_row_to_fac_payload(
        _pcl_row(), "526067", "2026", fecha_emision="2026-08-04", options=opts,
    )
    assert fac["TIPFAC"] == "4"
    assert fac["SERFAC"] == "A"
    assert fac["FECFAC"] == "2026-07-29"          # la opción gana a la fecha de hoy
    assert fac["FOPFAC"] == "03"
    assert fac["COMFAC"] == "Pago a 30 días"


def test_pcl_row_to_fac_payload_defaults_omit_optional_columns():
    fac = pcl_row_to_fac_payload(
        _pcl_row(), "1", "2026", fecha_emision="2026-08-04",
    )
    # Sin opciones no se inyectan serie / forma de pago / observaciones.
    assert "SERFAC" not in fac and "FOPFAC" not in fac and "COMFAC" not in fac


def test_lpc_row_to_lfa_payload_copies_line():
    lpc = {"ARTLPC": "ART-1", "DESLPC": "Producto 1", "CANLPC": 2.0,
           "PRELPC": 50.0, "IVALPC": 21.0, "TOTLPC": 100.0, "CODLPC": 2765}
    lfa = lpc_row_to_lfa_payload(lpc, "526067", 1, "2026")
    assert lfa["ARTLFA"] == "ART-1" and lfa["DESLFA"] == "Producto 1"
    assert lfa["CANLFA"] == 2.0 and lfa["TOTLFA"] == 100.0
    assert lfa["CODLFA"] == "526067" and lfa["POSLFA"] == 1 and lfa["EJELFA"] == "2026"


# --- emit_invoice -----------------------------------------------------------


def test_emit_invoice_when_pcl_missing_raises_clear_error(session_factory):
    with session_factory() as s:
        oid = _order(s, number="BOPRIN-99999")
        client = FakeFactusol(pcl_row=None)   # el pedido no está en F_PCL
        with pytest.raises(FactusolError, match="aún no está en FACTUSOL"):
            emit_invoice(s, oid, client)


def test_emit_invoice_end_to_end_success(session_factory):
    from app.models.crm import SyncLog  # noqa: PLC0415

    lpc_rows = [
        {"ARTLPC": "A1", "DESLPC": "L1", "CANLPC": 1, "TOTLPC": 100, "CODLPC": 2765},
        {"ARTLPC": "A2", "DESLPC": "L2", "CANLPC": 2, "TOTLPC": 86.34, "CODLPC": 2765},
    ]
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(pcl_row=_pcl_row(), lpc_rows=lpc_rows, f_fac_last=526066)
        result = emit_invoice(s, oid, client)
    tablas = [t for t, _ in client.writes]
    assert tablas.count("F_FAC") == 1 and tablas.count("F_LFA") == 2
    assert result == {"codfac": "526067", "codpcl": "2765",
                      "ejercicio": "2026", "lines": 2}
    cabecera = next(rec for t, rec in client.writes if t == "F_FAC")
    assert cabecera["REFFAC"] == "BOP-099866" and cabecera["CODFAC"] == "526067"
    assert cabecera["TIPFAC"] == "1" and "PEDFAC" not in cabecera
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.invoice_status == InvoiceStatus.INVOICED_BY_ERP
        assert o.factusol_invoice_number == "526067"
        hist = [h for h in o.status_history
                if h.to_status == InvoiceStatus.INVOICED_BY_ERP.value]
        assert len(hist) == 1
        meta = json.loads(hist[0].metadata_json)
        assert meta["factusol_codfac"] == "526067" and meta["factusol_codpcl"] == "2765"
        sync = s.query(SyncLog).filter(
            SyncLog.operation == "factusol_emit_invoice").one()
        assert sync.status == "success"


def test_emit_invoice_recheck_auto_links_existing_factura(session_factory):
    """Anti-duplicado: si la factura ya existe en FACTUSOL (creada a mano o por
    carrera), emit la auto-vincula en vez de crear un duplicado."""
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(
            pcl_row=_pcl_row(),
            fac_by_ref=[{"CODFAC": 260695, "REFFAC": "BOP-099866"}],
            f_fac_last=526066,
        )
        result = emit_invoice(s, oid, client)
    # NO se escribió ninguna factura nueva.
    assert client.writes == []
    assert result["already_existed"] is True and result["codfac"] == "260695"
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.invoice_status == InvoiceStatus.INVOICED_BY_ERP
        assert o.factusol_invoice_number == "260695"
        meta = json.loads(o.status_history[0].metadata_json)
        assert meta["source"] == "auto_linked_from_factusol"


def test_emit_invoice_threads_options_to_header(session_factory):
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(pcl_row=_pcl_row(), lpc_rows=[], f_fac_last=100)
        emit_invoice(s, oid, client,
                     options=FacturaOptions(serfac="A", fopfac="03",
                                            comfac="obs"))
    cabecera = next(rec for t, rec in client.writes if t == "F_FAC")
    assert cabecera["SERFAC"] == "A" and cabecera["FOPFAC"] == "03"
    assert cabecera["COMFAC"] == "obs" and cabecera["TIPFAC"] == "1"


def test_emit_invoice_compensation_on_line_write_failure(session_factory):
    lpc_rows = [{"ARTLPC": "A1", "CODLPC": 2765}, {"ARTLPC": "A2", "CODLPC": 2765}]
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(pcl_row=_pcl_row(), lpc_rows=lpc_rows,
                              f_fac_last=100, fail_on_line=2)
        with pytest.raises(FactusolError):
            emit_invoice(s, oid, client)
    # Compensación: borra líneas + cabecera.
    assert any(t == "F_LFA" for t, _ in client.deletes)
    assert any(t == "F_FAC" for t, _ in client.deletes)
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.invoice_status != InvoiceStatus.INVOICED_BY_ERP
        assert o.factusol_invoice_number is None


def test_emit_invoice_rejects_already_invoiced(session_factory):
    with session_factory() as s:
        oid = _order(s, invoice_status=InvoiceStatus.INVOICED_BY_ERP.value)
        with pytest.raises(FactusolError, match="ya tiene factura"):
            emit_invoice(s, oid, FakeFactusol(pcl_row=_pcl_row()))
