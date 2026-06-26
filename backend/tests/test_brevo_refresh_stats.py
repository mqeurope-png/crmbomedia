"""PR-Fix-Sincronizar-Stats-Brevo — tests para
`POST /api/brevo/campaigns/{id}/refresh-stats`.

Bug 6 ya se intentó cerrar en PR #237 + PR #238 sin éxito: el toast
era educado ("Brevo aún no tiene stats disponibles, son normales
en envíos recientes <2h") pero mentía cuando Brevo SÍ tenía datos.

Estos tests blindan los 5 invariantes que el spec pide:

1. El happy-path persiste las stats devueltas por Brevo en
   `BrevoCampaignCache.stats_json` y reporta `sync_status.kind="ok"`.
2. El servicio loggea la respuesta cruda de Brevo a INFO para que
   futuras incidencias puedan diagnosticarse sin reproducir.
3. La heurística "es campaña reciente" depende de `sent_at`, NO de
   `delivered==0`:
   - todo-cero + sent_at <2h → `kind="recent"`.
   - todo-cero + sent_at ≥2h → `kind="empty"` (warning honesto).
4. Brevo 4xx/5xx sube como 502 con el código + mensaje real, no
   como toast genérico.
5. El cliente Brevo se invoca con el `brevo_campaign_id` numérico
   (el #46 de Brevo), NUNCA con el id interno UUID del cache.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.db.session import get_session
from app.integrations.errors import IntegrationClientError
from app.main import app
from app.models.brevo import BrevoCampaignCache
from app.models.crm import Base, ExternalSystem
from app.models.integration_settings import IntegrationAccount, IntegrationMode
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sf() as seed:
        seed_test_users(seed)
        seed.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="main",
                display_name="Brevo main",
                enabled=True,
                mode=IntegrationMode.LIVE,
                api_key_encrypted=crypto.encrypt("dummy"),
            )
        )
        seed.commit()
    yield sf
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed_campaign(
    factory: sessionmaker,
    *,
    brevo_campaign_id: int = 46,
    sent_at: datetime | None = None,
    stats: dict | None = None,
) -> str:
    """Insert a BrevoCampaignCache row and return its CRM uuid."""
    with factory() as session:
        row = BrevoCampaignCache(
            brevo_account_id="main",
            brevo_campaign_id=brevo_campaign_id,
            name="MAps NL A",
            status="sent",
            type="classic",
            sent_at=sent_at,
            cached_at=datetime.now(UTC),
            stats_json=json.dumps(stats or {"sent": 0, "delivered": 0}),
        )
        session.add(row)
        session.commit()
        return row.id


# ---------------------------------------------------------------------------
# 1. happy path: Brevo returns real stats → persisted + kind="ok".
# ---------------------------------------------------------------------------


def test_sync_stats_endpoint_persists_brevo_stats_for_existing_campaign(
    client: TestClient, factory: sessionmaker
) -> None:
    campaign_id = _seed_campaign(
        factory, sent_at=datetime.now(UTC) - timedelta(hours=24)
    )

    brevo_payload = {
        "id": 46,
        "name": "MAps NL A",
        "status": "sent",
        "sentDate": (datetime.now(UTC) - timedelta(hours=24)).isoformat(),
        "statistics": {
            "globalStats": {
                "sent": 100,
                "delivered": 90,
                "uniqueViews": 33,
                "uniqueClicks": 1,
                "softBounces": 5,
                "hardBounces": 5,
                "unsubscriptions": 1,
                "complaints": 0,
            }
        },
    }
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None
    fake_client.get_email_campaign = AsyncMock(return_value=brevo_payload)

    with patch(
        "app.integrations.brevo.campaigns.BrevoClient",
        return_value=fake_client,
    ):
        response = client.post(
            f"/api/brevo/campaigns/{campaign_id}/refresh-stats",
            headers=auth_headers(client, "admin"),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sync_status"]["kind"] == "ok"
    assert body["sync_status"]["brevo_returned_zero"] is False
    assert body["campaign"]["stats"]["delivered"] == 90
    assert body["campaign"]["stats"]["uniqueViews"] == 33

    with factory() as session:
        row = session.get(BrevoCampaignCache, campaign_id)
        persisted = json.loads(row.stats_json)
        assert persisted["sent"] == 100
        assert persisted["delivered"] == 90


# ---------------------------------------------------------------------------
# 2. diagnostics: raw Brevo payload is logged at INFO.
# ---------------------------------------------------------------------------


def test_sync_stats_endpoint_logs_brevo_response_for_diagnostics(
    client: TestClient,
    factory: sessionmaker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    campaign_id = _seed_campaign(factory)
    brevo_payload = {
        "id": 46,
        "name": "MAps NL A",
        "status": "sent",
        "statistics": {"globalStats": {"sent": 5, "delivered": 5}},
    }
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None
    fake_client.get_email_campaign = AsyncMock(return_value=brevo_payload)

    with caplog.at_level(
        logging.INFO, logger="app.integrations.brevo.campaigns"
    ), patch(
        "app.integrations.brevo.campaigns.BrevoClient",
        return_value=fake_client,
    ):
        response = client.post(
            f"/api/brevo/campaigns/{campaign_id}/refresh-stats",
            headers=auth_headers(client, "admin"),
        )

    assert response.status_code == 200, response.text
    diag = [
        record
        for record in caplog.records
        if record.name == "app.integrations.brevo.campaigns"
        and "brevo.refresh_stats" in record.getMessage()
    ]
    assert diag, (
        "raw Brevo response must be logged via "
        "`brevo.refresh_stats` INFO entry"
    )
    message = diag[0].getMessage()
    # Defensa concreta del paso 2 del spec: la línea contiene el
    # campaign_id correcto y la sección statistics serializada.
    assert "campaign_id=46" in message
    assert "globalStats" in message


# ---------------------------------------------------------------------------
# 3. recent-campaign branch + ≥2h-no-data branch, both driven by sent_at.
# ---------------------------------------------------------------------------


def test_sync_stats_endpoint_reports_recent_campaign_correctly(
    client: TestClient, factory: sessionmaker
) -> None:
    # Campaign sent 30 min ago — Brevo's pipeline often returns
    # all-zero counters during the first 1-2 h.
    recent_sent_at = datetime.now(UTC) - timedelta(minutes=30)
    campaign_id = _seed_campaign(factory, sent_at=recent_sent_at)
    zero_payload_recent = {
        "id": 46,
        "status": "sent",
        # Carry sentDate so `upsert_campaign_row` doesn't wipe row.sent_at
        # — Brevo always returns it for sent campaigns.
        "sentDate": recent_sent_at.isoformat(),
        "statistics": {
            "globalStats": {
                "sent": 0,
                "delivered": 0,
                "uniqueViews": 0,
                "uniqueClicks": 0,
            }
        },
    }
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None
    fake_client.get_email_campaign = AsyncMock(return_value=zero_payload_recent)

    with patch(
        "app.integrations.brevo.campaigns.BrevoClient",
        return_value=fake_client,
    ):
        response = client.post(
            f"/api/brevo/campaigns/{campaign_id}/refresh-stats",
            headers=auth_headers(client, "admin"),
        )

    assert response.status_code == 200, response.text
    status_block = response.json()["sync_status"]
    assert status_block["kind"] == "recent"
    assert status_block["brevo_returned_zero"] is True
    assert status_block["seconds_since_sent"] is not None
    assert status_block["seconds_since_sent"] < 7200

    # And the inverse: same all-zero payload but campaign sent 24 h
    # ago → honest "empty" toast, NOT the polite "es reciente" lie.
    old_sent_at = datetime.now(UTC) - timedelta(hours=24)
    campaign_id_old = _seed_campaign(
        factory, brevo_campaign_id=47, sent_at=old_sent_at
    )
    zero_payload_old = {
        "id": 47,
        "status": "sent",
        "sentDate": old_sent_at.isoformat(),
        "statistics": {
            "globalStats": {"sent": 0, "delivered": 0},
        },
    }
    fake_client.get_email_campaign = AsyncMock(return_value=zero_payload_old)
    with patch(
        "app.integrations.brevo.campaigns.BrevoClient",
        return_value=fake_client,
    ):
        response = client.post(
            f"/api/brevo/campaigns/{campaign_id_old}/refresh-stats",
            headers=auth_headers(client, "admin"),
        )

    assert response.status_code == 200, response.text
    status_block_old = response.json()["sync_status"]
    assert status_block_old["kind"] == "empty"
    assert status_block_old["brevo_returned_zero"] is True


# ---------------------------------------------------------------------------
# 4. 4xx from Brevo bubbles up as 502 with the real status + message.
# ---------------------------------------------------------------------------


def test_sync_stats_endpoint_reports_error_on_brevo_4xx(
    client: TestClient, factory: sessionmaker
) -> None:
    campaign_id = _seed_campaign(factory)

    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None
    fake_client.get_email_campaign = AsyncMock(
        side_effect=IntegrationClientError(
            "404 from brevo/main",
            system="brevo",
            account_id="main",
            status_code=404,
            body='{"code":"document_not_found"}',
        )
    )

    with patch(
        "app.integrations.brevo.campaigns.BrevoClient",
        return_value=fake_client,
    ):
        response = client.post(
            f"/api/brevo/campaigns/{campaign_id}/refresh-stats",
            headers=auth_headers(client, "admin"),
        )

    assert response.status_code == 502, response.text
    detail = response.json()["detail"]
    # Honest surfacing — operator sees the actual Brevo status, NOT a
    # generic "no disponibles" toast.
    assert "404" in detail
    assert "Brevo" in detail


# ---------------------------------------------------------------------------
# 5. The Brevo client is called with the numeric brevo_campaign_id, not
#    the CRM uuid. Pin against the regression Bart suspected in his
#    diagnostic (point 1 of the spec).
# ---------------------------------------------------------------------------


def test_sync_stats_endpoint_uses_brevo_campaign_id_not_internal_id(
    client: TestClient, factory: sessionmaker
) -> None:
    campaign_id = _seed_campaign(factory, brevo_campaign_id=46)

    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None
    fake_client.get_email_campaign = AsyncMock(
        return_value={"id": 46, "status": "sent", "statistics": {}}
    )

    with patch(
        "app.integrations.brevo.campaigns.BrevoClient",
        return_value=fake_client,
    ):
        response = client.post(
            f"/api/brevo/campaigns/{campaign_id}/refresh-stats",
            headers=auth_headers(client, "admin"),
        )

    assert response.status_code == 200, response.text
    fake_client.get_email_campaign.assert_awaited_once_with(46)
    # The CRM uuid is a 36-char string; assert we never passed it.
    call_args = fake_client.get_email_campaign.await_args
    assert call_args.args == (46,)
    assert campaign_id not in (str(a) for a in call_args.args)
