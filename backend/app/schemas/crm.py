import json
import logging
from datetime import datetime
from typing import Any, Literal

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
    # Sprint Email v2.3b — operator default for the "Incluir opción de
    # baja" toggle in the send modal. The frontend reads it once on
    # session start and uses it as the modal's initial state.
    email_include_unsubscribe_default: bool = False


class UserPreferencesRead(BaseModel):
    """Slim read model for the /account preferences section."""

    email_include_unsubscribe_default: bool

    model_config = ConfigDict(from_attributes=True)


class UserPreferencesWrite(BaseModel):
    email_include_unsubscribe_default: bool


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
    # Sprint Empresas — sub-PR 2/4. Professional + finer-grained
    # address columns lifted from `custom_fields` JSON.
    job_title: str | None = Field(default=None, max_length=200)
    linkedin_url: str | None = Field(default=None, max_length=500)
    personal_website: str | None = Field(default=None, max_length=500)
    address_line: str | None = Field(default=None, max_length=500)
    address_postal_code: str | None = Field(default=None, max_length=20)
    address_region: str | None = Field(default=None, max_length=120)
    lead_score: int | None = None
    # PR-Consolidado — Star Rating. Field validation 0-5 (ver
    # ContactUpdate). 0 / None ambos = sin valorar.
    star_rating: int | None = Field(default=None, ge=0, le=5)

    @field_validator("first_name")
    @classmethod
    def strip_first_name(cls, value: str) -> str:
        return value.strip()


class ContactPhonePayload(BaseModel):
    """PR-Editar-Completo. Payload de un teléfono dentro del PATCH
    masivo de la ficha. `id` opcional permite preservar la row
    existente (tracking de provenance) si el operador solo cambió
    `label`/`is_primary`; ausente → crea nueva."""

    id: str | None = None
    number: str = Field(min_length=1, max_length=80)
    label: str | None = Field(default=None, max_length=80)
    is_primary: bool = False


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
    # v2 sub-PR 2/4: same fields the create payload accepts so the
    # ficha can PATCH any of them individually.
    job_title: str | None = Field(default=None, max_length=200)
    linkedin_url: str | None = Field(default=None, max_length=500)
    personal_website: str | None = Field(default=None, max_length=500)
    address_line: str | None = Field(default=None, max_length=500)
    address_city: str | None = Field(default=None, max_length=120)
    address_state: str | None = Field(default=None, max_length=120)
    address_postal_code: str | None = Field(default=None, max_length=20)
    address_region: str | None = Field(default=None, max_length=120)
    address_country: str | None = Field(default=None, max_length=120)
    address_country_name: str | None = Field(default=None, max_length=255)
    # PR-Ficha-Fix. Faltaban en el `ContactUpdate` original: el strip
    # del inline edit mandaba `lead_score` en el PATCH y FastAPI lo
    # rechazaba con 422 (campo no permitido) — Bart veía el valor
    # volver a 0 tras cambiarlo. `custom_fields` lo añadimos también
    # para que el modal Editar pueda persistir el GRADO_DE_INTERES y
    # demás propiedades migradas desde Agile.
    lead_score: int | None = None
    # PR-Consolidado — Star Rating. Réplica del Star Value de AgileCRM
    # como campo independiente del lead_score. Validación: 0-5 o NULL;
    # cualquier otro valor → 422. `0` y `None` son ambos "sin valorar"
    # (semánticamente equivalentes; ver migration 0065).
    star_rating: int | None = Field(default=None, ge=0, le=5)
    custom_fields: dict[str, Any] | str | None = None
    # PR-Editar-Completo. Campos multi-tabla expuestos via el PATCH
    # masivo para que el modal pueda guardar todo en una sola
    # request:
    # - `phones`: REEMPLAZA la lista completa de contact_phones.
    #   Pasa `None` para no tocar; lista vacía para borrar todos.
    # - `owner_id`: usuario que pasa a ser primary en
    #   contact_assignments. `None` → quitar el primary actual sin
    #   asignar nuevo.
    # - `unsubscribe_action`: solo `"resubscribe"` admin-only.
    #   Borra rows de email_unsubscribes + quita tag.
    phones: list[ContactPhonePayload] | None = None
    owner_id: str | None = None
    unsubscribe_action: Literal["resubscribe"] | None = None
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
    # Sprint UX: pairs of "system:account_id". Takes precedence over
    # `origin_system` + `origin_account_id` if both are present. Kept
    # alongside the legacy fields so a view migrated from the old
    # shape can still be re-loaded by code paths that haven't been
    # updated yet — the route layer reads both and prefers this one.
    origin_account_keys: list[str] | None = None
    commercial_status: str | None = None
    marketing_consent: str | None = None
    is_active: bool | None = None
    lead_score_min: int | None = None
    lead_score_max: int | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    # Sprint UX: the segments-engine rules tree powers the new
    # Brevo-style query builder on /contacts. When present the route
    # layer prefers it over the legacy flat fields above; the legacy
    # fields stay so old views still re-load and the migration is
    # in-place rather than destructive.
    rules_json: dict[str, Any] | None = None


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


