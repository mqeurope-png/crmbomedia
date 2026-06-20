"""PR-Consolidado — Fix dispatcher sync. Tests end-to-end REALES.

Los tests del PR #221 mockaban `dispatch_event` y verificaban
`mock.call_count`. Pasaban. Pero en prod la cadena se rompía en el
RQ enqueue (string reference rota) y nadie lo veía hasta que Bart
comprobó `workflow_runs` y vio 0 rows.

Estos tests son **end-to-end reales**: invocan el handler `sync_agilecrm_contacts`
de verdad, dejan que `dispatch_event` corra su lógica completa (intentar
encolar a Redis → fallar porque no hay Redis en el test → caer al
`process_event_inline`), y asertan sobre `workflow_runs` directamente.

Si la cadena se rompe en cualquier eslabón, NO se crea workflow_run y
el test falla.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.integrations.agilecrm.jobs import (
    BULK_DISPATCH_THRESHOLD,
    sync_agilecrm_contacts,
)
from app.models.crm import (
    Base,
    ExternalSystem,
    SyncLog,
    SyncStatus,
    SyncTrigger,
)
from app.models.integration_settings import IntegrationAccount
from app.models.workflows import (
    Workflow,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from tests._test_helpers import seed_test_users

# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


def _make_payload(*, contact_id: int, first_name: str, email: str) -> dict[str, Any]:
    return {
        "id": contact_id,
        "tags": [],
        "properties": [
            {"name": "first_name", "value": first_name},
            {"name": "email", "value": email},
        ],
    }


class _FakeClient:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self._pages = list(pages)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def list_contacts(self, *, page_size=None, cursor=None, order_by=None):
        if not self._pages:
            return [], None
        page = self._pages.pop(0)
        return page, ("next" if self._pages else None)

    async def count_contacts(self):
        return None


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
                system=ExternalSystem.AGILECRM,
                account_id="default",
                display_name="AgileCRM default",
                enabled=True,
                credential_status="configured",
                api_key_encrypted=crypto.encrypt("ops@example.com:secret"),
            )
        )
        seed.commit()
    yield sf
    Base.metadata.drop_all(engine)


def _seed_workflow_matching_testt(session, *, trigger_config: dict | None = None) -> str:
    """Workflow ACTIVE con trigger=contact.created. El filtro del
    trigger es opcional — por defecto no filtra (cualquier contacto
    creado entra)."""
    wf = Workflow(
        name="bbbb",
        status=WorkflowStatus.ACTIVE,
        trigger_type="contact.created",
        trigger_config_json=json.dumps(trigger_config or {}),
    )
    session.add(wf)
    session.flush()
    # Entry step: un exit_natural mínimo. El motor exige UN step con
    # is_entry=True para que `start_run` no devuelva None.
    step = WorkflowStep(
        workflow_id=wf.id,
        type="exit_natural",
        config_json="{}",
        is_entry=True,
        position_x=0.0,
        position_y=0.0,
    )
    session.add(step)
    session.commit()
    return wf.id


def _new_sync_log(session, *, account_id: str = "default") -> SyncLog:
    sync_log = SyncLog(
        system=ExternalSystem.AGILECRM,
        account_id=account_id,
        operation="sync_contacts",
        status=SyncStatus.RUNNING.value,
        triggered_by=SyncTrigger.CRON.value,  # simula sync periódico
    )
    session.add(sync_log)
    session.flush()
    return sync_log


def _patch_client(fake: _FakeClient):
    @asynccontextmanager
    async def fake_ctx(_session, _account_id):
        async with fake:
            yield fake

    return patch(
        "app.integrations.agilecrm.jobs.AgileCRMClient",
        side_effect=lambda session, account_id: fake,
    )


def _patch_redis_unavailable():
    """Fuerza el fallback a `process_event_inline` desactivando el
    enqueue de Redis. `dispatch_event` importa `rq.Queue` lazy
    dentro de la función, así que parcheamos el método
    `Queue.enqueue` en el módulo `rq`.

    Esto reproduce el comportamiento de prod cuando Redis está
    operativo pero el job se pierde (string reference rota), y
    también el de CI sin Redis. En cualquiera de los dos escenarios
    el fallback inline corre `process_event_inline` y crea
    workflow_runs en la misma sesión — exactamente lo que el e2e
    test mide."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    def boom(self, *args, **kwargs):
        raise RedisConnectionError("test-mode: no Redis available")

    return patch("rq.Queue.enqueue", boom)


