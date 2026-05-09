from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationMode, IntegrationStatus


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


class IntegrationApiKeyUpdate(BaseModel):
    api_key: str = Field(..., min_length=1, max_length=4096)

    @field_validator("api_key")
    @classmethod
    def strip_api_key(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("api_key must not be blank")
        return stripped


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
    api_key_set_at: datetime | None = None
    api_key_last_used_at: datetime | None = None
    api_key_encrypted: str | None = Field(default=None, exclude=True, repr=False)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_api_key(self) -> bool:
        return self.api_key_encrypted is not None and self.api_key_encrypted != ""