class ExternalReferenceSummary(BaseModel):
    """Compact origin marker for the contacts list — just enough to
    render the per-row chips without the full reference payload."""

    system: str
    account_id: str

    model_config = ConfigDict(from_attributes=True)


class ContactRead(ContactCreate):
    id: str
    is_email_valid: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Real source-system dates (NULL until a sync carries one). The UI
    # shows `created_at_external` as the contact's age, falling back to
    # the CRM `created_at` with a "del CRM" badge when absent.
    created_at_external: datetime | None = None
    updated_at_external: datetime | None = None
    # All origins the contact belongs to, as `(system, account_id)`
    # pairs. Read from the ORM's `external_references_summary` property;
    # the repository eager-loads `external_refs` for the list so this
    # doesn't trigger an N+1.
    external_references_summary: list[ExternalReferenceSummary] = Field(
        default_factory=list
    )
    # Real M:N tags exposed alongside the (deprecated, still readable)
    # `tags` CSV field. New code should consume `tag_objects`; the CSV
    # stays for backwards-compat during the migration. The Contact ORM
    # exposes a `tag_objects` Python property that flattens its
    # `tag_assignments` relationship to a list of Tag rows — Pydantic
    # picks it up automatically via from_attributes.
    tag_objects: list[TagRead] = Field(default_factory=list)
    # READ-time tolerance for email + phone. `ContactCreate` keeps
    # `EmailStr` (strict at write time), but historical rows imported
    # from AgileCRM may carry malformed strings like
    # "emete@emete@emete.cat". A strict `EmailStr` on the read path
    # crashes the whole list endpoint with 500 the moment one bad row
    # surfaces. Surfacing `None` instead — and logging the row id so
    # ops can audit later — keeps the rest of the page rendering.
    email: str | None = None  # type: ignore[assignment]
    phone: str | None = None
    # PR-Fix-Sync-No-Sobreescribe-Cambios-CRM. Lista de nombres de
    # campos que el operador editó manualmente desde el CRM y que
    # el sync de Agile/Brevo respeta (no sobreescribe). El frontend
    # lo usa para pintar el badge "Editado manualmente · no se
    # sobreescribe en sync" junto a cada campo del modal Editar.
    # Vacío / NULL = ningún campo bajo protección. El alias
    # `manually_edited_fields_json` recoge la columna TEXT JSON cruda
    # del ORM; el validator de abajo la parsea a list[str].
    manually_edited_fields: list[str] = Field(
        default_factory=list,
        validation_alias="manually_edited_fields_json",
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("manually_edited_fields", mode="before")
    @classmethod
    def coerce_manual_edits(cls, value: object, info: object) -> list[str]:  # noqa: ARG003
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return []
            return [str(v) for v in parsed] if isinstance(parsed, list) else []
        return []

    @field_validator("email", mode="before")
    @classmethod
    def coerce_tolerant_email(cls, value: object, info: object) -> str | None:  # noqa: ARG003
        return _coerce_tolerant_email(value)

    @field_validator("phone", mode="before")
    @classmethod
    def coerce_tolerant_phone(cls, value: object) -> str | None:
        return _coerce_tolerant_phone(value)


def _coerce_tolerant_email(value: object) -> str | None:
    """Return a normalised email if parseable, else `None`.

    Plus a warning log entry — the caller never sees a ValidationError
    from a strange DB row. The strict validation still lives in
    `ContactCreate` so this can't be abused for new inserts."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    candidate = value.strip()
    if not candidate:
        return None
    try:
        from email_validator import (  # noqa: PLC0415
            EmailNotValidError,
            validate_email,
        )
    except ImportError:  # pragma: no cover - shipped with pydantic[email]
        return candidate.lower()
    try:
        result = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError:
        logging.getLogger(__name__).warning(
            "contact.email malformed; surfacing None: %r", candidate
        )
        return None
    return result.normalized.lower()


def _coerce_tolerant_phone(value: object) -> str | None:
    """Drop obvious garbage (>30 chars OR no digit at all). Keeps the
    column lossy — exactly what the operator wants on a value that's
    a free-form notes field across half of the AgileCRM accounts."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    candidate = value.strip()
    if not candidate:
        return None
    if len(candidate) > 30 or not any(ch.isdigit() for ch in candidate):
        logging.getLogger(__name__).warning(
            "contact.phone looks malformed; surfacing None: %r", candidate
        )
        return None
    return candidate


class ContactListPage(BaseModel):
    """Paginated wrapper returned by `GET /api/contacts`. The list page in
    the frontend consumes `total` to render pagination controls without a
    separate `/count` round-trip."""

    items: list[ContactRead]
    total: int
    limit: int
    offset: int


class ContactViewSaveAsSegmentRequest(BaseModel):
    """Body for `POST /api/contact-views/{id}/save-as-segment`. The
    view's `filters_json` is reused verbatim as the segment's
    `rules_json`; the operator provides display fields."""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=7)
    is_shared: bool = False