# ---------------------------------------------------------------------
# 1. Sync periódico DEBE crear workflow_runs reales
# ---------------------------------------------------------------------


def test_sync_periodic_dispatches_workflow_for_new_contact_via_agile_handler(
    factory: sessionmaker,
):
    """El test que faltaba: invocar `sync_agilecrm_contacts` de verdad
    y comprobar que `workflow_runs` se popula. Si el dispatcher está
    roto (string reference, import side-effect), NO se crea workflow_run
    y el test falla — exactamente como falló en prod tras PR #221."""
    with factory() as session:
        workflow_id = _seed_workflow_matching_testt(session)

    fake = _FakeClient(
        [[_make_payload(contact_id=1, first_name="TESTT 88", email="t88@example.com")]]
    )
    with factory() as session, _patch_client(fake), _patch_redis_unavailable():
        sync_log = _new_sync_log(session)
        sync_agilecrm_contacts(session, sync_log)

    with factory() as session:
        runs = list(
            session.scalars(
                select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)
            )
        )
        assert len(runs) == 1, (
            "El sync periódico debe crear un workflow_run para el "
            "contacto recién insertado. Si esto está en 0, la cadena "
            "dispatcher (encolar → fallback inline → start_run) se "
            "rompió igual que en prod tras PR #221."
        )
        # Y el workflow.total_entered debe haber subido (lo escribe
        # `start_run` al avanzar el motor).
        wf = session.get(Workflow, workflow_id)
        assert wf.total_entered >= 1


# ---------------------------------------------------------------------
# 2. Below threshold ⇒ periodic mode ⇒ dispatch
# ---------------------------------------------------------------------


def test_sync_periodic_below_threshold_dispatches(factory: sessionmaker):
    """Threshold = 50. Con 3 contactos nuevos el modo es periodic y
    se crean 3 workflow_runs."""
    with factory() as session:
        workflow_id = _seed_workflow_matching_testt(session)

    payloads = [
        _make_payload(contact_id=i, first_name=f"TESTT {i}", email=f"t{i}@example.com")
        for i in range(1, 4)
    ]
    fake = _FakeClient([payloads])
    with factory() as session, _patch_client(fake), _patch_redis_unavailable():
        sync_log = _new_sync_log(session)
        outcome = sync_agilecrm_contacts(session, sync_log)

    assert outcome.metadata["workflows_dispatch_mode"] == "periodic"
    assert outcome.metadata["workflows_dispatched"] == 3
    with factory() as session:
        runs = list(
            session.scalars(
                select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)
            )
        )
        assert len(runs) == 3


# ---------------------------------------------------------------------
# 3. Above threshold ⇒ bulk mode ⇒ NO dispatch
# ---------------------------------------------------------------------


def test_sync_bulk_above_threshold_does_not_dispatch(factory: sessionmaker):
    """Threshold = 50. Con 55 contactos nuevos el modo es bulk y
    NO se crea ningún workflow_run."""
    with factory() as session:
        workflow_id = _seed_workflow_matching_testt(session)

    count = BULK_DISPATCH_THRESHOLD + 5
    payloads = [
        _make_payload(contact_id=i, first_name=f"Bulk {i}", email=f"b{i}@example.com")
        for i in range(1, count + 1)
    ]
    fake = _FakeClient([payloads])
    with factory() as session, _patch_client(fake), _patch_redis_unavailable():
        sync_log = _new_sync_log(session)
        outcome = sync_agilecrm_contacts(session, sync_log)

    assert outcome.metadata["workflows_dispatch_mode"] == "bulk"
    assert outcome.metadata["workflows_dispatched"] == 0
    with factory() as session:
        runs = list(
            session.scalars(
                select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)
            )
        )
        assert runs == []


# ---------------------------------------------------------------------
# 4. Logging visible: las líneas del fix de observabilidad están ahí
# ---------------------------------------------------------------------


