import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.passwords import MAX_LENGTH as PASSWORD_MAX_LENGTH
from app.core.passwords import MIN_LENGTH as PASSWORD_MIN_LENGTH
from app.core.passwords import validate_password_policy
from app.models.crm import (
    AuditLog,
    ConsentStatus,
    ExternalSystem,
    GdprRequestStatus,
    GdprRequestType,
    TaskStatus,
    UserRole,
)


def _enforce_password_policy(value: str) -> str:
    validate_password_policy(value)
    return value


def _decode_json_dict(value: Any) -> dict[str, Any] | None:
    """Parse a JSON-text column into a dict for API output. Returns None
    for empty/null/non-dict payloads so the frontend always sees either
    a real object or null — never a half-parsed string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


class ErrorResponse(BaseModel):
    detail: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenRead(BaseModel):
    """Login / 2FA-verify response.

    `requires_2fa`: when true, `access_token` is a short-lived pre-2FA token
    that only unlocks POST /api/auth/2fa/verify; the client must call that
    endpoint with the user's TOTP code (or a backup code) to obtain the
    final token.

    `limited`: an admin authenticated by password but without 2FA enabled.
    The final JWT works for everything except sensitive admin endpoints
    (/api/users, /api/audit-logs, /api/integration-settings) until 2FA is
    set up.
    """

    access_token: str
    token_type: str = "bearer"
    requires_2fa: bool = False
    limited: bool = False


class TotpSetupRead(BaseModel):
    secret: str  # base32, also embedded in otpauth_uri
    otpauth_uri: str  # otpauth://totp/...  → QR


class TotpConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=16)


class TotpConfirmRead(BaseModel):
    backup_codes: list[str]
    enabled: bool = True


class TotpDisableRequest(BaseModel):
    password: str = Field(min_length=1)


class TotpVerifyRequest(BaseModel):
    temp_token: str = Field(min_length=20)
    code: str = Field(min_length=4, max_length=20)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    _validate_new_password = field_validator("new_password")(_enforce_password_policy)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetRequestRead(BaseModel):
    message: str
    reset_token: str | None = None


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=16)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    _validate_new_password = field_validator("new_password")(_enforce_password_policy)


class MessageRead(BaseModel):
    message: str


class CountRead(BaseModel):
    """Tiny envelope for `GET .../count` endpoints. The dashboard reads
    it to show real totals instead of the paginated page size."""

    total: int


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    role: UserRole = UserRole.VIEWER
    is_active: bool = True

    _validate_password = field_validator("password")(_enforce_password_policy)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: UserRole | None = None
    is_active: bool | None = None

class UserPasswordUpdate(BaseModel):
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    _validate_new_password = field_validator("new_password")(_enforce_password_policy)


class UserRead(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    totp_enabled: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CurrentUserRead(UserRead):
    """Returned by GET /api/auth/me. Includes a derived flag the UI uses to
    render the admin-no-2FA banner without re-deriving server policy."""

    requires_2fa_setup: bool = False


class AuditLogRead(BaseModel):
    id: str
    actor_user_id: str | None
    actor_email: str | None
    action: str
    target_type: str
    target_id: str | None
    # The DB column is JSON-encoded text; the API exposes it decoded so the
    # frontend doesn't need to JSON.parse on every row.
    metadata: dict[str, Any] | None = None
    message: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_audit_log(cls, audit: AuditLog) -> "AuditLogRead":
        metadata: dict[str, Any] | None
        if audit.metadata_json:
            try:
                parsed = json.loads(audit.metadata_json)
                metadata = parsed if isinstance(parsed, dict) else {"value": parsed}
            except (ValueError, TypeError):
                metadata = {"raw": audit.metadata_json}
        else:
            metadata = None
        return cls(
            id=audit.id,
            actor_user_id=audit.actor_user_id,
            actor_email=audit.actor_email,
            action=audit.action,
            target_type=audit.target_type,
            target_id=audit.target_id,
            metadata=metadata,
            message=audit.message,
            ip_address=audit.ip_address,
            user_agent=audit.user_agent,
            created_at=audit.created_at,
        )


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    tax_id: str | None = Field(default=None, max_length=64)
    website: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    tax_id: str | None = Field(default=None, max_length=64)
    website: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class CompanyRead(CompanyCreate):
    id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContactCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str | None = Field(default=None, max_length=160)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=80)
    origin: str | None = Field(default=None, max_length=120)
    tags: str = Field(default="", max_length=500)
    commercial_status: str = Field(default="new", max_length=80)
    marketing_consent: ConsentStatus = ConsentStatus.UNKNOWN
    company_id: str | None = None
    address_country: str | None = Field(default=None, max_length=120)
    address_country_name: str | None = Field(default=None, max_length=255)
    address_state: str | None = Field(default=None, max_length=120)
    address_city: str | None = Field(default=None, max_length=120)
    lead_score: int | None = None

    @field_validator("first_name")
    @classmethod
    def strip_first_name(cls, value: str) -> str:
        return value.strip()


class ContactUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=120)
    last_name: str | None = Field(default=None, max_length=160)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=80)
    origin: str | None = Field(default=None, max_length=120)
    tags: str | None = Field(default=None, max_length=500)
    commercial_status: str | None = Field(default=None, max_length=80)
    marketing_consent: ConsentStatus | None = None
    company_id: str | None = None
    is_active: bool | None = None

    @field_validator("first_name")
    @classmethod
    def strip_optional_first_name(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class ContactRead(ContactCreate):
    id: str
    is_email_valid: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContactListPage(BaseModel):
    """Paginated wrapper returned by `GET /api/contacts`. The list page in
    the frontend consumes `total` to render pagination controls without a
    separate `/count` round-trip."""

    items: list[ContactRead]
    total: int
    limit: int
    offset: int


class NoteCreate(BaseModel):
    body: str = Field(min_length=1)
    author_user_id: str | None = None


class NoteRead(NoteCreate):
    id: str
    contact_id: str
    created_at: datetime
    updated_at: datetime
    # Provenance for imported notes. NULL for manually-created notes,
    # which the UI form keeps producing untouched.
    external_system: str | None = None
    external_account_id: str | None = None
    external_id: str | None = None
    external_author_email: str | None = None
    external_author_name: str | None = None
    external_created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    status: TaskStatus = TaskStatus.OPEN
    due_at: datetime | None = None
    assignee_user_id: str | None = None


class TaskRead(TaskCreate):
    id: str
    contact_id: str
    created_at: datetime
    updated_at: datetime
    external_system: str | None = None
    external_account_id: str | None = None
    external_id: str | None = None
    external_created_at: datetime | None = None
    external_updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ActivityEventRead(BaseModel):
    """Timeline event row imported from an external CRM (AgileCRM
    today). `metadata` is the decoded JSON column so the UI can render
    extra fields without re-parsing."""

    id: str
    contact_id: str
    system: str
    account_id: str
    external_id: str | None = None
    event_type: str
    subject: str | None = None
    body: str | None = None
    metadata: dict[str, Any] | None = None
    occurred_at: datetime
    synced_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _from_orm(cls, data: Any) -> Any:
        # Same dance ExternalReferenceRead does: the DB column is named
        # `metadata` (which would collide with SQLAlchemy's
        # `Base.metadata`), so the Python attribute is `metadata_json`.
        if isinstance(data, dict) or data is None:
            return data
        return {
            "id": data.id,
            "contact_id": data.contact_id,
            "system": data.system,
            "account_id": data.account_id,
            "external_id": data.external_id,
            "event_type": data.event_type,
            "subject": data.subject,
            "body": data.body,
            "metadata": _decode_json_dict(getattr(data, "metadata_json", None)),
            "occurred_at": data.occurred_at,
            "synced_at": data.synced_at,
            "created_at": data.created_at,
            "updated_at": data.updated_at,
        }


class ActivityEventListPage(BaseModel):
    """Paginated wrapper for `GET /api/contacts/{id}/activity-events`."""

    items: list[ActivityEventRead]
    total: int
    limit: int
    offset: int


class ExternalReferenceRead(BaseModel):
    """Per-system link between a CRM contact and the remote record.

    Beyond the canonical id/system/external_id we also surface the
    integration `account_id` (so multi-tenant deployments tell the two
    AgileCRM accounts apart), the remote-system timestamps mirrored from
    AgileCRM's `created_time` / `updated_time`, the AgileCRM `source`
    string in `origin_detail`, and the decoded `metadata` JSON (owner
    snapshot, raw tags) the mapper stuffs in there.
    """

    id: str
    system: ExternalSystem
    account_id: str
    external_id: str
    account_label: str | None = None
    contact_id: str
    external_created_at: datetime | None = None
    external_updated_at: datetime | None = None
    origin_detail: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _from_orm(cls, data: Any) -> Any:
        # The ORM column is named `metadata` (clash with SQLAlchemy's
        # Base.metadata) so the Python attribute is `metadata_json`.
        # from_attributes alone would look for `obj.metadata` and hit
        # the SQLAlchemy MetaData object instead — so we map manually
        # and JSON-decode in one go.
        if isinstance(data, dict) or data is None:
            return data
        return {
            "id": data.id,
            "system": data.system,
            "account_id": data.account_id,
            "external_id": data.external_id,
            "account_label": data.account_label,
            "contact_id": data.contact_id,
            "external_created_at": data.external_created_at,
            "external_updated_at": data.external_updated_at,
            "origin_detail": data.origin_detail,
            "metadata": _decode_json_dict(getattr(data, "metadata_json", None)),
            "created_at": data.created_at,
            "updated_at": data.updated_at,
        }


class ContactDetailRead(ContactRead):
    notes: list[NoteRead] = Field(default_factory=list)
    tasks: list[TaskRead] = Field(default_factory=list)
    external_refs: list[ExternalReferenceRead] = Field(default_factory=list)
    # The detail screen only renders the latest 50 events; the full
    # timeline lives behind `GET /api/contacts/{id}/activity-events`.
    activity_events: list[ActivityEventRead] = Field(default_factory=list)
    # `custom_fields` is JSON text on the DB; we decode it here so the
    # detail screen doesn't need to JSON.parse a string before rendering
    # the key/value list.
    custom_fields: dict[str, Any] | None = None

    @field_validator("custom_fields", mode="before")
    @classmethod
    def _decode_custom_fields(cls, value: Any) -> Any:
        return _decode_json_dict(value)


class HealthRead(BaseModel):
    status: str
    app_name: str
    environment: str


class GdprRequestCreate(BaseModel):
    subject_email: EmailStr
    request_type: GdprRequestType
    notes: str | None = Field(default=None, max_length=4000)


class GdprRequestUpdate(BaseModel):
    status: GdprRequestStatus | None = None
    notes: str | None = Field(default=None, max_length=4000)


class GdprRequestRead(BaseModel):
    id: str
    subject_email: EmailStr
    subject_contact_id: str | None
    request_type: GdprRequestType
    status: GdprRequestStatus
    requested_at: datetime
    completed_at: datetime | None
    requester_user_id: str | None
    notes: str | None
    evidence_path: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GdprProcessResult(BaseModel):
    """Returned by POST /gdpr/requests/{id}/process. `payload` carries the
    type-specific summary (rectification endpoint list, erasure counts,
    objection state, etc.) so the UI can render a clear confirmation
    without re-fetching."""

    request_id: str
    request_type: GdprRequestType
    status: GdprRequestStatus
    evidence_path: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
