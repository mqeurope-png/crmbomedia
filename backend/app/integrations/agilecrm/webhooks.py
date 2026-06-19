"""AgileCRM webhook worker.

Reads a `webhook_events` row, parses the AgileCRM payload, applies
upsert / delete via the existing sync helpers (which already wire
fire-on-create into the assignment rules engine), and writes back
the outcome on the row.

Supported event types (per AgileCRM webhook docs):

- `add_contact` → fresh contact → `_upsert_contact_for_payload` creates
  it + the engine assigns a primary commercial.
- `update_contact` → existing contact (matched by `external_id`) →
  `_upsert_contact_for_payload` updates without re-running rules.
- `delete_contact` → mark the external reference `deleted_in_origin`
  and deactivate the contact (`Contact.is_active = False`). The row
  itself is preserved for audit.

Other event types (deal, task, note) are intentionally swallowed with
status=skipped today; a follow-up PR can route them to the right
worker as the rest of the integration grows.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.db.session import get_engine
from app.integrations.agilecrm.mapper import agilecrm_external_id
from app.models.crm import (
    Contact,
    ExternalReference,
    ExternalSystem,
)
from app.models.webhook_events import WebhookEvent, WebhookEventStatus

logger = logging.getLogger(__name__)

# AgileCRM event identifiers we react to. The vendor docs use
# snake_case; everything else falls into the `_UNSUPPORTED` bucket
# (logged + skipped, never failed — Agile retries failures).
EVENT_CREATE = "add_contact"
EVENT_UPDATE = "update_contact"
EVENT_DELETE = "delete_contact"
SUPPORTED_EVENTS = frozenset({EVENT_CREATE, EVENT_UPDATE, EVENT_DELETE})


def _extract_contact_payload(payload: Any) -> dict[str, Any] | None:
    """AgileCRM ships the contact inside the webhook body in either
    a flat shape (`{id, properties, ...}`) or nested under a `data` /
    `contact` key depending on the webhook version. Normalise here so
    the worker doesn't need to know."""
    if not isinstance(payload, dict):
        return None
    for key in ("contact", "data", "object"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and "id" in candidate:
            return candidate
    if "id" in payload and (
        "properties" in payload or "email" in payload
    ):
        return payload
    return None


def _extract_event_type(payload: Any) -> str:
    """Pull the canonical AgileCRM event name out of the body. The
    vendor sometimes ships it under `event`, sometimes `type`, and
    occasionally as `notification_type`. We accept any of those."""
    if not isinstance(payload, dict):
        return ""
    for key in ("event", "type", "notification_type", "eventType"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
    return ""


def _mark_failed(
    webhook_event: WebhookEvent, error: str
) -> None:
    webhook_event.status = WebhookEventStatus.FAILED
    webhook_event.error_summary = error[:1000]
    webhook_event.processed_at = datetime.now(UTC)


def _mark_skipped(
    webhook_event: WebhookEvent, reason: str
) -> None:
    webhook_event.status = WebhookEventStatus.SKIPPED
    webhook_event.error_summary = reason[:1000]
    webhook_event.processed_at = datetime.now(UTC)


def _mark_processed(
    webhook_event: WebhookEvent, contact_id: str | None
) -> None:
    webhook_event.status = WebhookEventStatus.PROCESSED
    webhook_event.processed_at = datetime.now(UTC)
    if contact_id:
        webhook_event.contact_id = contact_id


def _handle_delete(
    session: Session, *, account_id: str, contact_payload: dict[str, Any]
) -> str | None:
    """Soft-delete: flip `Contact.is_active` and mark the external
    reference `deleted_in_origin`. Returns the contact id when one
    was found, None otherwise (silent skip)."""
    external_id = agilecrm_external_id(contact_payload)
    if not external_id:
        return None
    ref = session.scalar(
        select(ExternalReference).where(
            ExternalReference.system == ExternalSystem.AGILECRM,
            ExternalReference.account_id == account_id,
            ExternalReference.external_id == external_id,
        )
    )
    if ref is None:
        return None
    ref.external_status = "deleted_in_origin"
    contact = session.get(Contact, ref.contact_id)
    if contact is not None:
        contact.is_active = False
    return ref.contact_id


def process_agilecrm_webhook_job(webhook_event_id: str) -> str:
    """RQ entry point — opens its own session.

    Returns one of `"processed"`, `"skipped"`, or `"failed"` for the
    benefit of RQ visibility / test assertions. Never re-raises: a
    real failure rolls back, records an audit row, and stops the
    retry loop because the event is already persisted as `failed`
    and a human can replay it from /admin/webhook-events.
    """
    with Session(get_engine()) as session:
        webhook_event = session.get(WebhookEvent, webhook_event_id)
        if webhook_event is None:
            logger.warning(
                "agilecrm.webhook unknown event id %s", webhook_event_id
            )
            return "skipped"

        try:
            raw_payload: Any = json.loads(webhook_event.payload_json)
        except (TypeError, ValueError) as exc:
            _mark_failed(webhook_event, f"invalid json: {exc}")
            session.commit()
            _emit_audit(
                session,
                webhook_event,
                Action.INTEGRATION_WEBHOOK_FAILED,
                reason="invalid_json",
            )
            session.commit()
            return "failed"

        event_type = _extract_event_type(raw_payload) or webhook_event.event_type
        if event_type and event_type != webhook_event.event_type:
            # Endpoint stamps a best-effort guess; let the worker
            # correct it from the body so the audit log is accurate.
            webhook_event.event_type = event_type[:80]

        if event_type not in SUPPORTED_EVENTS:
            _mark_skipped(
                webhook_event,
                f"unsupported event type: {event_type or '(missing)'}",
            )
            session.commit()
            _emit_audit(
                session,
                webhook_event,
                Action.INTEGRATION_WEBHOOK_SKIPPED,
                reason="unsupported_event",
            )
            session.commit()
            return "skipped"

        contact_payload = _extract_contact_payload(raw_payload)
        if contact_payload is None:
            _mark_skipped(webhook_event, "no contact body in payload")
            session.commit()
            _emit_audit(
                session,
                webhook_event,
                Action.INTEGRATION_WEBHOOK_SKIPPED,
                reason="no_contact_body",
            )
            session.commit()
            return "skipped"

        try:
            if event_type == EVENT_DELETE:
                contact_id = _handle_delete(
                    session,
                    account_id=webhook_event.account_id,
                    contact_payload=contact_payload,
                )
            else:
                # Both add_contact and update_contact go through the
                # same idempotent upserter; the engine fires only on
                # the brand-new branch.
                from app.integrations.agilecrm.jobs import (  # noqa: PLC0415
                    _upsert_contact_for_payload,
                )

                _, _, contact_id, _ = _upsert_contact_for_payload(
                    session,
                    account_id=webhook_event.account_id,
                    payload=contact_payload,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "agilecrm.webhook processing failed event_id=%s",
                webhook_event_id,
            )
            session.rollback()
            # Reattach the row in the rolled-back session before
            # flipping its status.
            webhook_event = session.get(WebhookEvent, webhook_event_id)
            if webhook_event is None:
                return "failed"
            _mark_failed(webhook_event, str(exc))
            session.commit()
            _emit_audit(
                session,
                webhook_event,
                Action.INTEGRATION_WEBHOOK_FAILED,
                reason=type(exc).__name__,
            )
            session.commit()
            return "failed"

        _mark_processed(webhook_event, contact_id)
        session.commit()
        _emit_audit(
            session,
            webhook_event,
            Action.INTEGRATION_WEBHOOK_PROCESSED,
            reason=event_type,
        )
        session.commit()
        return "processed"


def _emit_audit(
    session: Session,
    webhook_event: WebhookEvent,
    action: str,
    *,
    reason: str,
) -> None:
    record_event(
        session,
        action=action,
        target_type="webhook_event",
        target_id=webhook_event.id,
        metadata={
            "system": webhook_event.system,
            "account_id": webhook_event.account_id,
            "event_type": webhook_event.event_type,
            "status": webhook_event.status.value,
            "reason": reason,
        },
    )
