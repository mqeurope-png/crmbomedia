from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
