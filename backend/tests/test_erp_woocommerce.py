"""BoHub ERP Fase B PR B-2 — adaptador WooCommerce (mapper + admin API +
job de import). El cliente HTTP se testea con un stub inyectado — no
sale a red."""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.crypto import encrypt
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import (
    ErpException,
    IntegrationEvent,
    IntegrationEventStatus,
    Order,
    OrderLine,
    PaymentStatus,
    PreparationStatus,
    ProductSkuMapping,
    SkuMatchedBy,
)
from app.integrations.woocommerce.client import (
    WooError,
    WooHTTPClient,
    _to_iso8601_datetime,
)
from app.integrations.woocommerce.mapper import import_woo_order
from app.main import app
from app.models.crm import Company, Contact, ExternalSystem
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

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


def _mk_store(s: Session, slug="boprint") -> IntegrationAccount:
    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug,
        display_name=f"Woo {slug}", enabled=True,
        mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=f"https://{slug}.example",
        consumer_key_encrypted=encrypt("ck_test"),
        consumer_secret_encrypted=encrypt("cs_test"),
        credential_status="configured",
    )
    s.add(a)
    s.commit()
    return a


def _woo(**over) -> dict:
    base = {
        "id": 100,
        "number": "1001",
        "status": "processing",
        "total": "129.00",
        "currency": "EUR",
        "date_created": "2026-08-01T08:00:00Z",
        "date_paid": "2026-08-01T08:01:00Z",
        "billing": {
            "first_name": "Laura",
            "last_name": "Pérez",
            "email": "laura@ejemplo.com",
            "phone": "600111222",
            "city": "Barcelona",
            "country": "ES",
            "postcode": "08001",
            "address_1": "Calle Falsa 1",
            "company": "",
            "vat": "",
        },
        "line_items": [
            {"id": 1, "product_id": 42, "sku": "SKU-MBO-3050",
             "quantity": 1, "total": "129.00", "name": "MBO 3050 80W"},
        ],
        "meta_data": [],
    }
    base.update(over)
    return base


# --- mapper ------------------------------------------------------------------


