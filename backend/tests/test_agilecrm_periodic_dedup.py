"""PR-Fix-Web-Ingest-Starvation — el heartbeat de AgileCRM no apila syncs.

`periodic_read_check` encola un `sync_contacts` por cuenta cada hora, pero
SALTA las cuentas que ya tienen uno pendiente o en curso. Con syncs que duran
más que el intervalo, apilar 9 jobs más cada hora era lo que tenía a AgileCRM
«en bucle» y copando el worker — la causa de que la ingesta web se atascara.
"""
from __future__ import annotations

from collections.abc import Generator

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


def _sync_log(session: Session, account_id: str, status: str) -> None:
    session.add(SyncLog(
        system=ExternalSystem.AGILECRM, account_id=account_id,
        operation="sync_contacts", status=status,
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
