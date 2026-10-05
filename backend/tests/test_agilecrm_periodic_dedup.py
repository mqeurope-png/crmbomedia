"""PR-Fix-Web-Ingest-Starvation — el heartbeat de AgileCRM no apila syncs.

`periodic_read_check` encola un `sync_contacts` por cuenta cada hora, pero
SALTA las cuentas que ya tienen uno pendiente o en curso. Con syncs que duran
más que el intervalo, apilar 9 jobs más cada hora era lo que tenía a AgileCRM
«en bucle» y copando el worker — la causa de que la ingesta web se atascara.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401  — registra los modelos en Base.metadata
from app.db.base import Base
from app.integrations.agilecrm import scheduler
from app.models.crm import ExternalSystem, SyncLog, SyncStatus
from app.models.integration_settings import IntegrationAccount


@pytest.fixture()
def session(monkeypatch) -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    # El re-arm del heartbeat toca Redis: no-op en tests.
    monkeypatch.setattr(scheduler, "schedule_periodic_read", lambda: None)
    with sf() as s:
        yield s
    Base.metadata.drop_all(engine)


def _account(session: Session, account_id: str, *, enabled: bool = True) -> None:
    session.add(IntegrationAccount(
        system=ExternalSystem.AGILECRM, account_id=account_id,
        display_name=account_id, enabled=enabled, credential_status="configured",
    ))
    session.commit()


def _sync_log(session: Session, account_id: str, status: str, *,
              hace: timedelta | None = None) -> None:
    """Fila de `sync_contacts`; `hace` = cuánto hace que empezó (running) o se
    encoló (pending)."""
    inicio = datetime.now(UTC) - hace if hace is not None else None
    session.add(SyncLog(
        system=ExternalSystem.AGILECRM, account_id=account_id,
        operation="sync_contacts", status=status,
        **({"created_at": inicio} if inicio else {}),
        **({"started_at": inicio} if inicio and status == SyncStatus.RUNNING.value else {}),
    ))
    session.commit()


def _record_enqueues(monkeypatch) -> list[str]:
    calls: list[str] = []

    def _fake(session, **kwargs):  # noqa: ANN001, ANN003
        calls.append(kwargs["account_id"])
        return ("sync-log-id", "job-id")

    monkeypatch.setattr(scheduler, "enqueue_sync_job", _fake)
    return calls


def _fire(session: Session) -> SyncLog:
    fake_log = SyncLog(
        system=ExternalSystem.AGILECRM, operation="periodic_read",
        status=SyncStatus.RUNNING.value,
    )
    return scheduler.periodic_read_check(session, fake_log)


def test_enqueues_accounts_without_inflight_sync(session, monkeypatch) -> None:
    _account(session, "es")
    _account(session, "uk")
    calls = _record_enqueues(monkeypatch)

    outcome = _fire(session)

    assert sorted(calls) == ["es", "uk"]
    assert outcome.records_processed == 2
    assert outcome.metadata["skipped_inflight"] == 0


def test_skips_account_with_inflight_sync(session, monkeypatch) -> None:
    _account(session, "es")
    _account(session, "uk")
    _sync_log(session, "es", SyncStatus.RUNNING.value)   # es ya corriendo
    _sync_log(session, "uk", SyncStatus.PENDING.value)   # uk ya encolado
    calls = _record_enqueues(monkeypatch)

    outcome = _fire(session)

    # Ninguno se re-encola: no se apila un 2º sync sobre el que aún corre.
    assert calls == []
    assert outcome.records_processed == 0
    assert outcome.metadata["skipped_inflight"] == 2


def test_reenqueues_only_the_idle_account(session, monkeypatch) -> None:
    _account(session, "es")
    _account(session, "uk")
    _sync_log(session, "es", SyncStatus.RUNNING.value)   # es ocupado → saltar
    _sync_log(session, "uk", SyncStatus.SUCCESS.value)   # uk terminado → sí
    calls = _record_enqueues(monkeypatch)

    _fire(session)

    assert calls == ["uk"]


# --- el «en curso» caduca (incidencia del 05/10/2026: 18 días sin sincronizar) ---


def test_un_en_curso_viejo_ya_no_bloquea_la_cuenta(session, monkeypatch) -> None:
    """Una fila que se quedó en running/pending porque su worker murió (las de
    agosto) no cuenta como en curso pasados 60 min: se encola otro sync."""
    monkeypatch.delenv("SYNC_INFLIGHT_MAX_MINUTES", raising=False)
    _account(session, "es")
    _account(session, "uk")
    _account(session, "fr")
    _sync_log(session, "es", SyncStatus.RUNNING.value, hace=timedelta(days=40))
    _sync_log(session, "uk", SyncStatus.PENDING.value, hace=timedelta(hours=2))
    _sync_log(session, "fr", SyncStatus.RUNNING.value, hace=timedelta(minutes=10))
    calls = _record_enqueues(monkeypatch)

    outcome = _fire(session)

    assert sorted(calls) == ["es", "uk"]                  # fr sí está en curso de verdad
    assert outcome.metadata["skipped_inflight"] == 1


def test_minutos_de_caducidad_configurables(session, monkeypatch) -> None:
    monkeypatch.setenv("SYNC_INFLIGHT_MAX_MINUTES", "180")
    _account(session, "es")
    _sync_log(session, "es", SyncStatus.RUNNING.value, hace=timedelta(hours=2))
    calls = _record_enqueues(monkeypatch)

    _fire(session)

    assert calls == []                                    # 2 h < 180 min: sigue en curso
