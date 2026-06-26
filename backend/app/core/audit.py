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
    COMPANY_DELETED = "company.deleted"
    # PR-F — bulk dispatch en /api/companies/bulk-action. La acción
    # concreta viaja en el metadata para auditar qué se hizo.
    COMPANY_BULK_ACTION = "company.bulk_action"
    CONTACT_CREATED = "contact.created"
    CONTACT_UPDATED = "contact.updated"
    # PR-Ficha-Fix. PATCH masivo desde el modal "Editar completo"
    # (3+ campos en una sola request). El inline edit single-field
    # del strip sigue siendo CONTACT_UPDATED para no romper dashboards
    # ya existentes que cuentan ediciones individuales.
    CONTACT_BULK_UPDATED = "contact.bulk_updated"
    CONTACT_DEACTIVATED = "contact.deactivated"
    # PR-Consolidado — Star Rating. Cambios en `contacts.star_rating`
    # se auditan aparte para que el dashboard "Quién marca a quién con
    # estrellas" pueda agregar sin filtrar otros campos.
    CONTACT_STAR_RATING_CHANGED = "contact.star_rating_changed"
    # PR-Backlog-Consolidado B1. Hard delete del contacto: el row
    # desaparece de la BD junto con tasks/notes/assignments. El audit
    # log incluye un snapshot JSON de los datos clave (email, owner,
    # lifecycle_status, lead_score, created_at) por si hay disputa.
    CONTACT_DELETED = "contact.deleted"
    # PR-Contact-Unsubscribe-Admin. Admin reactiva un contacto que
    # se había opt-eado out vía la página pública de unsubscribe.
    # Metadata incluye los scopes borrados (marketing/all/etc.).
    CONTACT_RESUBSCRIBED = "contact.resubscribed"
    # Sprint Reglas-Assign PR-B — multi-asignación de comerciales.
    CONTACT_ASSIGNMENT_ADDED = "contact.assignment_added"
    CONTACT_ASSIGNMENT_REMOVED = "contact.assignment_removed"
    CONTACT_PRIMARY_CHANGED = "contact.primary_changed"
    # Sprint Reglas-Assign PR-C — motor de auto-asignación.
    ASSIGNMENT_RULE_CREATED = "assignment_rule.created"
    ASSIGNMENT_RULE_UPDATED = "assignment_rule.updated"
    ASSIGNMENT_RULE_DELETED = "assignment_rule.deleted"
    ASSIGNMENT_RULE_AUTO_DISABLED = "assignment_rule.auto_disabled"
    ASSIGNMENT_RULE_RUN = "assignment_rule.run"
    # PR-F (cierre): emitido por evaluate_for_contact y por
    # run_rule_over_universe cada vez que una regla se aplica a un
    # contacto concreto. Permite reconstruir "por qué este contacto
    # acabó asignado al user X" desde el audit log.
    ASSIGNMENT_RULE_APPLIED = "assignment_rule.applied"
    NOTE_CREATED = "note.created"
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_COMPLETED = "task.completed"
    TASK_DELETED = "task.deleted"

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
    INTEGRATION_SYNC_SKIPPED = "integration.sync_skipped"
    INTEGRATION_SYNC_FAILED = "integration.sync_failed"
    INTEGRATION_WEBHOOK_RECEIVED = "integration.webhook_received"
    # Per-record cleanups (e.g. AgileCRM quota purge). Metadata includes
    # the remote external_id + the account that owned it; never includes
    # any field of the contact beyond what's needed to identify the row.
    INTEGRATION_QUOTA_DELETED = "integration.quota_deleted"

    # Sprint Backup. Disparo manual desde /admin/backups + borrado
    # explícito de un backup. El éxito/fallo del job en sí no emite
    # audit (vive en la propia row de `backups`); estos eventos
    # cubren la acción humana.
    BACKUP_TRIGGERED = "backup.triggered"
    BACKUP_DELETED = "backup.deleted"

    # On-demand external-data refresh (Sprint A PR-8). One row per
    # operator-triggered fetch of notes/tasks/events for a contact,
    # plus per-system success / rate-limit / auth-error breakdowns.
    EXTERNAL_REFRESH_REQUESTED = "external_refresh.requested"
    EXTERNAL_REFRESH_RATE_LIMITED = "external_refresh.rate_limited"
    EXTERNAL_REFRESH_AUTH_ERROR = "external_refresh.auth_error"

    # Brevo webhooks (Sprint B+D). Reactive contact mutations driven
    # by inbound events — audited so a consent flip always has a
    # traceable origin.
    CONTACT_CONSENT_CHANGED_BY_WEBHOOK = "contact.consent_changed_by_webhook"
    CONTACT_EMAIL_INVALIDATED_BY_WEBHOOK = "contact.email_invalidated_by_webhook"

    # Sprint-Push-CRM-Brevo. Reverso del sync (CRM → Brevo).
    # `pushed` se emite con metadata `{contact_id, list_id, action}` donde
    # action ∈ {created, moved, added_to_list}.
    # `removed` con `{contact_id, list_ids, reason}` donde
    # reason ∈ {owner_removed, contact_deleted}.
    BREVO_CONTACT_PUSHED = "brevo.contact.pushed"
    BREVO_CONTACT_REMOVED = "brevo.contact.removed"
    BREVO_CONTACT_PUSH_FAILED = "brevo.contact.push_failed"
    BREVO_USER_LIST_MAPPING_UPDATED = "brevo.user_list_mapping.updated"
    BREVO_BACKFILL_TRIGGERED = "brevo.backfill.triggered"

    # Sprint-Backfill-Gmail. Job admin que importa 3 años de Gmail
    # histórico, asociado a cada contacto + comercial owner. Metadata
    # típica `{job_id, mode, config}` + counts en _COMPLETED.
    # _ATTACHMENT_DOWNLOADED es por click humano en la UI; metadata
    # incluye `{attachment_id, filename, size, message_id, contact_id}`.
    GMAIL_BACKFILL_ESTIMATED = "gmail.backfill.estimated"
    GMAIL_BACKFILL_TRIGGERED = "gmail.backfill.triggered"
    GMAIL_BACKFILL_CANCELLED = "gmail.backfill.cancelled"
    GMAIL_BACKFILL_COMPLETED = "gmail.backfill.completed"
    EMAIL_ATTACHMENT_DOWNLOADED = "email.attachment.downloaded"

    # Tags (Sprint P.1 ampliado).
    TAG_CREATED = "tag.created"
    TAG_UPDATED = "tag.updated"
    TAG_DELETED = "tag.deleted"
    CONTACT_TAG_ADDED = "contact_tag.added"
    CONTACT_TAG_REMOVED = "contact_tag.removed"
    CONTACT_TAGS_BULK_ACTION = "contact_tags.bulk_action"

    # Saved contact views (Sprint P.1 ampliado PR-B).
    CONTACT_VIEW_CREATED = "contact_view.created"
    CONTACT_VIEW_UPDATED = "contact_view.updated"
    CONTACT_VIEW_DELETED = "contact_view.deleted"
    CONTACT_VIEW_DUPLICATED = "contact_view.duplicated"
    CONTACT_VIEW_DEFAULT_SET = "contact_view.default_set"

    # Saved entity views — multi-entity generalization
    # (Sprint Filtros & Listas PR-B). Metadata carries `entity_type`.
    ENTITY_VIEW_CREATED = "entity_view.created"
    ENTITY_VIEW_UPDATED = "entity_view.updated"
    ENTITY_VIEW_DELETED = "entity_view.deleted"
    ENTITY_VIEW_DUPLICATED = "entity_view.duplicated"
    ENTITY_VIEW_DEFAULT_SET = "entity_view.default_set"

    # Pipelines (Sprint P.2).
    PIPELINE_CREATED = "pipeline.created"
    PIPELINE_UPDATED = "pipeline.updated"
    PIPELINE_DELETED = "pipeline.deleted"
    PIPELINE_DUPLICATED = "pipeline.duplicated"
    PIPELINE_STAGE_CREATED = "pipeline_stage.created"
    PIPELINE_STAGE_UPDATED = "pipeline_stage.updated"
    PIPELINE_STAGE_DELETED = "pipeline_stage.deleted"
    PIPELINE_STAGE_REORDERED = "pipeline_stage.reordered"
    CONTACT_PIPELINE_STAGE_ADDED = "contact_pipeline_stage.added"
    CONTACT_PIPELINE_STAGE_CHANGED = "contact_pipeline_stage.stage_changed"
    CONTACT_PIPELINE_STAGE_ARCHIVED = "contact_pipeline_stage.archived"

    PIPELINE_AI_GENERATED = "pipeline.ai_generated"

    # Segments (Sprint P.3).
    SEGMENT_CREATED = "segment.created"
    SEGMENT_UPDATED = "segment.updated"
    SEGMENT_DELETED = "segment.deleted"
    SEGMENT_DUPLICATED = "segment.duplicated"
    SEGMENT_EVALUATED = "segment.evaluated"
    SEGMENT_AI_GENERATED = "segment.ai_generated"
    SEGMENT_AI_EXPLAINED = "segment.ai_explained"

    # Gmail integration (Sprint Email v1).
    EMAIL_SENT_FROM_CRM = "email.sent_from_crm"
    EMAIL_REPLY_RECEIVED = "email.reply_received"
    EMAIL_THREAD_MARKED_READ = "email.thread_marked_read"
    GMAIL_WATCH_REGISTERED = "gmail.watch_registered"
    GMAIL_WATCH_RENEWED = "gmail.watch_renewed"

    # PR-OAuth-Permisos-Admin (items 9, 12, 13). Ciclo de vida OAuth.
    GMAIL_REFRESH_FAILED_PERMANENT = "gmail.refresh_failed_permanent"
    GMAIL_DISCONNECTED_BY_USER = "gmail.disconnected_by_user"
    GMAIL_RECONNECTED = "gmail.reconnected"
    GMAIL_CONNECTED = "gmail.connected"
    GMAIL_TOKEN_EXPIRY_WARNING_SENT = "gmail.token_expiry_warning_sent"
    GMAIL_ALIASES_SYNCED = "gmail.aliases_synced"
    GMAIL_ADMIN_DIGEST_SENT = "gmail.admin_digest_sent"

    # Mailbox redesign (Sprint Email v2.4).
    EMAIL_FOLDER_CREATED = "email.folder_created"
    EMAIL_FOLDER_UPDATED = "email.folder_updated"
    EMAIL_FOLDER_DELETED = "email.folder_deleted"
    EMAIL_LABEL_CREATED = "email.label_created"
    EMAIL_LABEL_UPDATED = "email.label_updated"
    EMAIL_LABEL_DELETED = "email.label_deleted"
    EMAIL_THREADS_UPDATED = "email.threads_updated"

    # Google Calendar integration (Mini-PR C Fase 2).
    GOOGLE_CALENDAR_CONNECTED = "google_calendar.connected"
    GOOGLE_CALENDAR_DISCONNECTED = "google_calendar.disconnected"
    GOOGLE_CALENDAR_SELECTED = "google_calendar.calendar_selected"
    GOOGLE_CALENDAR_EVENT_SYNCED = "google_calendar.event_synced"
    GOOGLE_CALENDAR_EVENT_FAILED = "google_calendar.event_failed"

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