class ContactViewPushToBrevoRequest(BaseModel):
    """Body for `POST /api/contact-views/{id}/push-to-brevo-list`.

    EITHER `brevo_list_id` (push into an existing Brevo list) OR
    `new_list_name` (create a fresh list then push). The endpoint
    validates exactly-one-of and 400s otherwise."""

    brevo_account_id: str = Field(min_length=1, max_length=64)
    brevo_list_id: int | None = None
    new_list_name: str | None = Field(default=None, max_length=200)


class ContactViewPushToBrevoResponse(BaseModel):
    sync_log_id: str
    job_id: str | None = None
    target_id: str
    segment_id: str
    contacts_to_push: int
    brevo_list_id: int


class ContactSearchRequest(BaseModel):
    """Body shape for `POST /api/contacts/search`. Mirrors the segments
    engine's `rules_json` tree exactly (the search endpoint reuses
    `build_filter` so any tree valid in `/api/segments/preview` works
    here unchanged).

    `rules_json` is OPTIONAL — an empty body returns every active
    contact, same as the bare `GET /api/contacts`. The query-builder UI
    on the contacts list starts with an empty tree and submits a body
    only when the operator adds at least one rule.
    """

    rules_json: dict[str, Any] | None = None
    sort_by: str = "created_at"
    sort_dir: str = "desc"
    limit: int = Field(default=25, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    include_inactive: bool = False
    # `q` rides alongside `rules_json` as a quick free-text filter (UI
    # search box). Backend applies it as an additional clause so the
    # operator can layer it over a saved view without rewriting the
    # tree.
    q: str | None = None
    # Toggle in the contacts list header that filters down to "only
    # contacts I own". Backend AND's `Contact.owner_user_id ==
    # current_user.id` into the rules tree's WHERE so the user
    # doesn't have to add the rule manually every time.
    assigned_to_me: bool = False


class TaskAssigneeRead(BaseModel):
    id: str
    full_name: str
    email: str

    model_config = ConfigDict(from_attributes=True)


class TaskContactRead(BaseModel):
    id: str
    first_name: str
    last_name: str | None = None
    email: str | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("email", mode="before")
    @classmethod
    def _tolerant_email(cls, value: object) -> str | None:
        return _coerce_tolerant_email(value)


class TaskCreate(BaseModel):
    """Payload for `POST /api/tasks`.

    `assigned_user_id` defaults to the caller at the route layer if
    omitted. `contact_id` / `company_id` / `pipeline_stage_id` are all
    optional links — the operator can build a personal todo with no
    CRM linkage and still get the calendar slot.

    `sync_with_google_calendar` triggers a best-effort mirror to the
    assignee's selected Google calendar after the task is persisted.
    """

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    due_at: datetime | None = None
    status: str = "pending"
    priority: str = "medium"
    assigned_user_id: str | None = None
    contact_id: str | None = None
    company_id: str | None = None
    pipeline_stage_id: str | None = None
    reminder_minutes_before: int | None = Field(default=None, ge=0, le=10080)
    sync_with_google_calendar: bool = False

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return value.strip()


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    due_at: datetime | None = None
    status: str | None = None
    priority: str | None = None
    assigned_user_id: str | None = None
    contact_id: str | None = None
    company_id: str | None = None
    pipeline_stage_id: str | None = None
    reminder_minutes_before: int | None = Field(default=None, ge=0, le=10080)
    # When provided, drives the sync side effect: True on an unsynced
    # task creates the event; False on a synced task deletes it and
    # clears `google_event_id`. Omit to leave the sync state alone.
    sync_with_google_calendar: bool | None = None


class TaskRead(BaseModel):
    id: str
    title: str
    description: str | None
    due_at: datetime | None
    status: str
    priority: str
    assigned_user_id: str
    assigned_user: TaskAssigneeRead | None = None
    contact_id: str | None
    contact: TaskContactRead | None = None
    company_id: str | None
    pipeline_stage_id: str | None
    created_by_user_id: str
    google_event_id: str | None = None
    google_calendar_id: str | None = None
    reminder_minutes_before: int | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskListPage(BaseModel):
    items: list[TaskRead]
    total: int
    limit: int
    offset: int


class TaskBuckets(BaseModel):
    """Output of `GET /api/tasks/my-buckets` — drives the dashboard
    widget. Counts ride alongside the trimmed item lists so the
    badge can show e.g. "12 overdue" without re-querying."""

    overdue: list[TaskRead] = Field(default_factory=list)
    today: list[TaskRead] = Field(default_factory=list)
    tomorrow: list[TaskRead] = Field(default_factory=list)
    later: list[TaskRead] = Field(default_factory=list)
    no_date: list[TaskRead] = Field(default_factory=list)
    total_open: int = 0


class TaskCompleteResponse(BaseModel):
    task: TaskRead


class GoogleCalendarItem(BaseModel):
    """One entry in the user's calendar list — feeds the post-OAuth
    setup screen."""

    id: str
    summary: str
    primary: bool = False
    access_role: str | None = None
    background_color: str | None = None


class GoogleCalendarSelection(BaseModel):
    """Currently selected calendar — embedded in the status payload."""

    id: str
    summary: str | None = None


class GoogleCalendarStatus(BaseModel):
    """Output of `GET /api/integrations/google/status`.

    Three states the UI cares about: not connected, connected without
    calendar (needs setup), fully wired. `configured` reflects the
    *server-side* config — when False the UI shows "Pide al admin que
    configure las credenciales".
    """

    configured: bool
    connected: bool
    google_email: str | None = None
    selected_calendar: GoogleCalendarSelection | None = None
    requires_calendar_selection: bool = False
    connected_at: datetime | None = None
    last_sync_at: datetime | None = None


class GoogleCalendarSelectPayload(BaseModel):
    calendar_id: str = Field(min_length=1, max_length=255)


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
    # Display label for the system ("AgileCRM", "Brevo", …) so the UI
    # doesn't carry its own slug→label map.
    system_label: str | None = None
    account_id: str
    account_label: str | None = None
    external_id: str
    contact_id: str
    external_created_at: datetime | None = None
    external_updated_at: datetime | None = None
    origin_detail: str | None = None
    # Deep link into the source system's UI, when we can build one
    # (Brevo always; AgileCRM when the account's base URL is known).
    # Filled by the contact detail endpoint, which has the account row.
    external_url: str | None = None
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
        from app.integrations.external_links import system_label  # noqa: PLC0415

        return {
            "id": data.id,
            "system": data.system,
            "system_label": system_label(data.system),
            "account_id": data.account_id,
            "account_label": data.account_label,
            "external_id": data.external_id,
            "contact_id": data.contact_id,
            "external_created_at": data.external_created_at,
            "external_updated_at": data.external_updated_at,
            "origin_detail": data.origin_detail,
            "external_url": None,
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
    # Frontend reads this flag to decide whether to render the
    # "Generar con IA" CTA. Never exposes the API key itself.
    ai_features_enabled: bool = False


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


class StalledContactRow(BaseModel):
    """Row in `GET /api/pipelines/{id}/stalled-contacts`. Cards the
    operator needs to nudge: the contact card + how many days they've
    been in the stage vs the SLA. The fields mirror what the kanban
    card uses so the same React renderer can be reused."""

    assignment_id: str
    contact_id: str
    first_name: str
    last_name: str | None = None
    email: str
    stage_id: str
    stage_name: str
    target_days: int
    days_in_stage: int
    overdue_days: int
    entered_stage_at: datetime


# ---------------------------------------------------------------------------
# Pipeline templates + AI generation (Sprint P.2.5)
# ---------------------------------------------------------------------------


class PipelineTemplateStage(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None
    is_won: bool = False
    is_lost: bool = False
    target_days: int | None = None


class PipelineTemplate(BaseModel):
    id: str
    name: str
    description: str
    category: str
    color: str | None = None
    stages: list[PipelineTemplateStage]


class PipelineFromTemplateRequest(BaseModel):
    template_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=100)


class PipelineGenerateAIRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)


class PipelineProposalStage(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None
    is_won: bool = False
    is_lost: bool = False
    target_days: int | None = None
    position: int


class PipelineProposal(BaseModel):
    """AI-generated pipeline that the operator inspects before saving.
    No DB row is written by `/generate-ai`; the proposal is round-
    tripped through the wizard which the operator then POSTs as a
    `PipelineCreate` (or via the template path) on confirm."""

    name: str
    description: str | None = None
    color: str | None = None
    stages: list[PipelineProposalStage]


# ---------------------------------------------------------------------------
# Segments (Sprint P.3)
# ---------------------------------------------------------------------------


class SegmentRuleNode(BaseModel):
    """The rule tree is a free-form dict. Pydantic only validates the
    outer envelope; the engine in
    `app/services/segments/engine.py` enforces the whitelist of
    fields + comparators before any SQL is generated."""

    model_config = ConfigDict(extra="allow")


class SegmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=7)
    is_shared: bool = False
    is_dynamic: bool = True
    rules: dict[str, Any] = Field(default_factory=dict)
    static_contact_ids: list[str] | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("color", mode="before")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        return _validate_tag_color(value)


class SegmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=7)
    is_shared: bool | None = None
    is_dynamic: bool | None = None
    rules: dict[str, Any] | None = None
    static_contact_ids: list[str] | None = None

    @field_validator("color", mode="before")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        return _validate_tag_color(value)


