"""BoHub ERP Fase A PR 5 — Cola SAT: queue, report-exception, packing-info,
attach-document (DocumentStorage local) + selección de backend de storage.
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import ErpException, ExceptionStatus, Order, OrderLine
from app.erp.storage import (
    HiDriveDocumentStorage,
    LocalDocumentStorage,
    get_document_storage,
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
def client(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _mk_order(s: Session, *, number="MAN-0001", prep="in_queue",
              transport="not_shipped") -> str:
    o = Order(order_number=number, preparation_status=prep, payment_status="paid",
              transport_status=transport)
    s.add(o)
    s.flush()
    s.add(OrderLine(order_id=o.id, product_sku="SKU-A", product_codart="A1",
                    description="Art A", quantity=1, unit_price=10, line_total=10))
    s.commit()
    return o.id


# --- cola SAT ----------------------------------------------------------------


def test_sat_queue_priorities_blocked_then_preparing_then_in_queue(
    client, session_factory
):
    with session_factory() as s:
        _mk_order(s, number="Q-INQUEUE", prep="in_queue")
        _mk_order(s, number="Q-PREP", prep="preparing")
        _mk_order(s, number="Q-BLOCKED", prep="blocked")
        _mk_order(s, number="Q-PACKED", prep="packed")  # va a «listos para envío»
    r = client.get("/api/erp/sat/queue", headers=auth_headers(client, "sat"))
    assert r.status_code == 200
    nums = [i["order_number"] for i in r.json()["preparing"]]
    assert nums == ["Q-BLOCKED", "Q-PREP", "Q-INQUEUE"]  # packed no está en «por embalar»


def test_erp_sat_queue_returns_customer_name(client, session_factory):
    """D-2: las cards del taller muestran el cliente, no solo el número."""
    from app.models.crm import Company, Contact  # noqa: PLC0415

    with session_factory() as s:
        comp = Company(name="Duplicoder SL")
        cont = Contact(first_name="Ana", last_name="Pi", email="ana@example.com")
        s.add_all([comp, cont])
        s.commit()
        comp_id, cont_id = comp.id, cont.id
        o1 = Order(order_number="Q-EMPRESA", preparation_status="preparing",
                   payment_status="paid", company_id=comp_id)
        o2 = Order(order_number="Q-CONTACTO", preparation_status="packed",
                   payment_status="paid", contact_id=cont_id)
        s.add_all([o1, o2])
        s.commit()
    body = client.get("/api/erp/sat/queue",
                      headers=auth_headers(client, "sat")).json()
    prep = {i["order_number"]: i for i in body["preparing"]}
    ready = {i["order_number"]: i for i in body["ready_for_pickup"]}
    assert prep["Q-EMPRESA"]["company_name"] == "Duplicoder SL"
    assert ready["Q-CONTACTO"]["contact_name"] == "Ana Pi"


def test_sat_queue_returns_two_sections(client, session_factory):
    """D-1-fix1: preparing (por embalar) + ready_for_pickup (packed sin salir)."""
    with session_factory() as s:
        _mk_order(s, number="PREP-1", prep="preparing")
        _mk_order(s, number="READY-1", prep="packed")            # not_shipped → listo
        _mk_order(s, number="GONE-1", prep="packed", transport="in_transit")  # ya salió
    r = client.get("/api/erp/sat/queue", headers=auth_headers(client, "sat"))
    body = r.json()
    assert [i["order_number"] for i in body["preparing"]] == ["PREP-1"]
    assert [i["order_number"] for i in body["ready_for_pickup"]] == ["READY-1"]
    # El item de listos lleva el estado de transporte para decidir «recogido».
    assert body["ready_for_pickup"][0]["transport_status"] == "not_shipped"


def test_sat_queue_visible_to_sat_role(client, session_factory):
    with session_factory() as s:
        _mk_order(s)
    assert client.get("/api/erp/sat/queue",
                      headers=auth_headers(client, "sat")).status_code == 200
    assert client.get("/api/erp/sat/queue",
                      headers=auth_headers(client, "viewer")).status_code == 403


# --- reportar excepción ------------------------------------------------------


def test_report_exception_creates_row_and_blocks_preparation(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, prep="preparing")
    r = client.post(
        f"/api/erp/orders/{oid}/report-exception",
        json={"type": "sat_issue", "description": "falta un tornillo"},
        headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["preparation_status"] == "blocked"
    with session_factory() as s:
        exc = s.scalar(select(ErpException).where(ErpException.order_id == oid))
        assert exc.type.value == "sat_issue"
        assert exc.status == ExceptionStatus.OPEN
        assert json.loads(exc.metadata_json)["description"] == "falta un tornillo"
        o = s.scalar(select(Order).where(Order.id == oid))
        assert o.preparation_status == "blocked"


def test_report_exception_validates_type_and_subtype(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s)
    bad_type = client.post(
        f"/api/erp/orders/{oid}/report-exception",
        json={"type": "no_existe"}, headers=auth_headers(client, "sat"),
    )
    assert bad_type.status_code == 400
    bad_sub = client.post(
        f"/api/erp/orders/{oid}/report-exception",
        json={"type": "stock_shortage", "subtype": "inventado"},
        headers=auth_headers(client, "sat"),
    )
    assert bad_sub.status_code == 400
    ok = client.post(
        f"/api/erp/orders/{oid}/report-exception",
        json={"type": "stock_shortage", "subtype": "eta_set",
              "metadata": {"eta_date": "2026-08-20", "provider": "MBO"}},
        headers=auth_headers(client, "sat"),
    )
    assert ok.status_code == 201


def test_report_exception_on_packed_order_records_without_transition(
    client, session_factory
):
    with session_factory() as s:
        oid = _mk_order(s, prep="packed")
    r = client.post(
        f"/api/erp/orders/{oid}/report-exception",
        json={"type": "material_defective"}, headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 201
    assert r.json()["preparation_status"] == "packed"  # no fuerza transición inválida


# --- packing info ------------------------------------------------------------


def test_packing_info_stored_in_packing_json(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s)
    r = client.post(
        f"/api/erp/orders/{oid}/packing-info",
        json={"weight_kg": 12.5, "dimensions_cm": "60x40x30", "packages": 2},
        headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 200
    assert r.json()["packing"] == {
        "weight_kg": 12.5, "dimensions_cm": "60x40x30", "packages": 2,
    }
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.id == oid))
        assert json.loads(o.packing_json)["weight_kg"] == 12.5


# --- adjuntos (DocumentStorage local) ---------------------------------------


def test_attach_document_saves_to_local_storage_and_records_ref(
    client, session_factory, tmp_path, monkeypatch
):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "erp_uploads_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "hidrive_webdav_url", "")
    with session_factory() as s:
        oid = _mk_order(s)
    r = client.post(
        f"/api/erp/orders/{oid}/attach-document",
        files={"file": ("foto.jpg", b"\xff\xd8\xff binary", "image/jpeg")},
        headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 201, r.text
    doc = r.json()["document"]
    assert doc["backend"] == "local"
    assert doc["filename"] == "foto.jpg"
    assert doc["size_bytes"] > 0
    # El archivo existe en disco y la ref queda en packing_json.
    saved = list(tmp_path.glob(f"{oid}/*_foto.jpg"))
    assert len(saved) == 1
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.id == oid))
        docs = json.loads(o.packing_json)["documents"]
        assert len(docs) == 1 and docs[0]["storage_key"].endswith("foto.jpg")


def test_attach_document_rejects_empty_file(client, session_factory, tmp_path, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "erp_uploads_dir", str(tmp_path))
    with session_factory() as s:
        oid = _mk_order(s)
    r = client.post(
        f"/api/erp/orders/{oid}/attach-document",
        files={"file": ("vacio.txt", b"", "text/plain")},
        headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 400


# --- selección de backend de storage ----------------------------------------


def test_get_document_storage_picks_local_without_credentials(monkeypatch):
    from app.core.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "hidrive_webdav_url", "")
    monkeypatch.setattr(s, "hidrive_user", "")
    monkeypatch.setattr(s, "hidrive_password", "")
    assert isinstance(get_document_storage(), LocalDocumentStorage)


def test_get_document_storage_picks_hidrive_with_credentials(monkeypatch):
    from app.core.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "hidrive_webdav_url", "https://webdav.hidrive.strato.com")
    monkeypatch.setattr(s, "hidrive_user", "bomedia")
    monkeypatch.setattr(s, "hidrive_password", "secret")
    assert isinstance(get_document_storage(), HiDriveDocumentStorage)


def test_local_storage_writes_file(tmp_path):
    store = LocalDocumentStorage(str(tmp_path))
    doc = store.save(order_id="o1", filename="../evil name.png",
                     content_type="image/png", data=b"data")
    assert doc.backend == "local"
    assert "evil_name.png" in doc.filename  # nombre saneado
    assert (tmp_path / doc.storage_key).read_bytes() == b"data"