def test_sync_dispatch_logs_visible_messages(factory: sessionmaker):
    """Bart pasó 90 min sin saber si el sync había llegado al dispatch
    porque NO había una sola línea de log. Estos mensajes son la
    diferencia entre debuggear con SQL y leer el log.

    Mockamos el logger del módulo directamente — `caplog` de pytest
    es flaky en CI py3.12 (captura vacía pese a configurar
    `set_level`). El mock no depende de cómo pytest interactúa con
    el handler raíz."""
    with factory() as session:
        _seed_workflow_matching_testt(session)

    fake = _FakeClient(
        [[_make_payload(contact_id=1, first_name="TESTT log", email="log@example.com")]]
    )
    with (
        factory() as session,
        _patch_client(fake),
        _patch_redis_unavailable(),
        patch("app.integrations.agilecrm.jobs.logger") as mock_logger,
    ):
        sync_log = _new_sync_log(session)
        sync_agilecrm_contacts(session, sync_log)

    # Renderizamos los format-strings tal como los emitiría el logger
    # (printf-style %s) para poder hacer substring checks.
    rendered: list[str] = []
    for call in mock_logger.info.call_args_list:
        args = call.args
        if not args:
            continue
        template = str(args[0])
        try:
            rendered.append(template % args[1:])
        except TypeError:
            rendered.append(template)
    joined = "\n".join(rendered)

    # Mensajes que deben estar SIEMPRE (independientemente de modo).
    assert "collected" in joined and "new_contacts" in joined, (
        f"falta la línea 'collected N new_contacts'; rendered={rendered!r}"
    )
    assert "bulk gate" in joined and "dispatch_mode=periodic" in joined, (
        f"falta 'bulk gate dispatch_mode=periodic'; rendered={rendered!r}"
    )
    # Mensaje por-contacto.
    assert "dispatching contact.created" in joined, (
        f"falta 'dispatching contact.created'; rendered={rendered!r}"
    )
    # Resumen final.
    assert "dispatched 1/1 contact.created" in joined, (
        f"falta resumen 'dispatched K/N contact.created'; rendered={rendered!r}"
    )


# ---------------------------------------------------------------------
# 5. Backfill manual endpoint
# ---------------------------------------------------------------------


def test_replay_contact_created_endpoint_dispatches_for_existing_contact(
    factory: sessionmaker,
):
    """`POST /api/admin/workflows/replay-contact-created` con una
    lista de UUIDs crea workflow_runs para cada uno (modo backfill
    explícito para reproducir contactos del bug PR #221)."""
    from fastapi.testclient import TestClient

    from app.db.session import get_session as _get_session
    from app.main import app as _app
    from app.models.crm import Contact

    def override():
        with factory() as session:
            yield session

    _app.dependency_overrides[_get_session] = override
    try:
        with factory() as session:
            workflow_id = _seed_workflow_matching_testt(session)
            # Seed los 2 contactos como existirían en prod tras PR #221.
            c1 = Contact(first_name="TESTT 88", email="t88@example.com")
            c2 = Contact(first_name="TESTT 99", email="t99@example.com")
            session.add_all([c1, c2])
            session.commit()
            c1_id, c2_id = c1.id, c2.id

        from tests._test_helpers import auth_headers

        with TestClient(_app) as client, _patch_redis_unavailable():
            headers = auth_headers(client, "admin")
            resp = client.post(
                "/api/admin/workflows/replay-contact-created",
                headers=headers,
                json={"contact_ids": [c1_id, c2_id]},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert set(body["dispatched"]) == {c1_id, c2_id}
        assert body["failures"] == []

        with factory() as session:
            runs = list(
                session.scalars(
                    select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)
                )
            )
            assert len(runs) == 2
    finally:
        _app.dependency_overrides.clear()


def test_replay_contact_created_endpoint_rejects_empty_payload(
    factory: sessionmaker,
):
    from fastapi.testclient import TestClient

    from app.db.session import get_session as _get_session
    from app.main import app as _app

    def override():
        with factory() as session:
            yield session

    _app.dependency_overrides[_get_session] = override
    try:
        from tests._test_helpers import auth_headers

        with TestClient(_app) as client:
            headers = auth_headers(client, "admin")
            resp = client.post(
                "/api/admin/workflows/replay-contact-created",
                headers=headers,
                json={"contact_ids": []},
            )
        assert resp.status_code == 400
    finally:
        _app.dependency_overrides.clear()