class SegmentRead(BaseModel):
    id: str
    name: str
    description: str | None = None
    color: str | None = None
    owner_user_id: str
    is_owner: bool = False
    is_shared: bool
    is_dynamic: bool
    rules: dict[str, Any] = Field(default_factory=dict)
    static_contact_ids: list[str] = Field(default_factory=list)
    cached_count: int | None = None
    last_evaluated_at: datetime | None = None
    # Set on Brevo-managed mirrors. `<system>:<account>:<external_id>`
    # — UI keys off this to hide the rule editor and show the
    # "Espejo Brevo" badge + refresh/open buttons.
    external_source: str | None = None
    external_last_refreshed_at: datetime | None = None
    external_refresh_interval_minutes: int | None = None
    created_at: datetime
    updated_at: datetime


class SegmentDuplicateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)


class SegmentPreviewRequest(BaseModel):
    rules: dict[str, Any]


class SegmentPreviewContactCard(BaseModel):
    id: str
    first_name: str
    last_name: str | None = None
    email: str
    lead_score: int | None = None


class SegmentPreviewResponse(BaseModel):
    count: int
    sample: list[SegmentPreviewContactCard]


class SegmentAIGenerateRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)


class SegmentAIGenerateResponse(BaseModel):
    rules: dict[str, Any] | None = None
    error: str | None = None
    count: int = 0
    sample: list[SegmentPreviewContactCard] = Field(default_factory=list)


