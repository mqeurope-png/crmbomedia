"""BoHub ERP Fase C PR C-1 — mapper + servicio FACTUSOL.

El cliente FACTUSOL se sustituye por un fake en memoria (registra escrituras
y borrados) — sin red. La BD es SQLite in-memory.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.erp.models import InvoiceStatus, Order, OrderLine
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.mapper import (
    company_to_factusol_client,
    order_to_factusol_invoice,
)
from app.integrations.factusol.service import (
    emit_invoice,
    ensure_customer_in_factusol,
)
from app.models.crm import Company


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
    """Cliente FACTUSOL simulado: registra writes/deletes en memoria."""

    def __init__(self, *, existing_by_cif=None, all_codclis=None, fail_on_line=None):
        self.default_ejercicio = "2026"
        self.writes: list[tuple[str, dict]] = []
        self.deletes: list[tuple[str, str]] = []
        self._existing_by_cif = existing_by_cif or {}
        self._all_codclis = all_codclis or []
        self._fail_on_line = fail_on_line
        self._line_calls = 0

    def load_table(self, tabla, *, filtro="", campos=None, numero_registros=None, ejercicio=None):
        if tabla == "F_CLI" and filtro.startswith("CIFCLI="):
            cif = filtro.split("=", 1)[1].strip("'")
            codcli = self._existing_by_cif.get(cif)
            return [{"CODCLI": codcli}] if codcli else []
        if tabla == "F_CLI":
            return [{"CODCLI": c} for c in self._all_codclis]
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


def _company(s: Session, **over) -> Company:
    base = {"name": "Rotulación Pérez SL", "tax_id": "B61234567"}
    base.update(over)
    c = Company(**base)
    s.add(c)
    s.commit()
    return c


def _order(s: Session, company_id, *, number="MAN-0007", n_lines=3) -> Order:
    o = Order(order_number=number, company_id=company_id, total_amount=300)
    s.add(o)
    s.flush()
    for i in range(n_lines):
        s.add(OrderLine(
            order_id=o.id, position=i, product_sku=f"SKU-{i}",
            product_codart=f"ART{i}", description=f"Art {i}",
            quantity=1, unit_price=100, tax_rate=21, line_total=100,
        ))
    s.commit()
    return o


# --- mapper (puro) ----------------------------------------------------------


def test_company_mapper_builds_f_cli_payload(session_factory):
    with session_factory() as s:
        c = _company(s, name="Acme SL", tax_id="B12345678", city="Barcelona",
                     postal_code="08001", website="https://acme.example")
        payload = company_to_factusol_client(c, "60001")
    assert payload["CODCLI"] == "60001"
    assert payload["PCOCLI"] == "Acme SL"
    assert payload["CIFCLI"] == "B12345678"
    assert payload["POBCLI"] == "Barcelona"
    assert payload["CPOCLI"] == "08001"
    assert payload["WEBCLI"] == "https://acme.example"


def test_invoice_mapper_builds_header_and_lines(session_factory):
    with session_factory() as s:
        c = _company(s)
        o = _order(s, c.id, number="BOPRIN-1042", n_lines=2)
        o = s.get(Order, o.id)
        cabecera, lineas = order_to_factusol_invoice(o, "60001", "2026")
    assert cabecera["CODFAC"] == "1042"          # dígitos del nº de pedido
    assert cabecera["CLIFAC"] == "60001"
    assert cabecera["EJEFAC"] == "2026"
    assert cabecera["REFFAC"] == "BOPRIN-1042"
    assert len(lineas) == 2
    assert lineas[0]["CODLFA"] == "1042" and lineas[0]["POSLFA"] == 1
    assert lineas[0]["ARTLFA"] == "ART0"


# --- ensure_customer_in_factusol --------------------------------------------


def test_ensure_customer_creates_new_when_no_link_no_cif_match(session_factory):
    with session_factory() as s:
        c = _company(s)
        cid = c.id
        client = FakeFactusol(all_codclis=[])  # F_CLI vacío → base 60000
        codcli = ensure_customer_in_factusol(s, cid, client)
    assert codcli == "60000"
    assert client.writes[0][0] == "F_CLI"
    assert client.writes[0][1]["CODCLI"] == "60000"
    with session_factory() as s:
        assert s.get(Company, cid).factusol_company_id == "60000"


def test_ensure_customer_reuses_existing_link(session_factory):
    with session_factory() as s:
        c = _company(s, factusol_company_id="12345")
        cid = c.id
        client = FakeFactusol()
        codcli = ensure_customer_in_factusol(s, cid, client)
    assert codcli == "12345"
    assert client.writes == []            # no crea nada


def test_ensure_customer_links_by_cif_without_duplicating(session_factory):
    with session_factory() as s:
        c = _company(s, tax_id="B99999999")
        cid = c.id
        client = FakeFactusol(existing_by_cif={"B99999999": "77777"})
        codcli = ensure_customer_in_factusol(s, cid, client)
    assert codcli == "77777"
    assert client.writes == []            # vincula sin crear cliente nuevo
    with session_factory() as s:
        assert s.get(Company, cid).factusol_company_id == "77777"


def test_ensure_customer_next_codcli_is_max_plus_one(session_factory):
    with session_factory() as s:
        c = _company(s, tax_id=None)
        cid = c.id
        client = FakeFactusol(all_codclis=["100", "70123", "abc"])
        codcli = ensure_customer_in_factusol(s, cid, client)
    assert codcli == "70124"              # max numérico (70123) + 1


# --- emit_invoice -----------------------------------------------------------


def test_emit_invoice_writes_header_lines_and_updates_order(session_factory):
    with session_factory() as s:
        c = _company(s, factusol_company_id="55555")
        o = _order(s, c.id, n_lines=3)
        oid = o.id
        client = FakeFactusol()
        result = emit_invoice(s, oid, client)
    tablas = [t for t, _ in client.writes]
    assert tablas.count("F_FAC") == 1
    assert tablas.count("F_LFA") == 3
    assert result["lines"] == 3
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.invoice_status == InvoiceStatus.INVOICED_BY_ERP
        assert o.factusol_invoice_number == result["factusol_invoice_number"]


def test_emit_invoice_rolls_back_on_line_failure(session_factory):
    with session_factory() as s:
        c = _company(s, factusol_company_id="55555")
        o = _order(s, c.id, n_lines=3)
        oid = o.id
        client = FakeFactusol(fail_on_line=2)   # falla en la 2ª línea
        with pytest.raises(FactusolError):
            emit_invoice(s, oid, client)
    # Compensación: se borran líneas + cabecera (no queda factura a medias).
    assert ("F_LFA", "CODLFA='7'") in client.deletes or any(
        t == "F_LFA" for t, _ in client.deletes)
    assert any(t == "F_FAC" for t, _ in client.deletes)
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.invoice_status != InvoiceStatus.INVOICED_BY_ERP
        assert o.factusol_invoice_number is None


def test_emit_invoice_requires_company(session_factory):
    with session_factory() as s:
        o = Order(order_number="MAN-NOCO", total_amount=10)
        s.add(o)
        s.commit()
        oid = o.id
        with pytest.raises(FactusolError):
            emit_invoice(s, oid, FakeFactusol())
