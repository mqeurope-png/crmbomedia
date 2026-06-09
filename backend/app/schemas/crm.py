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


class TagRead(BaseModel):
    """Tag exposed on contact list / detail responses. The lighter
    shape (id + name + color) lives on every contact row; the full
    shape (with description, count) goes through the dedicated
    `/api/tags` endpoints."""

    id: str
    name: str
    color: str | None = None

    model_config = ConfigDict(from_attributes=True)


#: Fixed tag colour palette. Mirrors the Tailwind v3 `*-500` shades.
#: Keep in sync with `frontend/src/app/lib/tagPalette.ts` — the
#: backend validates here so a hand-rolled API call can't smuggle a
#: random hex into the DB. Tags created before this PR may carry
#: out-of-palette colours; the validator accepts NULL/empty so an
#: operator can clear those legacy values from the UI.
TAG_COLOR_PALETTE: tuple[str, ...] = (
    "#64748b",  # slate-500
    "#6b7280",  # gray-500
    "#71717a",  # zinc-500
    "#737373",  # neutral-500
    "#78716c",  # stone-500
    "#ef4444",  # red-500
    "#f97316",  # orange-500
    "#f59e0b",  # amber-500
    "#eab308",  # yellow-500
    "#84cc16",  # lime-500
    "#22c55e",  # green-500
    "#10b981",  # emerald-500
    "#14b8a6",  # teal-500
    "#06b6d4",  # cyan-500
    "#0ea5e9",  # sky-500
    "#3b82f6",  # blue-500
    "#6366f1",  # indigo-500
    "#8b5cf6",  # violet-500
    "#a855f7",  # purple-500
    "#d946ef",  # fuchsia-500
    "#ec4899",  # pink-500
    "#f43f5e",  # rose-500
)
_TAG_COLOR_PALETTE_SET = {c.lower() for c in TAG_COLOR_PALETTE}


