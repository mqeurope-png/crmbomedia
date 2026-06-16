"""Sprint Reglas-Assign — PR-Da hotfix tests.

Bug 2: sync de cuenta deshabilitada o no configurada no debe marcar el
SyncLog como FAILED — el operador la deshabilitó adrede. Mapea a
SyncStatus.SKIPPED (verde gris en UI, no aparece como error).
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.integrations.errors import IntegrationSkipped
from app.models.crm import (
    Base,
    ExternalSystem,
    SyncLog,
    SyncStatus,
)
from app.models.integration_settings import IntegrationAccount


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    Base.metadata.drop_all(engine)


def _seed_account(
    session: Session,
    *,
    system: ExternalSystem,
    enabled: bool,
    status: str = "configured",
) -> IntegrationAccount:
    acc = IntegrationAccount(
        system=system,
        account_id=f"acc-{system.value}",
        display_name="Acc",
        account_label="Acc",
        enabled=enabled,
        credential_status=status,
    )
    session.add(acc)
    session.flush()
    return acc


def test_agile_load_account_disabled_raises_skipped(
    session_factory: sessionmaker,
) -> None:
    from app.integrations.agilecrm.jobs import _load_account  # noqa: PLC0415

    with session_factory() as session:
        acc = _seed_account(
            session, system=ExternalSystem.AGILECRM, enabled=False
        )
        with pytest.raises(IntegrationSkipped) as exc_info:
            _load_account(session, acc.account_id)
    assert "disabled" in str(exc_info.value)


def test_agile_load_account_not_configured_raises_skipped(
    session_factory: sessionmaker,
) -> None:
    from app.integrations.agilecrm.jobs import _load_account  # noqa: PLC0415

    with session_factory() as session:
        acc = _seed_account(
            session, system=ExternalSystem.AGILECRM, enabled=True, status="error"
        )
        with pytest.raises(IntegrationSkipped):
            _load_account(session, acc.account_id)


def test_brevo_load_account_disabled_raises_skipped(
    session_factory: sessionmaker,
) -> None:
    from app.integrations.brevo.jobs import _load_account  # noqa: PLC0415

    with session_factory() as session:
        # PR-Da hotfix: Brevo no chequea credential_status (lo valida
        # cada handler), solo `enabled`. Esto refleja el comportamiento
        # legacy y no rompe la suite de Brevo existente.
        acc = _seed_account(
            session, system=ExternalSystem.BREVO, enabled=False
        )
        with pytest.raises(IntegrationSkipped):
            _load_account(session, acc.account_id)


# -- worker wrapper maps IntegrationSkipped → SyncStatus.SKIPPED ----


def test_worker_maps_skipped_exception_to_skipped_status(
    monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker
) -> None:
    """El handler levanta IntegrationSkipped; el wrapper en
    `app.workers.jobs.run_sync_log` debe persistir SyncStatus.SKIPPED
    en el SyncLog (no FAILED), y emitir audit
    Action.INTEGRATION_SYNC_SKIPPED."""
    from app.models.crm import AuditLog  # noqa: PLC0415
    from app.workers import jobs as workers_jobs  # noqa: PLC0415

    # 1. Registramos un handler de prueba que siempre lanza Skipped.
    def _fake_handler(session: Session, sync_log: SyncLog) -> Any:
        raise IntegrationSkipped(
            f"{sync_log.system} account '{sync_log.account_id}' is disabled",
            system=sync_log.system,
            account_id=sync_log.account_id,
        )

    monkeypatch.setitem(
        workers_jobs.OPERATIONS, "agilecrm:test_op", _fake_handler
    )

    # 2. Engine override → la sesión real del worker apunta a SQLite.
    engine = session_factory().bind  # type: ignore[union-attr]
    monkeypatch.setattr(workers_jobs, "get_engine", lambda: engine)

    # 3. Seed sync_log PENDING.
    with session_factory() as session:
        sync_log = SyncLog(
            system=ExternalSystem.AGILECRM,
            account_id="acc-agilecrm",
            operation="test_op",
            status=SyncStatus.PENDING.value,
        )
        session.add(sync_log)
        session.commit()
        sync_log_id = sync_log.id

    # 4. Run wrapper.
    workers_jobs.run_sync_job(
        sync_log_id, "agilecrm", "acc-agilecrm", "test_op"
    )

    # 5. SyncLog en SKIPPED + audit row con INTEGRATION_SYNC_SKIPPED.
    with session_factory() as session:
        after = session.get(SyncLog, sync_log_id)
        assert after.status == SyncStatus.SKIPPED.value
        assert "disabled" in (after.error_summary or "")
        actions = list(
            session.scalars(
                select(AuditLog.action).where(AuditLog.target_id == sync_log_id)
            )
        )
        assert "integration.sync_skipped" in actions
        assert "integration.sync_failed" not in actions
