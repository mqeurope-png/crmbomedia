"""BoHub ERP Fase B PR B-1 — migración 0081: integration_events +
extensiones integration_accounts (WooCommerce) y carriers (Genei).
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra todos los modelos
from app.db.base import Base
from app.erp.models import (
    MAX_RETRIES,
    RETRY_BACKOFF_SECONDS,
    Carrier,
    IntegrationEvent,
    IntegrationEventStatus,
)
from app.models.crm import ExternalSystem
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)


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
    yield factory
    Base.metadata.drop_all(engine)


# --- integration_events -----------------------------------------------------


def _mk_event(**over) -> IntegrationEvent:
    base = {
        "system": "woocommerce", "account_id": "boprint",
        "external_event_id": "wh-0001", "event_type": "order.created",
        "payload_json": '{"id": 42}',
    }
    base.update(over)
    return IntegrationEvent(**base)


def test_event_defaults_are_received_zero_retries(session_factory):
    with session_factory() as s:
        s.add(_mk_event())
        s.commit()
        e = s.scalar(select(IntegrationEvent))
        assert e.status == IntegrationEventStatus.RECEIVED
        assert e.retry_count == 0
        assert e.next_retry_at is None
        assert e.processed_at is None


def test_event_dedup_unique_on_system_account_external_id(session_factory):
    with session_factory() as s:
        s.add(_mk_event())
        s.commit()
        # Mismo (system, account_id, external_event_id) → IntegrityError.
        s.add(_mk_event(event_type="order.updated"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_event_same_external_id_across_stores_is_allowed(session_factory):
    with session_factory() as s:
        s.add(_mk_event(account_id="boprint"))
        s.add(_mk_event(account_id="artisjet"))  # misma id remota, otra tienda
        s.commit()
        assert s.scalar(select(IntegrationEvent).where(
            IntegrationEvent.account_id == "artisjet")) is not None


def test_event_same_external_id_across_systems_is_allowed(session_factory):
    with session_factory() as s:
        s.add(_mk_event(system="woocommerce"))
        s.add(_mk_event(system="genei"))  # otro system, misma id remota
        s.commit()
        n = s.scalar(select(IntegrationEvent).where(
            IntegrationEvent.external_event_id == "wh-0001"))
        # dos rows conviven — dedup solo dentro de mismo system.
        assert n is not None


def test_event_status_and_retry_lifecycle(session_factory):
    with session_factory() as s:
        e = _mk_event()
        s.add(e)
        s.commit()
        e.status = IntegrationEventStatus.PROCESSING
        e.retry_count = 2
        e.next_retry_at = datetime.now(UTC) + timedelta(seconds=RETRY_BACKOFF_SECONDS[2])
        e.error_message = "timeout"
        s.commit()
        assert MAX_RETRIES == len(RETRY_BACKOFF_SECONDS) == 5
        e2 = s.scalar(select(IntegrationEvent))
        assert e2.status == IntegrationEventStatus.PROCESSING
        assert e2.retry_count == 2
        assert e2.error_message == "timeout"


def test_event_can_be_marked_processed_and_duplicate(session_factory):
    with session_factory() as s:
        e = _mk_event()
        s.add(e)
        s.commit()
        e.status = IntegrationEventStatus.PROCESSED
        e.processed_at = datetime.now(UTC)
        s.commit()
        # una segunda entrega marcada como duplicate (después del UNIQUE
        # el worker crea otra row solo cuando cambia algún componente del
        # dedup — aquí probamos que el estado DUPLICATE es válido).
        s.add(_mk_event(external_event_id="wh-0002",
                        status=IntegrationEventStatus.DUPLICATE))
        s.commit()
        statuses = {row.status for row in s.scalars(select(IntegrationEvent))}
        assert {IntegrationEventStatus.PROCESSED, IntegrationEventStatus.DUPLICATE} <= statuses


# --- extensión integration_accounts (WooCommerce) ---------------------------


def test_integration_account_woocommerce_stores_multi_tienda(session_factory):
    with session_factory() as s:
        for slug, url in [
            ("boprint", "https://boprint.net"),
            ("artisjet", "https://artisjet-printers.eu"),
            ("flux", "https://fluxlasers.es"),
        ]:
            s.add(IntegrationAccount(
                system=ExternalSystem.WOOCOMMERCE, account_id=slug,
                display_name=f"Woo {slug}", enabled=True,
                mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
                base_url=url,
                consumer_key_encrypted="ENC_CK",
                consumer_secret_encrypted="ENC_CS",
                metadata_json='{"webhook_version": "wp_api_v3"}',
            ))
        s.commit()
        stores = list(s.scalars(select(IntegrationAccount).where(
            IntegrationAccount.system == ExternalSystem.WOOCOMMERCE)))
        assert len(stores) == 3
        assert {s_.account_id for s_ in stores} == {"boprint", "artisjet", "flux"}
        first = next(x for x in stores if x.account_id == "boprint")
        assert first.base_url == "https://boprint.net"
        assert first.consumer_key_encrypted == "ENC_CK"
        assert first.consumer_secret_encrypted == "ENC_CS"


def test_integration_account_legacy_rows_have_nullable_new_columns(session_factory):
    """AgileCRM/Brevo/… existentes NO usan los campos nuevos — deben poder
    guardarse sin rellenar base_url/consumer_*."""
    with session_factory() as s:
        s.add(IntegrationAccount(
            system=ExternalSystem.AGILECRM, account_id="default",
            display_name="Agile", enabled=False,
            mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
            api_key_encrypted="OLD_KEY",
        ))
        s.commit()
        row = s.scalar(select(IntegrationAccount))
        assert row.base_url is None
        assert row.consumer_key_encrypted is None
        assert row.consumer_secret_encrypted is None
        assert row.metadata_json is None


def test_woocommerce_ends_up_in_external_system_enum():
    # Contract test: ExternalSystem incluye woocommerce y genei (Fase B).
    assert ExternalSystem.WOOCOMMERCE.value == "woocommerce"
    assert ExternalSystem.GENEI.value == "genei"


# --- extensión carriers (Genei API + DSV manual) ----------------------------


def test_carrier_genei_stores_api_fields(session_factory):
    with session_factory() as s:
        s.add(Carrier(
            name="Genei", code="genei", has_api=True,
            adapter_class="app.erp.integrations.genei.client.GeneiClient",
            api_base_url="https://apiv2.genei.es",
            api_credentials_encrypted="ENC_USER_PASS",
            webhook_secret_encrypted=None,  # Genei aún no firma webhooks
            default_address_id="1304422",
        ))
        s.commit()
        c = s.scalar(select(Carrier).where(Carrier.code == "genei"))
        assert c.has_api is True
        assert c.api_base_url == "https://apiv2.genei.es"
        assert c.default_address_id == "1304422"


def test_carrier_dsv_manual_leaves_new_columns_null(session_factory):
    """DSV se configura como manual/portal (sin API) — todos los campos
    Fase B son NULL y sigue funcionando como en Fase A."""
    with session_factory() as s:
        s.add(Carrier(name="DSV", code="dsv", has_api=False, active=True))
        s.commit()
        c = s.scalar(select(Carrier).where(Carrier.code == "dsv"))
        assert c.has_api is False
        assert c.api_credentials_encrypted is None
        assert c.api_base_url is None
        assert c.default_address_id is None
