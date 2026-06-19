"""Multi-account integration model.

One row per external account (e.g. one AgileCRM account per market,
one Freshdesk per team). The table key is the composite
`(system, account_id)`; `account_id` is a human-readable slug chosen by
the operator. The original `integration_settings` table is renamed to
`integration_accounts` in migration 20260515_0007 with every legacy row
preserved as `account_id='default'`.
"""
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, ExternalSystem, TimestampMixin, enum_values


class IntegrationMode(StrEnum):
    SANDBOX = "sandbox"
    LIVE = "live"


class IntegrationStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    CONFIGURED = "configured"
    PAUSED = "paused"


class QuotaStrategy(StrEnum):
    """How the connector should react when `quota_max_contacts` is hit.

    `keep_newest`: drop the oldest contacts to make room.
    `keep_oldest`: refuse to push new contacts beyond the cap.
    `none`: log a warning but otherwise ignore the cap (default when
    `quota_max_contacts` is not set).
    """

    KEEP_NEWEST = "keep_newest"
    KEEP_OLDEST = "keep_oldest"
    NONE = "none"


class IntegrationAccount(TimestampMixin, Base):
    __tablename__ = "integration_accounts"
    __table_args__ = (
        UniqueConstraint("system", "account_id", name="uq_integration_accounts_system_account_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    system: Mapped[ExternalSystem] = mapped_column(
        Enum(ExternalSystem, native_enum=False, values_callable=enum_values, length=32),
        nullable=False,
        index=True,
    )
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
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
    # Non-secret companion to `api_key_encrypted`. AgileCRM (and similar
    # vendors) need a user identifier — typically an email — to compose
    # HTTP Basic auth. Stored in plaintext because it's metadata, not a
    # secret. Nullable for every system; only AgileCRM currently
    # requires it.
    auth_identifier: Mapped[str | None] = mapped_column(String(255))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    api_key_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    api_key_last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    quota_max_contacts: Mapped[int | None] = mapped_column(Integer)
    quota_strategy: Mapped[QuotaStrategy | None] = mapped_column(
        Enum(QuotaStrategy, native_enum=False, values_callable=enum_values, length=32),
        nullable=True,
    )
    sync_priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    # Sprint Webhooks Agile Real-Time. Per-account shared secret carried
    # as a `?token=` query param on the webhook URL. AgileCRM webhooks
    # don't sign the body, so this is the cheapest reasonably-private
    # auth: rotated on demand from the admin UI, never sent to logs.
    # NULL = real-time intake disabled; the periodic sync still runs.
    webhook_secret: Mapped[str | None] = mapped_column(String(64))
    webhook_last_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


# Backwards-compatible alias for any caller still importing the old name.
# All first-party code now imports `IntegrationAccount` directly.
IntegrationSetting = IntegrationAccount
