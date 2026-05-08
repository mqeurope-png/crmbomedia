from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Boolean, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, ExternalSystem, TimestampMixin, enum_values


class IntegrationMode(StrEnum):
    SANDBOX = "sandbox"
    LIVE = "live"


class IntegrationStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    CONFIGURED = "configured"
    PAUSED = "paused"


class IntegrationSetting(TimestampMixin, Base):
    __tablename__ = "integration_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    system: Mapped[ExternalSystem] = mapped_column(
        Enum(ExternalSystem, native_enum=False, values_callable=enum_values, length=32),
        nullable=False,
        unique=True,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mode: Mapped[IntegrationMode] = mapped_column(
        Enum(IntegrationMode, native_enum=False, values_callable=enum_values, length=32),
        default=IntegrationMode.SANDBOX,
        nullable=False,
    )
    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(IntegrationStatus, native_enum=False, values_callable=enum_values, length=32),
        default=IntegrationStatus.NOT_CONFIGURED,
        nullable=False,
    )
    api_base_url: Mapped[str | None] = mapped_column(String(255))
    account_label: Mapped[str | None] = mapped_column(String(255))
    credential_status: Mapped[str] = mapped_column(
        String(80), default="not_configured", nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
