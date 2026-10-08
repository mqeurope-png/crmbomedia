"""Brevo webhook receiver + event materialisation."""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.integrations.brevo.webhooks import (
    process_brevo_webhook_event,
)
from app.main import app
from app.models.crm import ActivityEvent, AuditLog, Contact, ExternalSystem
from app.models.integration_settings import IntegrationAccount
from tests._test_helpers import seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as session:
        session.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="main",
                display_name="Brevo",
                enabled=True,
            )
        )
        session.add(
            Contact(
                first_name="Ana",
                email="ana@example.com",
                marketing_consent="granted",
            )
        )
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory) -> Generator[TestClient, None, None]:
    with session_factory() as seed:
        seed_test_users(seed)

    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _event(name: str, **overrides):
    base = {
        "event": name,
        "email": "ana@example.com",
        "id": 117,
        "message-id": "<msg-1@brevo>",
        "date": "2026-06-10 12:00:00",
        "subject": "Oferta verano",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Processor unit tests
# ---------------------------------------------------------------------------


def test_opened_event_creates_activity_event(session_factory):
    with session_factory() as session:
        status = process_brevo_webhook_event(
            session,
            _event("opened", **{"campaign-id": 39}),
            account_id="main",
        )
        session.commit()
        assert status == "processed"
        event = session.scalar(select(ActivityEvent))
        assert event.event_type == "email.opened"
        assert event.system == "brevo"
        assert event.subject == "Oferta verano"
        # The mapper picks Brevo's `campaign-id` payload key — the
        # recipients endpoint needs it to filter responses per
        # campaign.
        assert event.campaign_brevo_id == 39


def test_event_without_campaign_id_lands_with_null_column(session_factory):
    """Transactional sends don't carry a campaign — the column stays
    NULL and the row is still materialised."""
    with session_factory() as session:
        process_brevo_webhook_event(
            session, _event("delivered"), account_id="main"
        )
        session.commit()
        event = session.scalar(select(ActivityEvent))
        assert event is not None
        assert event.campaign_brevo_id is None


def test_event_campaign_id_accepts_string_payload(session_factory):
    """Brevo occasionally ships the id as a JSON string."""
    with session_factory() as session:
        process_brevo_webhook_event(
            session, _event("opened", **{"campaign-id": "42"}),
            account_id="main",
        )
        session.commit()
        event = session.scalar(select(ActivityEvent))
        assert event.campaign_brevo_id == 42
        assert event.occurred_at is not None


def test_brevo_webhook_persists_sent_event_correctly(session_factory):
    """PR-Fix-Sent-Backfill. El evento `sent` de Brevo se persiste como
    `email.sent` con la campaña correcta (base del filtro "Enviados")."""
    with session_factory() as session:
        status = process_brevo_webhook_event(
            session, _event("sent", **{"campaign-id": 77}), account_id="main"
        )
        session.commit()
        assert status == "processed"
        event = session.scalar(
            select(ActivityEvent).where(ActivityEvent.event_type == "email.sent")
        )
        assert event is not None
        assert event.campaign_brevo_id == 77
        assert event.contact_id is not None


def test_brevo_webhook_persists_delivered_event_correctly(session_factory):
    with session_factory() as session:
        status = process_brevo_webhook_event(
            session,
            _event("delivered", **{"campaign-id": 77}),
            account_id="main",
        )
        session.commit()
        assert status == "processed"
        event = session.scalar(
            select(ActivityEvent).where(
                ActivityEvent.event_type == "email.delivered"
            )
        )
        assert event is not None
        assert event.campaign_brevo_id == 77


def test_click_event_stores_url(session_factory):
    with session_factory() as session:
        process_brevo_webhook_event(
            session,
            _event("click", link="https://mbolasers.com/promo"),
            account_id="main",
        )
        session.commit()
        event = session.scalar(select(ActivityEvent))
        assert event.event_type == "email.clicked"
        assert event.body == "https://mbolasers.com/promo"


def test_unsubscribe_flips_marketing_consent(session_factory):
    with session_factory() as session:
        process_brevo_webhook_event(
            session, _event("unsubscribe"), account_id="main"
        )
        session.commit()
        contact = session.scalar(select(Contact))
        assert contact.marketing_consent == "unsubscribed"
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "contact.consent_changed_by_webhook"
            )
        )
        assert audit is not None


