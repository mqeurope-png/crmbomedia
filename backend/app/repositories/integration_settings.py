"""Repository for the multi-account `integration_accounts` table.

Each row is identified by the composite `(system, account_id)`. Helpers
that used to look up by `system` only now require an `account_id` too.
The legacy single-row-per-system data is preserved on the migration as
rows with `account_id='default'`, so existing helpers still work as long
as the caller passes `account_id='default'`.
"""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import encrypt
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationAccount

CREDENTIAL_STATUS_NOT_CONFIGURED = "not_configured"
CREDENTIAL_STATUS_CONFIGURED = "configured"
CREDENTIAL_STATUS_VERIFIED = "verified"
CREDENTIAL_STATUS_ERROR = "error"

# Human-readable names used when the migration inserts the bootstrap
# `default` row for each system that hasn't been provisioned yet.
DEFAULT_DISPLAY_NAMES = {
    ExternalSystem.AGILECRM: "AgileCRM",
    ExternalSystem.BREVO: "Brevo",
    ExternalSystem.FRESHDESK: "Freshdesk",
    ExternalSystem.FACTUSOL: "FactuSOL",
}


def list_integration_accounts(
    session: Session,
    *,
    system: ExternalSystem | None = None,
    enabled: bool | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[IntegrationAccount]:
    statement = select(IntegrationAccount)
    if system is not None:
        statement = statement.where(IntegrationAccount.system == system)
    if enabled is not None:
        statement = statement.where(IntegrationAccount.enabled.is_(enabled))
    statement = (
        statement.order_by(
            IntegrationAccount.system,
            IntegrationAccount.sync_priority,
            IntegrationAccount.display_name,
        )
        .offset(skip)
        .limit(limit)
    )
    return list(session.scalars(statement))


def count_integration_accounts(
    session: Session,
    *,
    system: ExternalSystem | None = None,
    enabled: bool | None = None,
) -> int:
    from sqlalchemy import func

    statement = select(func.count()).select_from(IntegrationAccount)
    if system is not None:
        statement = statement.where(IntegrationAccount.system == system)
    if enabled is not None:
        statement = statement.where(IntegrationAccount.enabled.is_(enabled))
    return int(session.scalar(statement) or 0)


def get_integration_account(
    session: Session, system: ExternalSystem, account_id: str
) -> IntegrationAccount | None:
    return session.scalar(
        select(IntegrationAccount).where(
            IntegrationAccount.system == system,
            IntegrationAccount.account_id == account_id,
        )
    )


def get_integration_account_by_natural_key(
    session: Session, system: ExternalSystem, account_id: str
) -> IntegrationAccount | None:
    """Alias kept for readability in connector code; identical to
    `get_integration_account`."""
    return get_integration_account(session, system, account_id)


def set_api_key(
    session: Session, account: IntegrationAccount, plaintext_api_key: str
) -> IntegrationAccount:
    account.api_key_encrypted = encrypt(plaintext_api_key)
    account.api_key_set_at = datetime.now(UTC)
    account.api_key_last_used_at = None
    account.credential_status = CREDENTIAL_STATUS_CONFIGURED
    session.flush()
    return account


def clear_api_key(
    session: Session, account: IntegrationAccount
) -> IntegrationAccount:
    account.api_key_encrypted = None
    account.api_key_set_at = None
    account.api_key_last_used_at = None
    account.credential_status = CREDENTIAL_STATUS_NOT_CONFIGURED
    session.flush()
    return account


def touch_api_key_use(session: Session, account: IntegrationAccount) -> None:
    account.api_key_last_used_at = datetime.now(UTC)
    session.flush()


# ---------------------------------------------------------------------------
# Backwards-compatible aliases (legacy callers expect *_setting* names)
# ---------------------------------------------------------------------------

INTEGRATION_DISPLAY_NAMES = DEFAULT_DISPLAY_NAMES


def list_integration_settings(session: Session) -> list[IntegrationAccount]:
    return list_integration_accounts(session)


def get_integration_setting(
    session: Session, system: ExternalSystem
) -> IntegrationAccount | None:
    """Legacy lookup — returns the row with `account_id='default'`."""
    return get_integration_account(session, system, "default")
