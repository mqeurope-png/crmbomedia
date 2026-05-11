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

    # Integration settings
    INTEGRATION_SETTING_UPDATED = "integration_setting.updated"
    INTEGRATION_API_KEY_SET = "integration_api_key.set"
    INTEGRATION_API_KEY_DELETED = "integration_api_key.deleted"

    # Audit log itself
    AUDIT_EXPORTED = "audit.exported"

    # Access control
    ACCESS_FORBIDDEN = "access.forbidden"


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
