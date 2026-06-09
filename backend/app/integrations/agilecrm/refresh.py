"""On-demand AgileCRM refresh for a single contact.

Drives `POST /api/contacts/{id}/refresh-external-data`. For every
`external_references` row attached to the contact that points at an
AgileCRM account, it:

1. Opens an `AgileCRMClient` for that account.
2. Fetches notes / tasks / events for the AgileCRM contact id (bounded
   by the same `MAX_SUBSYNC_CONCURRENCY` semaphore the bulk sync used
   to use, now per-call).
3. Upserts the payloads into `notes` / `tasks` / `activity_events` via
   the existing `_sync_contact_*` helpers in `jobs.py` — so the
   dedup-by-external-id story stays identical to the (retired) bulk
   path.

Soft-fails: 429 and 401 are surfaced as warnings on the response
rather than aborting the whole refresh. Per-system error isolation
means a saturated AgileCRM account never blocks a future Brevo /
Freshdesk refresh — the same endpoint will fan out to them too once
those connectors land.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.integrations.agilecrm.client import AgileCRMClient
from app.integrations.agilecrm.jobs import (
    MAX_SUBSYNC_CONCURRENCY,
    _sync_contact_events,
    _sync_contact_notes,
    _sync_contact_tasks,
)
from app.integrations.errors import (
    IntegrationAuthError,
    IntegrationRateLimitError,
)

if TYPE_CHECKING:
    from app.models.crm import Contact, ExternalReference, User

logger = logging.getLogger(__name__)


@dataclass
class RefreshResult:
    """Plain DTO the API route turns into JSON. Counters are zeroed
    when a system soft-fails (rate-limited / auth-error), so the
    operator can still see which sources were tried and which were
    cached only."""

    refreshed_at: datetime
    sources_refreshed: list[str] = field(default_factory=list)
    notes_count: int = 0
    tasks_count: int = 0
    events_count: int = 0
    warnings: list[str] = field(default_factory=list)
    status: str = "ok"  # ok | partial


async def refresh_contact_external_data(
    session: Session,
    *,
    contact: Contact,
    actor: User | None = None,
) -> RefreshResult:
    """Refresh the cached AgileCRM data for one contact. Idempotent.

    The function returns a structured result even when no source
    succeeded — the UI uses it to decide whether to show the cached
    rows with a "rate-limited" banner or with a "we're up to date"
    badge.
    """
    refreshed_at = datetime.now(UTC)
    result = RefreshResult(refreshed_at=refreshed_at)

    refs: list[ExternalReference] = list(contact.external_refs)
    for ref in refs:
        # `system` is the SQLAlchemy enum; compare via `.value` so we
        # stay independent of the Python member name.
        if ref.system.value != "agilecrm":
            # Other connectors (Brevo, Freshdesk) plug in here as they
            # land. Ignoring unknown systems means a multi-account
            # contact can still get a partial refresh from AgileCRM
            # alone.
            continue
        if ref.external_status == "deleted_in_origin":
            continue
        await _refresh_agilecrm_account(
            session,
            contact=contact,
            reference=ref,
            result=result,
        )

    contact.external_data_refreshed_at = refreshed_at
    session.flush()

    record_event(
        session,
        action=Action.EXTERNAL_REFRESH_REQUESTED,
        target_type="contact",
        target_id=contact.id,
        actor=actor,
        metadata={
            "sources_refreshed": list(result.sources_refreshed),
            "notes_count": result.notes_count,
            "tasks_count": result.tasks_count,
            "events_count": result.events_count,
            "status": result.status,
            "warnings": list(result.warnings),
        },
    )
    session.commit()
    return result


async def _refresh_agilecrm_account(
    session: Session,
    *,
    contact: Contact,
    reference: ExternalReference,
    result: RefreshResult,
) -> None:
    """Refresh one AgileCRM account's payload for the contact.

    All exception handling lives here so the outer loop can keep
    iterating across the remaining `external_references` rows even
    when one of them returns 429 / 401.
    """
    source = f"agilecrm:{reference.account_id}"
    semaphore = asyncio.Semaphore(MAX_SUBSYNC_CONCURRENCY)
    try:
        async with AgileCRMClient(session, reference.account_id) as client:
            note_payloads, task_payloads, event_payloads = await _fetch_three(
                client, reference.external_id, semaphore
            )
    except IntegrationRateLimitError as exc:
        result.status = "partial"
        result.warnings.append(
            f"Rate limit on {source}. Showing cached data."
        )
        record_event(
            session,
            action=Action.EXTERNAL_REFRESH_RATE_LIMITED,
            target_type="contact",
            target_id=contact.id,
            metadata={
                "system": "agilecrm",
                "account_id": reference.account_id,
                "retry_after_seconds": getattr(exc, "retry_after_seconds", None),
            },
        )
        return
    except IntegrationAuthError as exc:
        # The base http client already flipped credential_status to
        # 'error' and committed an INTEGRATION_AUTH_FAILED audit row;
        # we add a refresh-level audit so the operator sees the
        # connection between "I clicked refresh" and "the credential
        # is now broken".
        result.status = "partial"
        result.warnings.append(
            f"Authentication failed for {source}. Showing cached data."
        )
        record_event(
            session,
            action=Action.EXTERNAL_REFRESH_AUTH_ERROR,
            target_type="contact",
            target_id=contact.id,
            metadata={
                "system": "agilecrm",
                "account_id": reference.account_id,
                "status_code": getattr(exc, "status_code", None),
            },
        )
        return
    except Exception as exc:  # noqa: BLE001 - never fail the whole refresh
        logger.warning(
            "external_refresh.unexpected_error system=agilecrm account_id=%s "
            "contact_id=%s: %s",
            reference.account_id,
            contact.id,
            exc,
        )
        result.status = "partial"
        result.warnings.append(f"Could not refresh {source}.")
        return

    try:
        result.notes_count += _sync_contact_notes(
            session,
            contact_id=contact.id,
            account_id=reference.account_id,
            payloads=note_payloads,
        )
        result.tasks_count += _sync_contact_tasks(
            session,
            contact_id=contact.id,
            account_id=reference.account_id,
            payloads=task_payloads,
        )
        result.events_count += _sync_contact_events(
            session,
            contact_id=contact.id,
            account_id=reference.account_id,
            payloads=event_payloads,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "external_refresh.persist_error system=agilecrm account_id=%s "
            "contact_id=%s: %s",
            reference.account_id,
            contact.id,
            exc,
        )
        result.status = "partial"
        result.warnings.append(
            f"Could not persist refreshed data from {source}."
        )
        session.rollback()
        return

    result.sources_refreshed.append(source)


async def _fetch_three(
    client: AgileCRMClient,
    agilecrm_contact_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[list, list, list]:
    """Fan out notes / tasks / events under the semaphore. Unlike the
    `_fetch_subresources` helper in `jobs.py` we DO NOT swallow
    exceptions — the caller wants to react differently to a 429 vs a
    401 vs a transient 5xx, so we let them bubble. Only the FIRST
    failing call propagates (asyncio.gather stops on the first
    exception when `return_exceptions=False`)."""

    async def _guarded(fetcher: Any) -> list:
        async with semaphore:
            return await fetcher(agilecrm_contact_id)

    return await asyncio.gather(  # type: ignore[return-value]
        _guarded(client.list_contact_notes),
        _guarded(client.list_contact_tasks),
        _guarded(client.list_contact_events),
    )