def test_import_creates_order_contact_and_lines(session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        payload = _woo()
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        assert out.created is True and out.contact_created is True
        order = s.get(Order, out.order_id)
        assert order.external_source.value == "woocommerce"
        assert order.external_id == "100"
        assert order.store_id == store.id
        assert order.order_number == "BOPRIN-1001"
        assert order.payment_status == PaymentStatus.PAID  # date_paid seteado
        assert order.preparation_status == PreparationStatus.PENDING_REVIEW
        assert order.total_amount == pytest.approx(129.0)
        contact = s.scalar(select(Contact).where(
            func.lower(Contact.email) == "laura@ejemplo.com"))
        assert contact and contact.first_name == "Laura"
        assert order.contact_id == contact.id


def test_import_is_idempotent_no_duplicate_order(session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        payload = _woo()
        payload["_store_slug"] = store.account_id
        import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        assert out.created is False
        assert s.scalar(select(func.count(Order.id))) == 1


def test_import_pending_when_date_paid_missing(session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        payload = _woo(date_paid=None)
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        assert s.get(Order, out.order_id).payment_status == PaymentStatus.PENDING


def test_reimport_promotes_pending_to_paid_when_date_paid_arrives(session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        payload = _woo(date_paid=None)
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        # Segundo webhook (order.updated) con date_paid.
        payload2 = _woo(date_paid="2026-08-01T09:00:00Z")
        payload2["_store_slug"] = store.account_id
        import_woo_order(s, store=store, woo_order=payload2)
        s.commit()
        order = s.get(Order, out.order_id)
        assert order.payment_status == PaymentStatus.PAID


def test_reimport_does_not_regress_advanced_preparation(session_factory):
    """El ERP ya movió el pedido a preparing; un webhook tardío NO debe
    devolverlo a pending_review."""
    with session_factory() as s:
        store = _mk_store(s)
        payload = _woo()
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        order = s.get(Order, out.order_id)
        order.preparation_status = PreparationStatus.PREPARING
        s.commit()
        import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        assert s.get(Order, out.order_id).preparation_status == PreparationStatus.PREPARING


def test_import_auto_creates_company_from_billing_vat(session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        payload = _woo()
        payload["billing"]["company"] = "Rotulación Pérez SL"
        payload["billing"]["vat"] = "B61234567"
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        assert out.company_created is True
        company = s.scalar(select(Company).where(Company.tax_id == "B61234567"))
        assert company and company.name == "Rotulación Pérez SL"
        assert company.source == "woocommerce"
        order = s.get(Order, out.order_id)
        assert order.company_id == company.id


def test_import_reuses_company_by_normalised_cif(session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        s.add(Company(name="Rot Pérez", tax_id="B61234567"))
        s.commit()
        payload = _woo()
        # CIF con separadores/espacios y minúsculas — normaliza y encuentra.
        payload["billing"]["company"] = "Rot Pérez"
        payload["billing"]["vat"] = "b-6123 4567"
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        assert out.company_created is False
        assert s.scalar(select(func.count(Company.id))) == 1


def test_import_no_company_when_no_cif_or_no_name(session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        payload = _woo()  # sin company ni vat
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        assert out.company_created is False
        assert s.scalar(select(func.count(Company.id))) == 0


def test_unmapped_sku_creates_exception_and_leaves_codart_null(session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        payload = _woo()
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        assert out.unmapped_skus == ["SKU-MBO-3050"]
        line = s.scalar(select(OrderLine).where(OrderLine.order_id == out.order_id))
        assert line.product_codart is None
        exc = s.scalar(select(ErpException).where(
            ErpException.order_id == out.order_id))
        assert exc is not None
        meta = json.loads(exc.metadata_json)
        assert meta["code"] == "sku_unmapped" and meta["sku"] == "SKU-MBO-3050"


def test_import_before_cutoff_auto_marks_externally_processed(session_factory):
    """B-2-fix4: si la tienda tiene external_cutoff_date, los pedidos con
    date_created anterior entran ya como «procesado externamente» (fuera de
    la Cola PEDIDOS)."""
    with session_factory() as s:
        store = _mk_store(s)
        store.metadata_json = json.dumps({"external_cutoff_date": "2026-08-03"})
        s.commit()
        payload = _woo(date_created="2026-07-01T10:00:00Z")  # anterior al corte
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        order = s.get(Order, out.order_id)
        assert order.externally_processed_at is not None
        assert order.preparation_status == PreparationStatus.ALREADY_COMPLETED_EXTERNALLY


def test_import_after_cutoff_enters_normal_queue(session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        store.metadata_json = json.dumps({"external_cutoff_date": "2026-07-01"})
        s.commit()
        payload = _woo(date_created="2026-08-01T10:00:00Z")  # posterior al corte
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        order = s.get(Order, out.order_id)
        assert order.externally_processed_at is None
        assert order.preparation_status == PreparationStatus.PENDING_REVIEW


def test_admin_store_roundtrips_external_cutoff_date(client, session_factory):
    created = client.post(
        "/api/erp/integrations/woocommerce/stores",
        json={
            "account_id": "boprint", "display_name": "boprint.net",
            "base_url": "https://boprint.net",
            "consumer_key": "ck", "consumer_secret": "cs",
            "external_cutoff_date": "2026-08-03",
        },
        headers=auth_headers(client, "admin"),
    ).json()
    assert created["external_cutoff_date"] == "2026-08-03"
    # PATCH lo actualiza; "" lo limpia.
    upd = client.patch(
        f"/api/erp/integrations/woocommerce/stores/{created['id']}",
        json={"external_cutoff_date": "2026-01-01"},
        headers=auth_headers(client, "admin"),
    ).json()
    assert upd["external_cutoff_date"] == "2026-01-01"


def test_confirmed_sku_mapping_populates_codart(session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        s.add(ProductSkuMapping(
            woo_sku="SKU-MBO-3050", store_id=store.id,
            factusol_codart="MBO3050", matched_by=SkuMatchedBy.MANUAL,
            confirmed_at=datetime.now(UTC),
        ))
        s.commit()
        payload = _woo()
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        assert out.unmapped_skus == []
        line = s.scalar(select(OrderLine).where(OrderLine.order_id == out.order_id))
        assert line.product_codart == "MBO3050"
        # No excepción creada.
        assert s.scalar(select(func.count(ErpException.id))) == 0


def test_unconfirmed_mapping_still_counts_as_unmapped(session_factory):
    """Un mapping propuesto (auto por fuzzy) sin `confirmed_at` NO se usa
    automáticamente — requiere revisión en /admin/erp/sku-mapping (PR B-5)."""
    with session_factory() as s:
        store = _mk_store(s)
        s.add(ProductSkuMapping(
            woo_sku="SKU-MBO-3050", store_id=store.id,
            factusol_codart="MBO3050", matched_by=SkuMatchedBy.AUTO,
            confirmed_at=None,
        ))
        s.commit()
        payload = _woo()
        payload["_store_slug"] = store.account_id
        out = import_woo_order(s, store=store, woo_order=payload)
        s.commit()
        assert out.unmapped_skus == ["SKU-MBO-3050"]


# --- admin API ---------------------------------------------------------------


def test_admin_creates_store_and_hides_secrets(client, session_factory):
    r = client.post(
        "/api/erp/integrations/woocommerce/stores",
        json={
            "account_id": "boprint",
            "display_name": "boprint.net",
            "base_url": "https://boprint.net",
            "consumer_key": "ck_abc",
            "consumer_secret": "cs_xyz",
        },
        headers=auth_headers(client, "admin"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["account_id"] == "boprint"
    assert body["base_url"] == "https://boprint.net"
    # Los secretos NUNCA se devuelven.
    assert "consumer_key" not in body and "consumer_secret" not in body
    with session_factory() as s:
        a = s.scalar(select(IntegrationAccount).where(
            IntegrationAccount.account_id == "boprint"))
        assert a.consumer_key_encrypted and a.consumer_key_encrypted != "ck_abc"


def test_admin_duplicate_slug_rejected_409(client):
    payload = {
        "account_id": "boprint", "display_name": "boprint",
        "base_url": "https://boprint.net",
        "consumer_key": "ck", "consumer_secret": "cs",
    }
    client.post("/api/erp/integrations/woocommerce/stores", json=payload,
                headers=auth_headers(client, "admin"))
    r = client.post("/api/erp/integrations/woocommerce/stores", json=payload,
                    headers=auth_headers(client, "admin"))
    assert r.status_code == 409


def test_admin_endpoints_require_admin(client, session_factory):
    with session_factory() as s:
        _mk_store(s)
    for role in ("manager", "pedidos", "user", "viewer"):
        r = client.get("/api/erp/integrations/woocommerce/stores",
                       headers=auth_headers(client, role))
        assert r.status_code == 403, role


def test_admin_test_connection_calls_client(client, session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        sid = store.id
    with patch(
        "app.erp.api.woocommerce_admin.WooHTTPClient",
    ) as MockClient:
        instance = MockClient.return_value
        instance.list_orders.return_value = []
        r = client.post(
            f"/api/erp/integrations/woocommerce/stores/{sid}/test-connection",
            headers=auth_headers(client, "admin"),
        )
    assert r.status_code == 200 and r.json()["ok"] is True
    instance.list_orders.assert_called_once_with(per_page=1)


# --- job de import desde integration_events ---------------------------------


def test_import_from_event_marks_processed_and_creates_order(session_factory):
    from app.integrations.woocommerce import jobs as woo_jobs

    with session_factory() as s:
        store = _mk_store(s)
        payload = _woo()
        payload["_store_slug"] = store.account_id
        s.add(IntegrationEvent(
            system="woocommerce", account_id=store.account_id,
            external_event_id="wh-42", event_type="order.created",
            payload_json=json.dumps(payload),
        ))
        s.commit()
        event_id = s.scalar(select(IntegrationEvent.id))
    # Los jobs usan su propia sesión (get_engine) — parcheamos para
    # reusar la fixture del test contra el mismo engine in-memory.
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory):
        res = woo_jobs.import_order_from_event(event_id)
    assert res["created"] is True
    with session_factory() as s:
        ev = s.get(IntegrationEvent, event_id)
        assert ev.status == IntegrationEventStatus.PROCESSED
        assert ev.processed_at is not None
        assert s.scalar(select(func.count(Order.id))) == 1


def test_import_from_event_second_time_skipped(session_factory):
    from app.integrations.woocommerce import jobs as woo_jobs

    with session_factory() as s:
        store = _mk_store(s)
        payload = _woo()
        payload["_store_slug"] = store.account_id
        s.add(IntegrationEvent(
            system="woocommerce", account_id=store.account_id,
            external_event_id="wh-42", event_type="order.created",
            payload_json=json.dumps(payload),
            status=IntegrationEventStatus.PROCESSED,
            processed_at=datetime.now(UTC),
        ))
        s.commit()
        event_id = s.scalar(select(IntegrationEvent.id))
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory):
        res = woo_jobs.import_order_from_event(event_id)
    assert res.get("skipped") is True


def test_import_from_event_retries_on_failure_with_backoff(session_factory):
    from app.integrations.woocommerce import jobs as woo_jobs

    with session_factory() as s:
        store = _mk_store(s)
        s.add(IntegrationEvent(
            system="woocommerce", account_id=store.account_id,
            external_event_id="wh-bad", event_type="order.created",
            payload_json=json.dumps({"nope": True}),  # sin id → ValueError
        ))
        s.commit()
        event_id = s.scalar(select(IntegrationEvent.id))
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory):
        with pytest.raises(ValueError):
            woo_jobs.import_order_from_event(event_id)
    with session_factory() as s:
        ev = s.get(IntegrationEvent, event_id)
        assert ev.status == IntegrationEventStatus.RECEIVED
        assert ev.retry_count == 1
        assert ev.next_retry_at is not None
        assert ev.next_retry_at > datetime.now(UTC) - timedelta(seconds=5)


# --- B-2-fix: ISO 8601 en `after` + cuerpo de error en WooError ------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-07-04", "2026-07-04T00:00:00"),
        ("2026-07-04T09:30:00", "2026-07-04T09:30:00"),
        ("2026-07-04 09:30:00", "2026-07-04T09:30:00"),
        ("", ""),
        ("  2026-07-04  ", "2026-07-04T00:00:00"),
    ],
)
def test_to_iso8601_datetime_normalises_dates(raw, expected):
    assert _to_iso8601_datetime(raw) == expected


def test_list_orders_sends_after_as_full_iso8601(session_factory):
    """El bug real: WC v3 rechaza `after=2026-07-04` (400). El cliente debe
    enviar `after=2026-07-04T00:00:00`."""
    from unittest.mock import MagicMock

    captured: dict[str, object] = {}

    def _fake_request(method, url, **kwargs):  # noqa: ANN001
        captured["params"] = kwargs.get("params")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = []
        return resp

    with session_factory() as s:
        # Construir el cliente con el store aún atado a la sesión: __init__
        # descifra CK/CS eagerly, luego ya no necesita la sesión.
        client = WooHTTPClient(_mk_store(s))
    with patch("httpx.Client") as MockHttp:
        MockHttp.return_value.__enter__.return_value.request.side_effect = _fake_request
        client.list_orders(status="processing", since="2026-07-04", per_page=50)
    assert captured["params"]["after"] == "2026-07-04T00:00:00"


def test_woo_error_message_includes_response_body(session_factory):
    """Ante 400, el mensaje de WooError debe llevar el cuerpo de Woo para
    que aparezca en el log del worker (antes solo iba en `.body`)."""
    from unittest.mock import MagicMock

    with session_factory() as s:
        client = WooHTTPClient(_mk_store(s))

    def _fake_request(method, url, **kwargs):  # noqa: ANN001
        resp = MagicMock()
        resp.status_code = 400
        resp.text = '{"code":"rest_invalid_param","message":"after no es válido"}'
        return resp

    with patch("httpx.Client") as MockHttp:
        MockHttp.return_value.__enter__.return_value.request.side_effect = _fake_request
        with pytest.raises(WooError) as exc:
            client.list_orders(since="2026-07-04")
    assert "400" in str(exc.value)
    assert "after no es válido" in str(exc.value)  # cuerpo en el mensaje
    assert exc.value.status == 400


# --- B-2-fix3: el backfill ENCOLA el procesamiento (no deja events en received)


def test_backfill_creates_events_enqueues_jobs_and_processes_to_orders(session_factory):
    """Bug B-2: el backfill creaba integration_events pero nunca encolaba
    import_order_from_event → se quedaban en `received`. Ahora N pedidos →
    N events + N jobs encolados en woocommerce:import + N Orders al
    procesar. Registra SyncLog success."""
    from app.integrations.woocommerce import jobs as woo_jobs
    from app.models.crm import SyncLog

    with session_factory() as s:
        slug = _mk_store(s).account_id

    def _order(oid: int, email: str) -> dict:
        o = _woo(id=oid, number=str(oid))
        o["billing"] = {**o["billing"], "email": email}
        return o

    page1 = [
        _order(99865, "a@x.com"), _order(99864, "b@x.com"), _order(99863, "c@x.com"),
    ]
    enqueued_ids: list[str] = []

    with patch.object(woo_jobs, "_session_factory", return_value=session_factory), \
         patch.object(woo_jobs, "_enqueue_import", side_effect=enqueued_ids.append), \
         patch.object(woo_jobs, "WooHTTPClient") as MockClient:
        MockClient.return_value.list_orders.side_effect = [page1, []]
        res = woo_jobs.sync_orders_backfill(slug, "2026-07-04")

    assert res["created"] == 3
    assert res["enqueued"] == 3          # N jobs encolados en woocommerce:import
    assert len(enqueued_ids) == 3
    with session_factory() as s:
        assert s.scalar(select(func.count(IntegrationEvent.id))) == 3
        sl = s.scalar(select(SyncLog).where(SyncLog.operation == "sync_backfill"))
        assert sl.status == "success"
        assert sl.records_processed == 3

    # Procesa los jobs encolados (fuera de la sesión del backfill).
    for event_id in enqueued_ids:
        with patch.object(woo_jobs, "_session_factory", return_value=session_factory):
            woo_jobs.import_order_from_event(event_id)

    with session_factory() as s:
        assert s.scalar(select(func.count(Order.id))) == 3       # N Orders
        assert s.scalar(select(func.count(Contact.id)).where(
            Contact.origin == "woocommerce")) == 3               # contactos auto-creados
        assert s.scalar(select(func.count(IntegrationEvent.id)).where(
            IntegrationEvent.status == IntegrationEventStatus.PROCESSED)) == 3


def test_backfill_reenqueues_stuck_received_events(session_factory):
    """Recuperación: un re-run encola también los events `received` que se
    quedaron atascados en runs anteriores (los 25 de producción), aunque el
    dedup no cree ninguno nuevo."""
    from app.integrations.woocommerce import jobs as woo_jobs

    with session_factory() as s:
        store = _mk_store(s)
        slug = store.account_id
        # 2 events atascados de un run previo (status received).
        for oid in (99865, 99864):
            s.add(IntegrationEvent(
                system="woocommerce", account_id=slug,
                external_event_id=f"backfill:{oid}", event_type="order.backfill",
                payload_json=json.dumps(_woo(id=oid, number=str(oid))),
            ))
        s.commit()

    enqueued_ids: list[str] = []
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory), \
         patch.object(woo_jobs, "_enqueue_import", side_effect=enqueued_ids.append), \
         patch.object(woo_jobs, "WooHTTPClient") as MockClient:
        MockClient.return_value.list_orders.side_effect = [[], []]  # sin pedidos nuevos
        res = woo_jobs.sync_orders_backfill(slug)

    assert res["created"] == 0            # dedup: nada nuevo
    assert res["enqueued"] == 2           # pero re-encola los 2 atascados
    assert len(enqueued_ids) == 2
