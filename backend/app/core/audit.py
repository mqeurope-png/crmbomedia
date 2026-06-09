"""Audit-log emission helpers.

Centralises the canonical action names (`Action.*`) so renaming an event
is a one-place change, and `record_event(session, ..., request=request)` —
the single entry point every route handler should use to write to
`audit_logs`. It captures the actor's user id and email when an
authenticated user is present, serialises the optional `metadata` dict,
and pulls the client IP + user agent off the FastAPI Request (honouring
X-Forwarded-For / X-Real-IP for setups behind Nginx or Plesk).

Action names are dotted (`auth.password_changed`) so the audit log can be
filtered by prefix (e.g. all `auth.*` events) without ambiguous matches.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.crm import AuditLog, User


class Action:
    """Canonical action strings. Use these instead of inline literals."""

    # Authentication
    AUTH_LOGIN_SUCCESS = "auth.login_success"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_PASSWORD_CHANGED = "auth.password_changed"
    AUTH_PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"
    AUTH_PASSWORD_RESET_CONFIRMED = "auth.password_reset_confirmed"

    # Two-factor authentication
    AUTH_2FA_SETUP_STARTED = "auth.2fa_setup_started"
    AUTH_2FA_ENABLED = "auth.2fa_enabled"
    AUTH_2FA_DISABLED = "auth.2fa_disabled"
    AUTH_2FA_VERIFIED = "auth.2fa_verified"
    AUTH_2FA_VERIFIED_BACKUP_CODE = "auth.2fa_verified_backup_code"
    AUTH_2FA_RESET_CLI = "auth.2fa_reset_cli"

    # Users
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_ROLE_CHANGED = "user.role_changed"
    USER_DEACTIVATED = "user.deactivated"
    USER_REACTIVATED = "user.reactivated"
    USER_PASSWORD_SET_BY_ADMIN = "user.password_set_by_admin"

    # CRM
    COMPANY_CREATED = "company.created"
    COMPANY_UPDATED = "company.updated"
    COMPANY_DEACTIVATED = "company.deactivated"
    CONTACT_CREATED = "contact.created"
    CONTACT_UPDATED = "contact.updated"
    CONTACT_DEACTIVATED = "contact.deactivated"
    NOTE_CREATED = "note.created"
    TASK_CREATED = "task.created"

    # Integration accounts (multi-account refactor 20260515_0007).
    # The legacy single-account names live on as aliases below so audit
    # readers and dashboards can still filter on the old strings while
    # historic rows live out their retention.
    INTEGRATION_ACCOUNT_CREATED = "integration_account.created"
    INTEGRATION_ACCOUNT_UPDATED = "integration_account.updated"
    INTEGRATION_ACCOUNT_DELETED = "integration_account.deleted"
    INTEGRATION_ACCOUNT_API_KEY_SET = "integration_account.api_key_set"
    INTEGRATION_ACCOUNT_API_KEY_DELETED = "integration_account.api_key_deleted"
    # Legacy aliases kept as compile-time references to old constant names.
    INTEGRATION_SETTING_UPDATED = INTEGRATION_ACCOUNT_UPDATED
    INTEGRATION_API_KEY_SET = INTEGRATION_ACCOUNT_API_KEY_SET
    INTEGRATION_API_KEY_DELETED = INTEGRATION_ACCOUNT_API_KEY_DELETED

    # Audit log itself
    AUDIT_EXPORTED = "audit.exported"

    # Access control
    ACCESS_FORBIDDEN = "access.forbidden"

    # Integration runtime (HTTP client, worker, webhook intake).
    # Sprint A baseline; per-system action names will be added when the
    # individual connectors land.
    INTEGRATION_API_CALL = "integration.api_call"
    INTEGRATION_AUTH_FAILED = "integration.auth_failed"
    INTEGRATION_SYNC_TRIGGERED = "integration.sync_triggered"
    INTEGRATION_SYNC_STARTED = "integration.sync_started"
    INTEGRATION_SYNC_SUCCEEDED = "integration.sync_succeeded"
    INTEGRATION_SYNC_PARTIAL = "integration.sync_partial"
    INTEGRATION_SYNC_FAILED = "integration.sync_failed"
    INTEGRATION_WEBHOOK_RECEIVED = "integration.webhook_received"
    # Per-record cleanups (e.g. AgileCRM quota purge). Metadata includes
    # the remote external_id + the account that owned it; never includes
    # any field of the contact beyond what's needed to identify the row.
    INTEGRATION_QUOTA_DELETED = "integration.quota_deleted"

    # On-demand external-data refresh (Sprint A PR-8). One row per
    # operator-triggered fetch of notes/tasks/events for a contact,
    # plus per-system success / rate-limit / auth-error breakdowns.
    EXTERNAL_REFRESH_REQUESTED = "external_refresh.requested"
    EXTERNAL_REFRESH_RATE_LIMITED = "external_refresh.rate_limited"
    EXTERNAL_REFRESH_AUTH_ERROR = "external_refresh.auth_error"

    # Tags (Sprint P.1 ampliado).
    TAG_CREATED = "tag.created"
    TAG_UPDATED = "tag.updated"
    TAG_DELETED = "tag.deleted"
    CONTACT_TAG_ADDED = "contact_tag.added"
    CONTACT_TAG_REMOVED = "contact_tag.removed"
    CONTACT_TAGS_BULK_ACTION = "contact_tags.bulk_action"

    # GDPR / RGPD subject-rights events
    GDPR_REQUEST_CREATED = "gdpr.request_created"
    GDPR_REQUEST_UPDATED = "gdpr.request_updated"
    GDPR_REQUEST_PROCESSED = "gdpr.request_processed"
    GDPR_EXPORT_GENERATED = "gdpr.export_generated"
    GDPR_CONTACT_ERASED = "gdpr.contact_erased"
    GDPR_AUDIT_ANONYMIZED = "gdpr.audit_anonymized"
    GDPR_OBJECTION_APPLIED = "gdpr.objection_applied"
    GDPR_RECTIFICATION_GUIDANCE = "gdpr.rectification_guidance"


def client_ip(request: Request | None) -> str | None:
    """Resolve the request's source IP, honouring proxy headers."""
    if request is None:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip() or None
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip() or None
    if request.client and request.client.host:
        return request.client.host
    return None


def _user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    return request.headers.get("user-agent")


def record_event(
    session: Session,
    *,
    action: str,
    target_type: str,
    target_id: str | None = None,
    actor: User | None = None,
    actor_email: str | None = None,
    metadata: dict[str, Any] | None = None,
    message: str | None = None,
    request: Request | None = None,
) -> AuditLog:
    """Persist one audit row. Caller is responsible for `session.commit()`."""
    audit = AuditLog(
        actor_user_id=actor.id if actor else None,
        actor_email=actor_email or (actor.email if actor else None),
        action=action,
        target_type=target_type,
        target_id=target_id,
        message=message,
        metadata_json=json.dumps(metadata, default=str) if metadata else None,
        ip_address=client_ip(request),
        user_agent=_user_agent(request),
    )
    session.add(audit)
    return audit
