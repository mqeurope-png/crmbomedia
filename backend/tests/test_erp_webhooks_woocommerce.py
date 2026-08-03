"""BoHub ERP Fase B PR B-3 — receptor de webhooks WooCommerce + procesador.

Firma HMAC, dedup por delivery-id, encolado async y el job processor
(refetch + import). El cliente Woo se stubea — no sale a red.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

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
    IntegrationEvent,
    IntegrationEventStatus,
    Order,
)
from app.integrations.woocommerce import jobs as woo_jobs
from app.integrations.woocommerce.client import WooError
from app.integrations.woocommerce.webhooks import compute_signature
from app.main import app
from app.models.crm import ExternalSystem
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import auth_headers, seed_test_users

_SECRET = "test-webhook-secret-abcd1234"


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


def _mk_store(s: Session, slug="boprint", *, enabled=True, secret=_SECRET) -> IntegrationAccount:
    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug,
        display_name=f"Woo {slug}", enabled=enabled,
        mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=f"https://{slug}.example",
        consumer_key_encrypted=encrypt("ck_test"),
        consumer_secret_encrypted=encrypt("cs_test"),
        credential_status="configured",
        metadata_json=json.dumps({"webhook_secret": secret}) if secret else None,
    )
    s.add(a)
    s.commit()
    return a


def _woo_order(**over) -> dict:
    base = {
        "id": 555, "number": "555", "status": "processing",
        "total": "80.00", "currency": "EUR",
        "date_created": "2026-08-03T10:00:00Z", "date_paid": "2026-08-03T10:01:00Z",
        "billing": {"first_name": "Ana", "last_name": "Ruiz",
                    "email": "ana@ejemplo.com", "company": "", "vat": ""},
        "line_items": [{"id": 1, "product_id": 9, "sku": "SKU-9",
                        "quantity": 1, "total": "80.00", "name": "Producto 9"}],
        "meta_data": [],
    }
    base.update(over)
    return base


def _post(client, slug, body: bytes, *, secret=_SECRET, sign=True,
          delivery="d-1", topic="order.created"):
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-WC-Webhook-Signature"] = compute_signature(secret, body)
    if delivery is not None:
        headers["X-WC-Webhook-Delivery-ID"] = delivery
    if topic is not None:
        headers["X-WC-Webhook-Topic"] = topic
    return client.post(f"/webhooks/woocommerce/{slug}", content=body, headers=headers)


# --- receptor: firma + dedup ------------------------------------------------


def test_valid_signature_creates_event_and_enqueues(client, session_factory):
    with session_factory() as s:
        _mk_store(s)
    body = json.dumps(_woo_order()).encode()
    with patch.object(woo_jobs, "enqueue_webhook_event") as enq, \
         patch("app.webhooks.woocommerce.enqueue_webhook_event", enq):
        r = _post(client, "boprint", body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["received"] is True and data["duplicate"] is False
    enq.assert_called_once_with(data["event_id"])
    with session_factory() as s:
        ev = s.get(IntegrationEvent, data["event_id"])
        assert ev.system == "woocommerce" and ev.account_id == "boprint"
        assert ev.status == IntegrationEventStatus.RECEIVED
        assert ev.external_event_id == "d-1"
        assert ev.event_type == "order.created"


def test_invalid_signature_rejected_401_no_event(client, session_factory):
    with session_factory() as s:
        _mk_store(s)
    body = json.dumps(_woo_order()).encode()
    headers = {
        "X-WC-Webhook-Signature": "not-the-right-signature",
        "X-WC-Webhook-Delivery-ID": "d-x", "X-WC-Webhook-Topic": "order.created",
    }
    r = client.post("/webhooks/woocommerce/boprint", content=body, headers=headers)
    assert r.status_code == 401
    assert r.json() == {"error": "invalid_signature"}
    with session_factory() as s:
        assert s.scalar(select(func.count(IntegrationEvent.id))) == 0


def test_missing_signature_header_rejected_401(client, session_factory):
    with session_factory() as s:
        _mk_store(s)
    body = json.dumps(_woo_order()).encode()
    r = _post(client, "boprint", body, sign=False)
    assert r.status_code == 401


def test_unknown_store_returns_404(client, session_factory):
    body = json.dumps(_woo_order()).encode()
    r = _post(client, "does-not-exist", body)
    assert r.status_code == 404
    assert r.json() == {"error": "unknown_store"}


def test_paused_store_returns_404(client, session_factory):
    with session_factory() as s:
        _mk_store(s, enabled=False)
    body = json.dumps(_woo_order()).encode()
    r = _post(client, "boprint", body)
    assert r.status_code == 404


def test_duplicate_delivery_id_is_idempotent(client, session_factory):
    with session_factory() as s:
        _mk_store(s)
    body = json.dumps(_woo_order()).encode()
    with patch("app.webhooks.woocommerce.enqueue_webhook_event"):
        first = _post(client, "boprint", body, delivery="dup-1")
        second = _post(client, "boprint", body, delivery="dup-1")
    assert first.status_code == 200 and first.json()["duplicate"] is False
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert second.json()["event_id"] == first.json()["event_id"]
    with session_factory() as s:
        assert s.scalar(select(func.count(IntegrationEvent.id))) == 1


# --- job processor ----------------------------------------------------------


def _seed_event(session_factory, *, topic="order.created", order=None,
                account_id="boprint") -> str:
    with session_factory() as s:
        _mk_store(s, slug=account_id)
        ev = IntegrationEvent(
            system="woocommerce", account_id=account_id,
            external_event_id=f"wh-{topic}", event_type=topic,
            payload_json=json.dumps(order or {"id": 555}),
        )
        s.add(ev)
        s.commit()
        return ev.id


def test_process_supported_topic_imports_order(session_factory):
    event_id = _seed_event(session_factory, order={"id": 555})
    fake = MagicMock()
    fake.get_order.return_value = _woo_order(id=555)
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory), \
         patch.object(woo_jobs, "WooHTTPClient", return_value=fake):
        res = woo_jobs.process_webhook_event(event_id)
    fake.get_order.assert_called_once_with(555)
    assert res["created"] is True
    with session_factory() as s:
        ev = s.get(IntegrationEvent, event_id)
        assert ev.status == IntegrationEventStatus.PROCESSED
        assert ev.processed_at is not None
        assert s.scalar(select(func.count(Order.id))) == 1


def test_process_unsupported_topic_is_ignored(session_factory):
    event_id = _seed_event(session_factory, topic="product.updated")
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory), \
         patch.object(woo_jobs, "WooHTTPClient") as MockClient:
        res = woo_jobs.process_webhook_event(event_id)
    MockClient.assert_not_called()
    assert res["ignored"] is True
    with session_factory() as s:
        assert s.get(IntegrationEvent, event_id).status == IntegrationEventStatus.IGNORED


def test_process_woo_404_marks_failed_without_raise(session_factory):
    event_id = _seed_event(session_factory, order={"id": 999})
    fake = MagicMock()
    fake.get_order.side_effect = WooError("gone", status=404, body="not found")
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory), \
         patch.object(woo_jobs, "WooHTTPClient", return_value=fake):
        res = woo_jobs.process_webhook_event(event_id)  # no raise
    assert res["error"] == "order_not_found"
    with session_factory() as s:
        assert s.get(IntegrationEvent, event_id).status == IntegrationEventStatus.FAILED


def test_process_woo_5xx_raises_for_retry(session_factory):
    event_id = _seed_event(session_factory, order={"id": 555})
    fake = MagicMock()
    fake.get_order.side_effect = WooError("boom", status=503, body="server error")
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory), \
         patch.object(woo_jobs, "WooHTTPClient", return_value=fake):
        with pytest.raises(WooError):
            woo_jobs.process_webhook_event(event_id)
    with session_factory() as s:
        # No queda processed; el error queda registrado para el retry.
        ev = s.get(IntegrationEvent, event_id)
        assert ev.status != IntegrationEventStatus.PROCESSED
        assert ev.error_message is not None


def test_failure_callback_marks_event_failed(session_factory):
    event_id = _seed_event(session_factory, order={"id": 555})
    job = MagicMock()
    job.args = (event_id,)
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory):
        woo_jobs.handle_webhook_failure(job, None, WooError, WooError("boom"), None)
    with session_factory() as s:
        assert s.get(IntegrationEvent, event_id).status == IntegrationEventStatus.FAILED


# --- admin: secret + estado -------------------------------------------------


def test_create_store_generates_webhook_secret(client, session_factory):
    r = client.post(
        "/api/erp/integrations/woocommerce/stores",
        json={"account_id": "artisjet", "display_name": "artisjet",
              "base_url": "https://artisjet.example",
              "consumer_key": "ck", "consumer_secret": "cs"},
        headers=auth_headers(client, "admin"),
    )
    assert r.status_code == 201, r.text
    with session_factory() as s:
        acc = s.scalar(select(IntegrationAccount).where(
            IntegrationAccount.account_id == "artisjet"))
        meta = json.loads(acc.metadata_json)
        assert meta.get("webhook_secret")
        # El secreto NO se devuelve en el create.
    assert "webhook_secret" not in r.json()


def test_regenerate_webhook_secret_changes_value(client, session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        sid = store.id
    r = client.post(
        f"/api/erp/integrations/woocommerce/stores/{sid}/regenerate-webhook-secret",
        headers=auth_headers(client, "admin"),
    )
    assert r.status_code == 200, r.text
    new_secret = r.json()["webhook_secret"]
    assert new_secret and new_secret != _SECRET
    with session_factory() as s:
        acc = s.get(IntegrationAccount, sid)
        assert json.loads(acc.metadata_json)["webhook_secret"] == new_secret


def test_regenerate_requires_admin(client, session_factory):
    with session_factory() as s:
        sid = _mk_store(s).id
    for role in ("pedidos", "sat", "user", "viewer"):
        r = client.post(
            f"/api/erp/integrations/woocommerce/stores/{sid}/regenerate-webhook-secret",
            headers=auth_headers(client, role),
        )
        assert r.status_code == 403, role


def test_webhook_status_reports_counts(client, session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        sid = store.id
        # 3 eventos recientes: 2 ok + 1 failed, dos topics.
        s.add(IntegrationEvent(system="woocommerce", account_id="boprint",
              external_event_id="a", event_type="order.created",
              payload_json="{}", status=IntegrationEventStatus.PROCESSED))
        s.add(IntegrationEvent(system="woocommerce", account_id="boprint",
              external_event_id="b", event_type="order.updated",
              payload_json="{}", status=IntegrationEventStatus.PROCESSED))
        s.add(IntegrationEvent(system="woocommerce", account_id="boprint",
              external_event_id="c", event_type="order.created",
              payload_json="{}", status=IntegrationEventStatus.FAILED))
        s.commit()
    r = client.get(
        f"/api/erp/integrations/woocommerce/stores/{sid}/webhook-status",
        headers=auth_headers(client, "admin"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["webhook_url"].endswith("/webhooks/woocommerce/boprint")
    assert body["webhook_secret_last4"] == _SECRET[-4:]
    assert body["count_24h"] == 3
    assert body["errors_24h"] == 1
    assert set(body["topics_received_24h"]) == {"order.created", "order.updated"}
    assert body["last_received_at"] is not None


def test_list_stores_includes_webhook_summary(client, session_factory):
    with session_factory() as s:
        _mk_store(s)
        s.add(IntegrationEvent(system="woocommerce", account_id="boprint",
              external_event_id="z", event_type="order.created",
              payload_json="{}", status=IntegrationEventStatus.PROCESSED))
        s.commit()
    r = client.get("/api/erp/integrations/woocommerce/stores",
                   headers=auth_headers(client, "admin"))
    assert r.status_code == 200
    item = next(i for i in r.json()["items"] if i["account_id"] == "boprint")
    assert item["webhook_summary"]["count_24h"] == 1
    assert item["webhook_summary"]["errors_24h"] == 0


def test_webhook_status_older_events_excluded_from_24h(client, session_factory):
    with session_factory() as s:
        store = _mk_store(s)
        sid = store.id
        old = IntegrationEvent(system="woocommerce", account_id="boprint",
              external_event_id="old", event_type="order.created",
              payload_json="{}", status=IntegrationEventStatus.PROCESSED)
        s.add(old)
        s.flush()
        old.created_at = datetime.now(UTC) - timedelta(hours=48)
        s.commit()
    r = client.get(
        f"/api/erp/integrations/woocommerce/stores/{sid}/webhook-status",
        headers=auth_headers(client, "admin"),
    )
    body = r.json()
    assert body["count_24h"] == 0          # el de hace 48h no cuenta
    assert body["last_received_at"] is not None  # pero sí es el último recibido
