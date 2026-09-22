"""BoHub ERP Fase D · PR D-1 — endpoints de expedición (bultos + ficheros).

Storage local en tmp_path (monkeypatch); el cliente WooCommerce se mockea.
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

import app.erp.api.shipping as shipping_api
import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import (
    Order,
    OrderLine,
    OrderStatusHistory,
    ShipmentFile,
    ShipmentPackage,
)
from app.integrations.woocommerce.client import WooError
from app.main import app
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationAccount
from app.storage.local import LocalShippingStorage
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
def client(session_factory, tmp_path) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    storage = LocalShippingStorage(base_dir=str(tmp_path))
    with patch.object(shipping_api, "get_shipping_storage", lambda: storage):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


def _mk_order(s: Session, *, number="MAN-0001", prep="preparing",
              source="manual", store_id=None, external_id=None,
              transport="not_shipped") -> str:
    o = Order(order_number=number, preparation_status=prep, payment_status="paid",
              transport_status=transport,
              external_source=source, store_id=store_id, external_id=external_id)
    s.add(o)
    s.flush()
    s.add(OrderLine(order_id=o.id, product_sku="SKU-A", product_codart="A1",
                    description="Art A", quantity=1, unit_price=10, line_total=10))
    s.commit()
    return o.id


def _pkg(**over):
    base = {"weight_kg": 2.5, "height_cm": 30, "width_cm": 20, "depth_cm": 10}
    base.update(over)
    return base


# --- bultos -----------------------------------------------------------------


def test_package_crud_creates_lists_and_replaces(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s)
    # Crea 2 bultos.
    r = client.post(f"/api/erp/orders/{oid}/packages",
                    json=[_pkg(), _pkg(weight_kg=1.0)],
                    headers=auth_headers(client, "sat"))
    assert r.status_code == 200, r.text
    assert [p["position"] for p in r.json()["packages"]] == [1, 2]
    # GET los devuelve.
    g = client.get(f"/api/erp/orders/{oid}/packages",
                   headers=auth_headers(client, "sat"))
    assert len(g.json()["items"]) == 2
    # Reemplaza por 1 solo (idempotente).
    client.post(f"/api/erp/orders/{oid}/packages", json=[_pkg()],
                headers=auth_headers(client, "sat"))
    with session_factory() as s:
        rows = list(s.scalars(select(ShipmentPackage).where(
            ShipmentPackage.order_id == oid)))
        assert len(rows) == 1


def test_package_incomplete_rejected_400(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s)
    r = client.post(f"/api/erp/orders/{oid}/packages",
                    json=[{"weight_kg": 2.0, "height_cm": 10, "width_cm": 10}],
                    headers=auth_headers(client, "sat"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "package_incomplete"


def test_package_zero_dimension_rejected_400(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s)
    r = client.post(f"/api/erp/orders/{oid}/packages",
                    json=[_pkg(weight_kg=0)],
                    headers=auth_headers(client, "sat"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "package_invalid"


# --- transición a packed ----------------------------------------------------


def test_transition_packed_requires_packages(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, prep="preparing")
    # Sin bultos → 400.
    r = client.post(f"/api/erp/orders/{oid}/transition/preparation/packed",
                    headers=auth_headers(client, "sat"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "no_packages"
    # Con bultos → 200 y estado packed.
    client.post(f"/api/erp/orders/{oid}/packages", json=[_pkg()],
                headers=auth_headers(client, "sat"))
    r = client.post(f"/api/erp/orders/{oid}/transition/preparation/packed",
                    headers=auth_headers(client, "sat"))
    assert r.status_code == 200, r.text
    assert r.json()["preparation_status"] == "packed"


def test_generic_transition_to_packed_also_blocked_without_packages(
    client, session_factory
):
    """El guard del engine protege también el endpoint genérico de transición."""
    with session_factory() as s:
        oid = _mk_order(s, prep="preparing")
    r = client.post(f"/api/erp/orders/{oid}/transitions",
                    json={"domain": "preparation", "to_status": "packed"},
                    headers=auth_headers(client, "sat"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "guard_failed"


# --- ficheros de expedición -------------------------------------------------


def _upload(client, oid, kind, *, name="doc.pdf", content=b"%PDF-1.4 x",
            mime="application/pdf", role="sat"):
    return client.post(
        f"/api/erp/orders/{oid}/shipping-files",
        data={"kind": kind},
        files={"file": (name, content, mime)},
        headers=auth_headers(client, role),
    )


def test_shipping_files_upload_manual_and_list(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s)
    r = _upload(client, oid, "etiqueta")
    assert r.status_code == 201, r.text
    body = r.json()["file"]
    assert body["kind"] == "etiqueta" and body["source"] == "manual_upload"
    assert body["download_url"].endswith("/download")
    # Listado devuelve el vigente.
    g = client.get(f"/api/erp/orders/{oid}/shipping-files?kind=etiqueta",
                   headers=auth_headers(client, "sat"))
    assert len(g.json()["items"]) == 1


def test_shipping_files_replaces_previous_of_same_kind(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s)
    _upload(client, oid, "etiqueta", name="v1.pdf")
    _upload(client, oid, "etiqueta", name="v2.pdf")
    # Solo 1 vigente; el primero queda con replaced_at.
    g = client.get(f"/api/erp/orders/{oid}/shipping-files?kind=etiqueta",
                   headers=auth_headers(client, "sat"))
    items = g.json()["items"]
    assert len(items) == 1 and items[0]["filename"].endswith("v2.pdf")
    with session_factory() as s:
        rows = list(s.scalars(select(ShipmentFile).where(
            ShipmentFile.order_id == oid)))
        assert len(rows) == 2
        assert sum(1 for r in rows if r.replaced_at is not None) == 1


def test_shipping_file_download_inline(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s)
    up = _upload(client, oid, "albaran", content=b"%PDF-1.4 hello")
    file_id = up.json()["file"]["id"]
    r = client.get(f"/api/erp/orders/{oid}/shipping-files/{file_id}/download",
                   headers=auth_headers(client, "sat"))
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 hello"
    assert r.headers["content-type"].startswith("application/pdf")
    assert "inline" in r.headers["content-disposition"]


def test_shipping_file_upload_rejects_bad_mime(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s)
    r = _upload(client, oid, "etiqueta", name="x.exe",
                content=b"MZ", mime="application/octet-stream")
    assert r.status_code == 415


# --- Lote 2 C: subir la etiqueta ES «Crear envío» ---------------------------


def _transport_of(s: Session, oid: str) -> str:
    v = s.get(Order, oid).transport_status
    return getattr(v, "value", v)


def _history(s: Session, oid: str) -> list[OrderStatusHistory]:
    return list(s.scalars(select(OrderStatusHistory).where(
        OrderStatusHistory.order_id == oid)))


def test_etiqueta_upload_on_packed_order_creates_label(client, session_factory):
    """Pedido embalado + etiqueta subida → `not_shipped → label_created` por
    la máquina de estados: historial con motivo «etiqueta subida» y el
    fichero como evidencia."""
    with session_factory() as s:
        oid = _mk_order(s, prep="packed")
    r = _upload(client, oid, "etiqueta", name="gls.pdf")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["transition_applied"] is True
    assert body["transport_status"] == "label_created"
    assert body["transition_reason"] is None
    assert body["file"]["kind"] == "etiqueta"
    with session_factory() as s:
        assert _transport_of(s, oid) == "label_created"
        rows = _history(s, oid)
        assert len(rows) == 1
        h = rows[0]
        assert getattr(h.domain, "value", h.domain) == "transport"
        assert (h.from_status, h.to_status) == ("not_shipped", "label_created")
        assert h.reason == "etiqueta subida"
        assert h.changed_by_user_id is not None
        meta = json.loads(h.metadata_json)
        assert meta == {"shipment_file_id": body["file"]["id"], "filename": "gls.pdf"}


def test_etiqueta_upload_on_unpacked_order_keeps_file_without_transition(
    client, session_factory
):
    """Sin embalar el guard rechaza el arco: la etiqueta se guarda igual y la
    respuesta dice que el transporte no se movió y por qué."""
    with session_factory() as s:
        oid = _mk_order(s, prep="preparing")
    r = _upload(client, oid, "etiqueta")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["transition_applied"] is False
    assert body["transport_status"] == "not_shipped"
    assert "embalada" in body["transition_reason"]
    g = client.get(f"/api/erp/orders/{oid}/shipping-files?kind=etiqueta",
                   headers=auth_headers(client, "sat"))
    assert len(g.json()["items"]) == 1
    with session_factory() as s:
        assert _transport_of(s, oid) == "not_shipped"
        assert _history(s, oid) == []


def test_etiqueta_upload_forbidden_for_view_only_role(client, session_factory):
    """Roles y permisos: subir la etiqueta exige la capacidad `erp.sat.shipping`
    (SAT/Pedidos/Comercial/Admin). Un rol de solo lectura del ERP (`user`) ya no
    puede subirla → 403, y no queda ningún fichero."""
    with session_factory() as s:
        oid = _mk_order(s, prep="packed")
    r = _upload(client, oid, "etiqueta", role="user")
    assert r.status_code == 403, r.text
    with session_factory() as s:
        assert list(s.scalars(select(ShipmentFile).where(
            ShipmentFile.order_id == oid))) == []
        assert _transport_of(s, oid) == "not_shipped"


def test_etiqueta_upload_when_transition_blocked_keeps_file(client, session_factory):
    """Un rol autorizado a subir (SAT) sobre un pedido que NO está embalado: el
    guard «sin embalar» rechaza «Crear envío», pero el fichero se guarda igual y
    el motivo vuelve al cliente (el estado lo moverá una subida posterior)."""
    with session_factory() as s:
        oid = _mk_order(s, prep="preparing")  # no packed → guard bloquea el arco
    r = _upload(client, oid, "etiqueta", role="sat")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["transition_applied"] is False
    assert body["transport_status"] == "not_shipped"
    assert "embalada" in body["transition_reason"]
    with session_factory() as s:
        assert _transport_of(s, oid) == "not_shipped"
        assert _history(s, oid) == []
        # El fichero SÍ se conservó pese a no aplicarse la transición.
        assert len(list(s.scalars(select(ShipmentFile).where(
            ShipmentFile.order_id == oid)))) == 1


def test_albaran_upload_never_transitions(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, prep="packed")
    r = _upload(client, oid, "albaran")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["transition_applied"] is False
    assert body["transition_reason"] is None
    assert body["transport_status"] == "not_shipped"
    with session_factory() as s:
        assert _transport_of(s, oid) == "not_shipped"
        assert _history(s, oid) == []


def test_etiqueta_replace_after_label_created_does_not_transition_again(
    client, session_factory
):
    """Reemplazar la etiqueta con el envío ya creado no toca el estado ni
    añade historial."""
    with session_factory() as s:
        oid = _mk_order(s, prep="packed", transport="label_created")
    r = _upload(client, oid, "etiqueta", name="v2.pdf")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["transition_applied"] is False
    assert body["transition_reason"] is None
    assert body["transport_status"] == "label_created"
    with session_factory() as s:
        assert _transport_of(s, oid) == "label_created"
        assert _history(s, oid) == []


def test_etiqueta_then_mark_picked_up_reaches_in_transit(client, session_factory):
    """Flujo completo del taller: etiqueta subida (label_created) y después
    «Marcar recogido» (in_transit) — `mark-picked-up` no cambia."""
    with session_factory() as s:
        oid = _mk_order(s, prep="packed")
    assert _upload(client, oid, "etiqueta").json()["transport_status"] == "label_created"
    r = client.post(f"/api/erp/orders/{oid}/mark-picked-up",
                    headers=auth_headers(client, "sat"))
    assert r.status_code == 200, r.text
    assert r.json()["transport_status"] == "in_transit"
    with session_factory() as s:
        arcs = [(h.from_status, h.to_status) for h in _history(s, oid)]
        assert sorted(arcs) == [("label_created", "in_transit"),
                                ("not_shipped", "label_created")]


# --- albarán desde Woo ------------------------------------------------------


def _woo_order(s: Session) -> str:
    acc = IntegrationAccount(system=ExternalSystem.WOOCOMMERCE,
                             account_id="boprint", display_name="BoPrint")
    s.add(acc)
    s.flush()
    return _mk_order(s, number="BOP-1", source="woocommerce",
                     store_id=acc.id, external_id="4567")


def _order_json(woo_id: int) -> dict:
    return {"id": woo_id, "number": "BOP-1", "order_key": "wc_order_abc",
            "shipping": {"first_name": "Ana", "last_name": "Pi",
                         "address_1": "C Aribau 171", "city": "Barcelona"},
            "line_items": [{"sku": "A1", "name": "Art", "quantity": 1}]}


def test_albaran_fetch_from_woo_uses_mu_plugin_when_available(client, session_factory):
    with session_factory() as s:
        oid = _woo_order(s)

    class _FakeWoo:
        def __init__(self, account):
            pass

        def get_packing_slip_pdf(self, woo_id):
            assert woo_id == 4567
            return b"%PDF-1.5 plugin", f"albaran-{woo_id}.pdf"

    with patch("app.integrations.woocommerce.client.WooHTTPClient", _FakeWoo):
        r = client.post(f"/api/erp/orders/{oid}/albaran/fetch-from-woo",
                        headers=auth_headers(client, "sat"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["file"]["source"] == "woo_pdf_plugin"
    assert body["already_present"] is False
    # Idempotente: segunda llamada no re-descarga.
    with patch("app.integrations.woocommerce.client.WooHTTPClient", _FakeWoo):
        r2 = client.post(f"/api/erp/orders/{oid}/albaran/fetch-from-woo",
                         headers=auth_headers(client, "sat"))
    assert r2.json()["already_present"] is True


def test_albaran_fetch_generates_pdf_when_plugin_unavailable(client, session_factory):
    """Si el mu-plugin no entrega el albarán, se genera uno propio (nunca 502)."""
    with session_factory() as s:
        oid = _woo_order(s)

    class _NoPluginWoo:
        def __init__(self, account):
            pass

        def get_packing_slip_pdf(self, woo_id):
            raise WooError("mu-plugin no disponible", status=404)

        def get_order(self, woo_id):
            return _order_json(woo_id)

    with patch("app.integrations.woocommerce.client.WooHTTPClient", _NoPluginWoo):
        r = client.post(f"/api/erp/orders/{oid}/albaran/fetch-from-woo",
                        headers=auth_headers(client, "sat"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["file"]["source"] == "crm_generated_pdf"
    # El fichero generado es un PDF real y descargable.
    dl = client.get(body["file"]["download_url"], headers=auth_headers(client, "sat"))
    assert dl.status_code == 200 and dl.content[:5] == b"%PDF-"


def test_albaran_fetch_502_when_woo_unreachable(client, session_factory):
    with session_factory() as s:
        oid = _woo_order(s)

    class _DeadWoo:
        def __init__(self, account):
            pass

        def get_packing_slip_pdf(self, woo_id):
            raise WooError("mu-plugin KO", status=404)

        def get_order(self, woo_id):
            raise WooError("woo down", status=502)

    with patch("app.integrations.woocommerce.client.WooHTTPClient", _DeadWoo):
        r = client.post(f"/api/erp/orders/{oid}/albaran/fetch-from-woo",
                        headers=auth_headers(client, "sat"))
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "woo_unreachable"


def test_albaran_fetch_from_woo_rejects_non_woo_order(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, source="manual")
    r = client.post(f"/api/erp/orders/{oid}/albaran/fetch-from-woo",
                    headers=auth_headers(client, "sat"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "not_woo_order"


def test_albaran_fetch_accepts_orders_in_any_prep_state(client, session_factory):
    """No hay guard de estado: se puede descargar el albarán en in_queue."""
    class _FakeWoo:
        def __init__(self, account):
            pass

        def get_packing_slip_pdf(self, woo_id):
            return b"%PDF-1.5 x", f"albaran-{woo_id}.pdf"

    for prep in ("in_queue", "preparing", "packed"):
        with session_factory() as s:
            acc = IntegrationAccount(system=ExternalSystem.WOOCOMMERCE,
                                     account_id=f"s-{prep}", display_name="S")
            s.add(acc)
            s.flush()
            oid = _mk_order(s, number=f"BOP-{prep}", source="woocommerce",
                            store_id=acc.id, external_id="99", prep=prep)
        with patch("app.integrations.woocommerce.client.WooHTTPClient", _FakeWoo):
            r = client.post(f"/api/erp/orders/{oid}/albaran/fetch-from-woo",
                            headers=auth_headers(client, "sat"))
        assert r.status_code == 201, f"{prep}: {r.text}"


# --- marcar recogido --------------------------------------------------------


def test_mark_picked_up_transitions_state(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, prep="packed", transport="label_created")
    r = client.post(f"/api/erp/orders/{oid}/mark-picked-up",
                    headers=auth_headers(client, "sat"))
    assert r.status_code == 200, r.text
    assert r.json()["transport_status"] == "in_transit"


def test_mark_picked_up_from_not_shipped_auto_creates_label(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, prep="packed", transport="not_shipped")
    r = client.post(f"/api/erp/orders/{oid}/mark-picked-up",
                    headers=auth_headers(client, "sat"),
                    json={"tracking_number": "GLS-99"})
    assert r.status_code == 200, r.text
    assert r.json()["transport_status"] == "in_transit"
    with session_factory() as s:
        assert s.get(Order, oid).tracking_number == "GLS-99"


def test_mark_picked_up_rejects_non_packed(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, prep="preparing")
    r = client.post(f"/api/erp/orders/{oid}/mark-picked-up",
                    headers=auth_headers(client, "sat"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "not_packed"
