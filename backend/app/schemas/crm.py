from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.crm import (
    ConsentStatus,
    ExternalSystem,
    IntegrationMode,
    IntegrationStatus,
    TaskStatus,
    UserRole,
)


class ErrorResponse(BaseModel):
    detail: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetRequestRead(BaseModel):
    message: str
    reset_token: str | None = None


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=16)
    new_password: str = Field(min_length=8, max_length=128)


class MessageRead(BaseModel):
    message: str


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.VIEWER
    is_active: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: UserRole | None = None
    is_active: bool | None = None

class UserPasswordUpdate(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class UserRead(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogRead(BaseModel):
    id: str
    actor_user_id: str | None
    action: str
    entity_type: str
    entity_id: str | None
    message: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


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


class IntegrationSettingUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    mode: IntegrationMode | None = None
    status: IntegrationStatus | None = None
    api_base_url: str | None = Field(default=None, max_length=255)
    account_label: str | None = Field(default=None, max_length=255)
    credential_status: str | None = Field(default=None, max_length=80)
    notes: str | None = None

    @field_validator("display_name")
    @classmethod
    def strip_optional_display_name(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class IntegrationSettingRead(BaseModel):
    id: str
    system: ExternalSystem
    display_name: str
    enabled: bool
    mode: IntegrationMode
    status: IntegrationStatus
    api_base_url: str | None
    account_label: str | None
    credential_status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HealthRead(BaseModel):
    status: str
    app_name: str
    environment: str