class SegmentAIExplainRequest(BaseModel):
    rules: dict[str, Any] | None = None
    segment_id: str | None = None


class SegmentAIExplainResponse(BaseModel):
    explanation: str


class SegmentTemplate(BaseModel):
    id: str
    name: str
    description: str
    category: str
    color: str | None = None
    rules: dict[str, Any]


class SegmentFieldDescriptor(BaseModel):
    key: str
    label: str
    type: str
    comparators: list[str]
    enum_values: list[str] = Field(default_factory=list)


class SegmentCountryOption(BaseModel):
    """One ISO code / country name actually present in `contacts`. The
    builder uses this list to populate the `address_country` value
    dropdown so the operator picks from real data instead of typing a
    free-form string that ends up matching nothing."""

    code: str
    contact_count: int


class SegmentOriginAccountOption(BaseModel):
    """One enabled integration account, in the value/label shape the
    `origin_account_id` value picker expects. `value` is the raw
    `account_id` (what the engine compares against); `label` is the
    human display so the operator sees "AgileCRM · España" instead of
    a slug."""

    value: str
    label: str
    system: str


class IntegrationAccountSummary(BaseModel):
    """Per-account row inside `IntegrationSystemGroup`. The contact
    count comes from a `SELECT COUNT(...)` over `external_references`
    so two accounts pointing at overlapping contact sets each get
    credited for the contacts they actually carry references to."""

    account_id: str
    label: str
    contacts_count: int
    enabled: bool


class IntegrationSystemGroup(BaseModel):
    """One group inside `GET /api/integrations/accounts`. The frontend
    pickers render each system as a header followed by its accounts;
    flat lists got unmanageable at 9 AgileCRM accounts."""

    system: str
    system_label: str
    accounts: list[IntegrationAccountSummary] = Field(default_factory=list)
