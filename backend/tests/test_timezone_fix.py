"""PR-Timezone-Fix.

Bart reportó "sync que termina AHORA muestra 'hace 2 horas'" en
Madrid (verano UTC+2). Causa: SQLAlchemy + MySQL/SQLite con
`DateTime(timezone=True)` devuelven datetime NAIVE al cargar la fila.
Pydantic serializa entonces sin offset (`2026-06-18T09:08:51`), y el
navegador interpreta esa cadena como hora LOCAL, restando 2 h al
diff `Date.now() - target`.

Estos tests cubren el contrato roto antes del fix:

1. INSERT con `datetime.now(UTC)` → SELECT → debe devolver datetime
   AWARE con `tzinfo=UTC`.
2. Pydantic `model_dump_json` de la fila → debe incluir `Z` o
   `+00:00` (cualquier offset positivo / negativo cuenta — no naive).
3. Round-trip cross-session (insert en sesión A, leer en sesión B)
   mantiene aware.
4. Refresh tras expire mantiene aware.
"""
from __future__ import annotations

import re
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.crm import Base, ExternalSystem, SyncLog

HAS_OFFSET_RE = re.compile(r"[Zz]|[+-]\d{2}:?\d{2}$")


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)


def _seed_sync_log(factory: sessionmaker) -> str:
    with factory() as session:
        row = SyncLog(
            system=ExternalSystem.AGILECRM,
            account_id="acme",
            operation="sync_contacts",
            status="success",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(row)
        session.commit()
        return row.id


def test_load_attaches_utc_to_naive_datetime(
    session_factory: sessionmaker,
) -> None:
    row_id = _seed_sync_log(session_factory)
    with session_factory() as session:
        fresh = session.get(SyncLog, row_id)
        assert fresh is not None
        assert fresh.finished_at is not None
        assert fresh.finished_at.tzinfo is UTC


def test_refresh_after_expire_keeps_utc(
    session_factory: sessionmaker,
) -> None:
    """`expire_all` + `get` pasa por `refresh`, no por `load`. El fix
    listen() sobre ambos para cubrir este path."""
    row_id = _seed_sync_log(session_factory)
    with session_factory() as session:
        row = session.get(SyncLog, row_id)
        session.expire_all()
        # Re-acceder al atributo dispara refresh
        assert row.finished_at.tzinfo is UTC


def test_pydantic_serializes_with_offset(
    session_factory: sessionmaker,
) -> None:
    """El smoking gun del bug: sin tzinfo, Pydantic emite
    `2026-06-18T09:00:00` (naive) y el navegador resta el offset
    local. Con el fix, emite `+00:00` o `Z`."""
    from pydantic import BaseModel, ConfigDict

    class _Read(BaseModel):
        model_config = ConfigDict(from_attributes=True)
        finished_at: datetime | None

    row_id = _seed_sync_log(session_factory)
    with session_factory() as session:
        fresh = session.get(SyncLog, row_id)
        payload = _Read.model_validate(fresh).model_dump_json()
    assert HAS_OFFSET_RE.search(payload) is not None, (
        f"Pydantic emitió datetime sin offset; el navegador lo "
        f"interpretará como hora local: {payload}"
    )


def test_aware_datetime_passthrough_unmodified(
    session_factory: sessionmaker,
) -> None:
    """Una columna que YA viene aware (poco común con MySQL pero
    posible con dialectos modernos) no debe perder precisión ni
    cambiar de zona horaria. El fix solo actúa cuando `tzinfo is None`.
    """
    fixed = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session:
        row = SyncLog(
            system=ExternalSystem.AGILECRM,
            account_id="acme",
            operation="sync_contacts",
            status="success",
            started_at=fixed,
            finished_at=fixed,
        )
        session.add(row)
        session.commit()
        row_id = row.id
    with session_factory() as session:
        fresh = session.get(SyncLog, row_id)
        assert fresh.finished_at == fixed
        assert fresh.finished_at.tzinfo is UTC