def _validate_tag_color(value: str | None) -> str | None:
    """Accept NULL/empty (clears the colour) or one of the palette
    swatches. Raises `ValueError` on anything else so FastAPI returns
    422 and the UI shows a clear field-level message."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.lower() not in _TAG_COLOR_PALETTE_SET:
        raise ValueError(
            "color must be one of the palette swatches (see TAG_COLOR_PALETTE)"
        )
    return cleaned.lower()


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=7)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("color", mode="before")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        return _validate_tag_color(value)


class TagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=7)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        return value.strip() if value else value

    @field_validator("color", mode="before")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        return _validate_tag_color(value)


class TagDetailRead(TagRead):
    description: str | None = None
    created_by_user_id: str | None = None
    contact_count: int = 0
    created_at: datetime
    updated_at: datetime


class TagListPage(BaseModel):
    items: list[TagDetailRead]
    total: int
    limit: int
    offset: int


class ContactTagAssignRequest(BaseModel):
    """Body for `POST /api/contacts/{id}/tags`. One of `tag_id` or
    `tag_name` must be set; sending `tag_name` triggers a case-
    insensitive upsert on the tags table so the operator can attach a
    brand new tag in one round-trip."""

    tag_id: str | None = None
    tag_name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=7)

    @field_validator("tag_name")
    @classmethod
    def strip_tag_name(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class BulkContactTagRequest(BaseModel):
    """`POST /api/contacts/bulk-tag` — add/remove one tag on many
    contacts in a single audited operation."""

    action: str = Field(pattern="^(add|remove)$")
    tag_id: str
    contact_ids: list[str] = Field(min_length=1, max_length=500)


class BulkContactTagResult(BaseModel):
    action: str
    tag_id: str
    affected: int
    skipped: int


# ---------------------------------------------------------------------------
# Saved contact views (Sprint P.1 ampliado PR-B)
# ---------------------------------------------------------------------------


class ContactViewFilters(BaseModel):
    """Shape stored as JSON in `contact_views.filters_json`. Mirrors
    the query params accepted by `GET /api/contacts` so the route can
    pass it through `_apply_contact_filters` after merging URL
    overrides on top."""

    q: str | None = None
    tag_ids: list[str] | None = None
    tag_match_mode: str | None = None
    origin_system: str | None = None
    origin_account_id: str | None = None
    commercial_status: str | None = None
    marketing_consent: str | None = None
    is_active: bool | None = None
    lead_score_min: int | None = None
    lead_score_max: int | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


class ContactViewColumns(BaseModel):
    """Visible columns + ordering + per-column widths. `visible` is the
    set of column keys the operator actually rendered; `order` is the
    full sequence (visible + hidden) so the column configurator can
    surface every option in its persisted position."""

    visible: list[str] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    widths: dict[str, int] = Field(default_factory=dict)


class ContactViewSort(BaseModel):
    sort_by: str = "created_at"
    sort_dir: str = "desc"


class ContactViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    is_shared: bool = False
    is_default: bool = False
    filters: ContactViewFilters = Field(default_factory=ContactViewFilters)
    columns: ContactViewColumns = Field(default_factory=ContactViewColumns)
    sort: ContactViewSort = Field(default_factory=ContactViewSort)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class ContactViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    is_shared: bool | None = None
    is_default: bool | None = None
    filters: ContactViewFilters | None = None
    columns: ContactViewColumns | None = None
    sort: ContactViewSort | None = None

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class ContactViewRead(BaseModel):
    id: str
    name: str
    description: str | None = None
    owner_user_id: str
    is_owner: bool = False
    is_shared: bool
    is_default: bool
    filters: ContactViewFilters
    columns: ContactViewColumns
    sort: ContactViewSort
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContactViewDuplicateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)


class ContactRead(ContactCreate):
    id: str
    is_email_valid: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Real M:N tags exposed alongside the (deprecated, still readable)
    # `tags` CSV field. New code should consume `tag_objects`; the CSV
    # stays for backwards-compat during the migration. The Contact ORM
    # exposes a `tag_objects` Python property that flattens its
    # `tag_assignments` relationship to a list of Tag rows — Pydantic
    # picks it up automatically via from_attributes.
    tag_objects: list[TagRead] = Field(default_factory=list)

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
    # Sprint A PR-8: notes/tasks/events are no longer pre-synced for
    # every contact during the bulk job — they're fetched on demand
    # from the detail page. These two fields drive the freshness
    # indicator + auto-refresh behaviour in the UI.
    last_external_refresh_at: datetime | None = None
    external_data_freshness: str = "outdated"

    @field_validator("custom_fields", mode="before")
    @classmethod
    def _decode_custom_fields(cls, value: Any) -> Any:
        return _decode_json_dict(value)


class ExternalRefreshRead(BaseModel):
    """Response of `POST /api/contacts/{id}/refresh-external-data`. The
    UI uses `status` to decide whether to render a warning banner and
    `sources_refreshed` to confirm which integrations were attempted —
    a multi-account contact whose primary AgileCRM succeeded and
    secondary was rate-limited shows "Partial" rather than "Failed"."""

    refreshed_at: datetime
    sources_refreshed: list[str]
    notes_count: int
    tasks_count: int
    events_count: int
    warnings: list[str]
    status: str  # ok | partial


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


# ---------------------------------------------------------------------------
# Pipelines (Sprint P.2)
# ---------------------------------------------------------------------------


class PipelineStageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=7)
    is_won: bool = False
    is_lost: bool = False
    target_days: int | None = Field(default=None, ge=0)
    position: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("color", mode="before")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        return _validate_tag_color(value)


class PipelineStageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=7)
    is_won: bool | None = None
    is_lost: bool | None = None
    target_days: int | None = Field(default=None, ge=0)

    @field_validator("color", mode="before")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        return _validate_tag_color(value)


class PipelineStageRead(BaseModel):
    id: str
    pipeline_id: str
    name: str
    description: str | None = None
    position: int
    color: str | None = None
    is_won: bool
    is_lost: bool
    target_days: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PipelineStageReorderRequest(BaseModel):
    """`POST /api/pipelines/{id}/stages/reorder` body — the full list
    of stage UUIDs in their new order. The route validates length
    equals the pipeline's current stage count so the operator can't
    accidentally drop a stage by omitting it."""

    stage_ids: list[str] = Field(min_length=1)


class PipelineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=7)
    is_shared: bool = True
    stages: list[PipelineStageCreate] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("color", mode="before")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        return _validate_tag_color(value)


class PipelineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=7)
    is_shared: bool | None = None
    is_active: bool | None = None

    @field_validator("color", mode="before")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        return _validate_tag_color(value)


class PipelineRead(BaseModel):
    id: str
    name: str
    description: str | None = None
    color: str | None = None
    is_active: bool
    is_shared: bool
    owner_user_id: str
    stages: list[PipelineStageRead] = Field(default_factory=list)
    contact_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PipelineDuplicateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    include_contacts: bool = False


class ContactPipelineAddRequest(BaseModel):
    pipeline_id: str
    stage_id: str | None = None  # defaults to position-0 stage
    note: str | None = Field(default=None, max_length=4000)


class ContactPipelineMoveRequest(BaseModel):
    stage_id: str
    note: str | None = Field(default=None, max_length=4000)


class ContactStageHistoryRead(BaseModel):
    id: str
    from_stage_id: str | None = None
    to_stage_id: str
    moved_by_user_id: str | None = None
    moved_at: datetime
    duration_seconds_in_previous_stage: int | None = None
    note: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ContactPipelineStageRead(BaseModel):
    id: str
    contact_id: str
    pipeline_id: str
    stage_id: str
    entered_stage_at: datetime
    added_to_pipeline_at: datetime
    last_activity_at: datetime | None = None
    notes: str | None = None
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContactPipelineSummary(BaseModel):
    """Compact row the contact-detail page uses to surface every
    pipeline a contact lives in. One round-trip beats N calls to
    `GET /pipelines/{id}` from the UI."""

    assignment_id: str
    pipeline_id: str
    pipeline_name: str
    pipeline_color: str | None = None
    stage_id: str
    stage_name: str
    stage_color: str | None = None
    stage_position: int
    is_won: bool
    is_lost: bool
    days_in_stage: int
    entered_stage_at: datetime
    added_to_pipeline_at: datetime


class PipelineContactCard(BaseModel):
    """Compact row used by the kanban view — enough to render a card
    without re-fetching the full contact."""

    id: str  # contact_pipeline_stages.id
    contact_id: str
    first_name: str
    last_name: str | None = None
    email: str
    phone: str | None = None
    lead_score: int | None = None
    tags: list[TagRead] = Field(default_factory=list)
    entered_stage_at: datetime
    added_to_pipeline_at: datetime
    days_in_stage: int


class PipelineStageGroup(BaseModel):
    stage_id: str
    stage_name: str
    stage_color: str | None = None
    position: int
    is_won: bool
    is_lost: bool
    target_days: int | None = None
    total: int
    contacts: list[PipelineContactCard]


class PipelineContactsResponse(BaseModel):
    pipeline: PipelineRead
    stages: list[PipelineStageGroup]


class PipelineStageMetric(BaseModel):
    stage_id: str
    stage_name: str
    position: int
    contact_count: int
    avg_seconds_in_stage: float | None = None
    conversion_to_next: float | None = None  # 0..1 ratio
    stalled_count: int = 0  # over target_days


class PipelineReportResponse(BaseModel):
    pipeline_id: str
    pipeline_name: str
    total_contacts: int
    won_count: int
    lost_count: int
    metrics: list[PipelineStageMetric]