def test_hard_bounce_invalidates_email(session_factory):
    with session_factory() as session:
        process_brevo_webhook_event(
            session, _event("hard_bounce"), account_id="main"
        )
        session.commit()
        contact = session.scalar(select(Contact))
        assert contact.is_email_valid is False
        # Consent untouched by a bounce.
        assert contact.marketing_consent == "granted"


def test_spam_flips_both(session_factory):
    with session_factory() as session:
        process_brevo_webhook_event(session, _event("spam"), account_id="main")
        session.commit()
        contact = session.scalar(select(Contact))
        assert contact.marketing_consent == "unsubscribed"
        assert contact.is_email_valid is False


def test_unknown_email_is_logged_and_discarded(session_factory, caplog):
    with session_factory() as session:
        with caplog.at_level("WARNING"):
            status = process_brevo_webhook_event(
                session,
                _event("opened", email="stranger@nowhere.invalid"),
                account_id="main",
            )
        session.commit()
        assert status == "unknown_contact"
        assert session.scalar(select(ActivityEvent)) is None
        assert session.scalar(
            select(Contact).where(Contact.email == "stranger@nowhere.invalid")
        ) is None
        assert any("no CRM contact" in rec.message for rec in caplog.records)


def test_duplicate_event_processed_once(session_factory):
    with session_factory() as session:
        first = process_brevo_webhook_event(
            session, _event("delivered"), account_id="main"
        )
        second = process_brevo_webhook_event(
            session, _event("delivered"), account_id="main"
        )
        session.commit()
        assert first == "processed"
        assert second == "duplicate"
        events = list(session.scalars(select(ActivityEvent)))
        assert len(events) == 1


def test_unsupported_event_is_ignored(session_factory):
    with session_factory() as session:
        status = process_brevo_webhook_event(
            session, _event("proxy_open"), account_id="main"
        )
        assert status == "unknown_event"


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------


