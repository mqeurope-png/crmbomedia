"""PR-Fix-Sync-Dispara-Reglas-Workflows tests.

Verifica que:

1. Sync periódico (≤ BULK_DISPATCH_THRESHOLD nuevos) dispatcha
   `contact.created` para cada contacto nuevo (motor workflows).
2. Sync periódico aplica reglas de asignación a contactos nuevos.
3. Sync bulk (> threshold) NO dispatcha workflows (mantiene PR #204).
4. El campo `Contact.origin_account_id` se rellena correctamente como
   `{system}:{account_id}` desde el sync.
5. Reglas con `field=origin_account_id value=agilecrm:default` matchean
   (sin esto, `value` y campo divergían y la rule nunca disparaba).
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
    AssignmentRule,
    Base,
    Contact,
    ContactAssignment,
    ExternalSystem,
    SyncLog,
    SyncStatus,
    SyncTrigger,
    User,
    UserRole,
)
from app.models.integration_settings import IntegrationAccount
from app.services.segments.engine import evaluate_contact_against_rules
from tests._test_helpers import seed_test_users


def _make_payload(*, contact_id: int, email: str, first_name: str = "Ana") -> dict[str, Any]:
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

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def list_contacts(
        self, *, page_size: int | None = None, cursor: str | None = None,
        order_by: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not self._pages:
            return [], None
        page = self._pages.pop(0)
        return page, ("next" if self._pages else None)

    async def count_contacts(self) -> int | None:
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


def _new_sync_log(session, *, account_id: str = "default") -> SyncLog:
    sync_log = SyncLog(
        system=ExternalSystem.AGILECRM,
        account_id=account_id,
        operation="sync_contacts",
        status=SyncStatus.RUNNING.value,
        triggered_by=SyncTrigger.MANUAL.value,
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


# ---------------------------------------------------------------------
# 1. Periodic dispatches workflows for new contacts
# ---------------------------------------------------------------------


def test_sync_periodic_dispatches_workflows_for_new_contacts(factory):
    """El sync con 1-50 contactos nuevos llama a `dispatch_event`
    `contact.created` para cada uno post-commit."""
    fake = _FakeClient(
        [
            [
                _make_payload(contact_id=1, email="ana@example.com"),
                _make_payload(contact_id=2, email="bob@example.com"),
            ]
        ]
    )
    with factory() as session, _patch_client(fake), patch(
        "app.workflows.dispatcher.dispatch_event"
    ) as mock_dispatch:
        sync_log = _new_sync_log(session)
        outcome = sync_agilecrm_contacts(session, sync_log)

    assert outcome.records_processed == 2
    assert outcome.metadata["workflows_dispatch_mode"] == "periodic"
    assert outcome.metadata["workflows_dispatched"] == 2
    # 2 llamadas: una por contacto nuevo, con event_type=contact.created.
    event_types = [c.args[1] for c in mock_dispatch.call_args_list]
    assert event_types == ["contact.created", "contact.created"]
    # El payload del dispatch incluye `source=agilecrm` y el account_id.
    payloads = [c.args[3] for c in mock_dispatch.call_args_list]
    assert all(p["source"] == "agilecrm" for p in payloads)
    assert all(p["account_id"] == "default" for p in payloads)


# ---------------------------------------------------------------------
# 2. Periodic applies assignment rules to new contacts
# ---------------------------------------------------------------------


def test_sync_periodic_applies_assignment_rules_for_new_contacts(factory):
    """El sync inline llama a `assignment_rules_engine.evaluate_for_contact`
    para cada nuevo, así una rule cuyo filtro matche queda con el owner
    correcto antes del commit."""
    with factory() as session:
        owner_uid = session.scalar(
            select(User.id).where(User.role == UserRole.USER)
        )
        creator_uid = session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        # Rule that matches when origin_account_id == "agilecrm:default".
        rule = AssignmentRule(
            name="Boprint",
            conditions_json=json.dumps(
                {
                    "operator": "AND",
                    "children": [
                        {
                            "type": "rule",
                            "field": "origin_account_id",
                            "comparator": "eq",
                            "value": "agilecrm:default",
                        }
                    ],
                }
            ),
            primary_user_id=owner_uid,
            priority=100,
            apply_to="unassigned_only",
            stop_on_match=True,
            created_by_user_id=creator_uid,
        )
        session.add(rule)
        session.commit()

    fake = _FakeClient([[_make_payload(contact_id=1, email="ana@example.com")]])
    with factory() as session, _patch_client(fake), patch(
        "app.workflows.dispatcher.dispatch_event"
    ):
        sync_log = _new_sync_log(session)
        sync_agilecrm_contacts(session, sync_log)

    with factory() as session:
        contact = session.scalar(
            select(Contact).where(Contact.email == "ana@example.com")
        )
        assignment = session.scalar(
            select(ContactAssignment).where(
                ContactAssignment.contact_id == contact.id
            )
        )
        assert assignment is not None, (
            "la rule debería haber asignado un owner al contacto nuevo"
        )
        assert assignment.user_id == owner_uid
        assert assignment.is_primary is True


# ---------------------------------------------------------------------
# 3. Bulk import does NOT dispatch workflows
# ---------------------------------------------------------------------


def test_sync_bulk_import_does_not_dispatch_workflows(factory):
    """Con > BULK_DISPATCH_THRESHOLD contactos nuevos, el dispatch se
    omite (PR #204) y el metadata lo marca como `bulk`."""
    count = BULK_DISPATCH_THRESHOLD + 5
    page = [
        _make_payload(contact_id=i, email=f"user{i}@example.com")
        for i in range(1, count + 1)
    ]
    fake = _FakeClient([page])
    with factory() as session, _patch_client(fake), patch(
        "app.workflows.dispatcher.dispatch_event"
    ) as mock_dispatch:
        sync_log = _new_sync_log(session)
        outcome = sync_agilecrm_contacts(session, sync_log)

    assert outcome.records_processed == count
    assert outcome.metadata["workflows_dispatch_mode"] == "bulk"
    assert outcome.metadata["workflows_dispatched"] == 0
    assert mock_dispatch.call_count == 0


# ---------------------------------------------------------------------
# 4. origin_account_id is resolved correctly from sync
# ---------------------------------------------------------------------


def test_origin_account_id_resolved_from_sync_correctly(factory):
    """Cada contacto sincronizado vía AgileCRM tiene `origin_account_id`
    seteado a `agilecrm:{account_id}` (el formato que las rules ya
    usaban: `value="agilecrm:default"`)."""
    fake = _FakeClient([[_make_payload(contact_id=1, email="ana@example.com")]])
    with factory() as session, _patch_client(fake), patch(
        "app.workflows.dispatcher.dispatch_event"
    ):
        sync_log = _new_sync_log(session)
        sync_agilecrm_contacts(session, sync_log)

    with factory() as session:
        contact = session.scalar(
            select(Contact).where(Contact.email == "ana@example.com")
        )
        assert contact.origin_account_id == "agilecrm:default"


# ---------------------------------------------------------------------
# 5. Assignment-rule evaluator matches origin_account_id
# ---------------------------------------------------------------------


def test_assignment_rule_matches_origin_with_account_id(factory):
    """Verificación pura del evaluador in-memory:
    `evaluate_contact_against_rules` con
    `field=origin_account_id value=agilecrm:default` debe devolver
    True cuando `Contact.origin_account_id == "agilecrm:default"`.
    Antes del fix devolvía el `account_id` "default" desde
    external_refs y comparaba con "agilecrm:default" → False."""
    with factory() as session:
        contact = Contact(
            first_name="Ana",
            email="ana@example.com",
            origin="agilecrm",
            origin_account_id="agilecrm:default",
        )
        session.add(contact)
        session.commit()
        # Reload con sesión limpia para forzar a que el evaluador lea
        # de la columna y no de un atributo transient.
        contact = session.get(Contact, contact.id)

        tree = {
            "operator": "AND",
            "children": [
                {
                    "type": "rule",
                    "field": "origin_account_id",
                    "comparator": "eq",
                    "value": "agilecrm:default",
                }
            ],
        }
        assert evaluate_contact_against_rules(contact, tree) is True

        # Negative case: rule for `agilecrm:boprint` should NOT match.
        tree_negative = {
            "operator": "AND",
            "children": [
                {
                    "type": "rule",
                    "field": "origin_account_id",
                    "comparator": "eq",
                    "value": "agilecrm:boprint",
                }
            ],
        }
        assert evaluate_contact_against_rules(contact, tree_negative) is False
