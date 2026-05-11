import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

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


class NoteCreate(BaseModel):
    body: str = Field(min_length=1)
    author_user_id: str | None = None


class NoteRead(NoteCreate):
    id: str
    contact_id: str
    created_at: datetime
    updated_at: datetime

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

    model_config = ConfigDict(from_attributes=True)


class ExternalReferenceRead(BaseModel):
    id: str
    system: ExternalSystem
    external_id: str
    account_label: str | None = None
    contact_id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContactDetailRead(ContactRead):
    notes: list[NoteRead] = Field(default_factory=list)
    tasks: list[TaskRead] = Field(default_factory=list)
    external_refs: list[ExternalReferenceRead] = Field(default_factory=list)


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