def test_route_accepts_and_enqueues(client: TestClient, monkeypatch):
    monkeypatch.delenv("BREVO_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    captured: dict = {}

    def fake_enqueue(events, account_id):
        captured["events"] = events
        captured["account_id"] = account_id

    with patch("app.api.webhooks._enqueue_brevo_events", fake_enqueue):
        response = client.post("/api/webhooks/brevo", json=_event("delivered"))
    assert response.status_code == 200, response.text
    assert response.json()["events"] == 1
    assert captured["account_id"] == "main"


def test_route_accepts_event_arrays(client: TestClient, monkeypatch):
    monkeypatch.delenv("BREVO_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    captured: dict = {}

    with patch(
        "app.api.webhooks._enqueue_brevo_events",
        lambda events, account_id: captured.update(events=events),
    ):
        response = client.post(
            "/api/webhooks/brevo",
            json=[_event("delivered"), _event("opened")],
        )
    assert response.status_code == 200
    assert len(captured["events"]) == 2


def test_route_rejects_bad_signature_when_secret_set(
    client: TestClient, monkeypatch
):
    monkeypatch.setenv("BREVO_WEBHOOK_SECRET", "super-secret")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    response = client.post(
        "/api/webhooks/brevo",
        json=_event("delivered"),
        headers={"brevo-signature-token": "wrong"},
    )
    assert response.status_code == 401


def test_route_accepts_valid_signature(client: TestClient, monkeypatch):
    monkeypatch.setenv("BREVO_WEBHOOK_SECRET", "super-secret")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    with patch("app.api.webhooks._enqueue_brevo_events", lambda *a: None):
        response = client.post(
            "/api/webhooks/brevo",
            json=_event("delivered"),
            headers={"brevo-signature-token": "super-secret"},
        )
    assert response.status_code == 200


def test_route_warns_but_accepts_without_secret(
    client: TestClient, monkeypatch, caplog
):
    monkeypatch.delenv("BREVO_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    with (
        patch("app.api.webhooks._enqueue_brevo_events", lambda *a: None),
        caplog.at_level("WARNING"),
    ):
        response = client.post("/api/webhooks/brevo", json=_event("delivered"))
    assert response.status_code == 200
    assert any(
        "WITHOUT signature validation" in rec.message for rec in caplog.records
    )


def test_route_rejects_non_json(client: TestClient, monkeypatch):
    monkeypatch.delenv("BREVO_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    response = client.post(
        "/api/webhooks/brevo",
        content=b"not json",
        headers={"content-type": "text/plain"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# PR-Hotfix-Notas-Workflows Item B — el webhook Brevo DISPARA workflows
# ---------------------------------------------------------------------------


def test_opened_event_dispatches_brevo_workflow_trigger(session_factory):
    """El bug: el webhook nunca llamaba al dispatcher, así que los
    triggers `email.brevo.opened` quedaban muertos. Además el nombre del
    trigger (`email.brevo.opened`) difiere del event_type de almacén
    (`email.opened`) a propósito."""
    with session_factory() as session:
        with (
            patch(
                "app.workflows.dispatcher.dispatch_event"
            ) as mock_dispatch,
            patch(
                "app.workflows.dispatcher.evaluate_brevo_engagement"
            ) as mock_eval,
        ):
            status = process_brevo_webhook_event(
                session, _event("opened"), account_id="main"
            )
            session.commit()
    assert status == "processed"
    assert mock_dispatch.call_count == 1
    # dispatch_event(session, "email.brevo.opened", contact_id, {...})
    args = mock_dispatch.call_args.args
    assert args[1] == "email.brevo.opened"
    # El engagement compuesto se re-evalúa tras cada apertura.
    mock_eval.assert_called_once()


def test_clicked_event_dispatches_brevo_clicked_trigger(session_factory):
    with session_factory() as session:
        with (
            patch(
                "app.workflows.dispatcher.dispatch_event"
            ) as mock_dispatch,
            patch("app.workflows.dispatcher.evaluate_brevo_engagement"),
        ):
            process_brevo_webhook_event(
                session, _event("click"), account_id="main"
            )
            session.commit()
    assert mock_dispatch.call_args.args[1] == "email.brevo.clicked"


def test_unsubscribe_event_dispatches_contact_unsubscribed(session_factory):
    with session_factory() as session:
        with (
            patch(
                "app.workflows.dispatcher.dispatch_event"
            ) as mock_dispatch,
            patch(
                "app.workflows.dispatcher.evaluate_brevo_engagement"
            ) as mock_eval,
        ):
            process_brevo_webhook_event(
                session, _event("unsubscribe"), account_id="main"
            )
            session.commit()
    assert mock_dispatch.call_args.args[1] == "contact.unsubscribed"
    # unsubscribe no es open/click → no re-evalúa engagement.
    mock_eval.assert_not_called()


def test_delivered_event_does_not_dispatch(session_factory):
    """Eventos sin trigger asociado (delivered/sent/bounce) no despachan."""
    with session_factory() as session:
        with patch(
            "app.workflows.dispatcher.dispatch_event"
        ) as mock_dispatch:
            process_brevo_webhook_event(
                session, _event("delivered"), account_id="main"
            )
            session.commit()
    mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# Un evento repetido no tumba el lote (1.687 fallidos del 23/07 al 07/10)
# ---------------------------------------------------------------------------


def test_un_reenvio_tras_caducar_la_marca_no_revienta(session_factory):
    """La tabla de marcas caduca a los 30 días; `activity_events` no. Un
    reenvío de Brevo más antiguo que eso pasaba el filtro y chocaba con la
    clave única, y ese IntegrityError se llevaba el lote entero."""
    from app.integrations.brevo.webhooks import WebhookEventSeen

    with session_factory() as session:
        assert process_brevo_webhook_event(
            session, _event("soft_bounce"), account_id="main") == "processed"
        session.commit()
        # Se borra la marca, como hace la limpieza de los 30 días.
        session.query(WebhookEventSeen).delete()
        session.commit()
        # El reenvío ahora se reconoce por `activity_events`, no revienta.
        assert process_brevo_webhook_event(
            session, _event("soft_bounce"), account_id="main") == "duplicate"
        session.commit()
        assert len(session.scalars(select(ActivityEvent)).all()) == 1


def test_un_evento_malo_no_se_lleva_los_buenos_del_lote(session_factory, monkeypatch):
    """Lo que se perdió el 07/10 con la campaña francesa: al morir el lote,
    se fueron con él las aperturas y los clics que venían en el mismo."""
    from app.integrations.brevo import webhooks as mod

    lote = [
        _event("delivered", **{"message-id": "<m1@brevo>"}),
        _event("opened", **{"message-id": "<m2@brevo>"}),
        _event("click", **{"message-id": "<malo@brevo>"}, link="x"),
        _event("click", **{"message-id": "<m4@brevo>"}, link="https://a.es"),
        _event("hard_bounce", **{"message-id": "<m5@brevo>"}),
    ]
    real = mod.process_brevo_webhook_event

    def falla_el_tercero(session, event, *, account_id):
        if event.get("message-id") == "<malo@brevo>":
            raise RuntimeError("el evento malo de turno")
        return real(session, event, account_id=account_id)

    monkeypatch.setattr(mod, "process_brevo_webhook_event", falla_el_tercero)
    monkeypatch.setattr(mod, "Session", lambda _engine: session_factory())
    monkeypatch.setattr("app.db.session.get_engine", lambda: None)

    mod.process_brevo_webhook_batch(lote, account_id="main")

    with session_factory() as session:
        claves = {e.external_id for e in session.scalars(select(ActivityEvent))}
    assert len(claves) == 4, claves          # los cuatro buenos entraron
    assert not any("malo" in c for c in claves)


def test_reprocesar_un_lote_ya_procesado_no_duplica(session_factory, monkeypatch):
    from app.integrations.brevo import webhooks as mod

    lote = [_event("delivered", **{"message-id": f"<m{i}@brevo>"}) for i in range(3)]
    monkeypatch.setattr(mod, "Session", lambda _engine: session_factory())
    monkeypatch.setattr("app.db.session.get_engine", lambda: None)

    mod.process_brevo_webhook_batch(lote, account_id="main")
    mod.process_brevo_webhook_batch(lote, account_id="main")      # otra vez

    with session_factory() as session:
        assert len(session.scalars(select(ActivityEvent)).all()) == 3


def test_un_integrityerror_que_no_es_duplicado_no_se_cuenta_como_hecho(
    session_factory, monkeypatch
):
    """Tratar cualquier `IntegrityError` como «duplicado» a nivel INFO perdía
    el evento sin dejar rastro: una clave ajena rota (el contacto borrado a
    media operación) se contaría como procesada."""
    from sqlalchemy.exc import IntegrityError

    with session_factory() as session:
        llamadas = {"n": 0}
        real_flush = session.flush

        def flush_que_revienta_la_segunda(*a, **kw):
            llamadas["n"] += 1
            if llamadas["n"] == 2:
                raise IntegrityError("INSERT", {}, Exception("FK rota"))
            return real_flush(*a, **kw)

        monkeypatch.setattr(session, "flush", flush_que_revienta_la_segunda)
        with pytest.raises(IntegrityError):
            process_brevo_webhook_event(
                session, _event("delivered", **{"message-id": "<fk@brevo>"}),
                account_id="main",
            )


def test_la_carrera_entre_dos_workers_marcando_el_evento_no_tumba_el_lote(
    session_factory,
):
    """Dos entregas del mismo evento a la vez rompen en la clave única de
    `webhook_event_seen`, antes de llegar a `activity_events`. Sin deshacer,
    la sesión quedaba envenenada y se llevaba por delante el resto del lote."""
    from datetime import UTC, datetime

    from app.integrations.brevo.webhooks import mark_event_seen
    from app.models.brevo import WebhookEventSeen

    with session_factory() as a, session_factory() as b:
        # El otro worker se cuela justo entre el SELECT y el INSERT de este.
        carrera = {"hecha": False}
        flush_real = b.flush

        def flush_con_carrera(*args, **kwargs):
            if not carrera["hecha"]:
                carrera["hecha"] = True
                a.add(WebhookEventSeen(system="brevo",
                                       event_key="clave-en-carrera",
                                       seen_at=datetime.now(UTC)))
                a.commit()
            return flush_real(*args, **kwargs)

        b.flush = flush_con_carrera  # type: ignore[method-assign]
        assert mark_event_seen(b, "clave-en-carrera") is False

        # Y la sesión sigue usable: el siguiente evento del lote entra.
        b.flush = flush_real  # type: ignore[method-assign]
        assert mark_event_seen(b, "otra-clave") is True
        assert b.scalar(
            select(WebhookEventSeen).where(
                WebhookEventSeen.event_key == "otra-clave")
        ) is not None
