from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import encrypt
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationSetting

INTEGRATION_DISPLAY_NAMES = {
    ExternalSystem.AGILECRM: "AgileCRM",
    ExternalSystem.BREVO: "Brevo",
    ExternalSystem.FRESHDESK: "Freshdesk",
    ExternalSystem.FACTUSOL: "FactuSOL",
}

CREDENTIAL_STATUS_NOT_CONFIGURED = "not_configured"
CREDENTIAL_STATUS_CONFIGURED = "configured"
CREDENTIAL_STATUS_VERIFIED = "verified"
CREDENTIAL_STATUS_ERROR = "error"


def ensure_integration_settings(session: Session) -> list[IntegrationSetting]:
    existing = {setting.system: setting for setting in session.scalars(select(IntegrationSetting))}
    for system, display_name in INTEGRATION_DISPLAY_NAMES.items():
        if system not in existing:
            setting = IntegrationSetting(system=system, display_name=display_name)
            session.add(setting)
            existing[system] = setting
    session.flush()
    return list(existing.values())


def list_integration_settings(session: Session) -> list[IntegrationSetting]:
    ensure_integration_settings(session)
    statement = select(IntegrationSetting).order_by(IntegrationSetting.display_name)
    return list(session.scalars(statement))


def get_integration_setting(session: Session, system: ExternalSystem) -> IntegrationSetting | None:
    ensure_integration_settings(session)
    return session.scalar(select(IntegrationSetting).where(IntegrationSetting.system == system))


def set_api_key(
    session: Session, setting: IntegrationSetting, plaintext_api_key: str
) -> IntegrationSetting:
    setting.api_key_encrypted = encrypt(plaintext_api_key)
    setting.api_key_set_at = datetime.now(UTC)
    setting.api_key_last_used_at = None
    setting.credential_status = CREDENTIAL_STATUS_CONFIGURED
    session.flush()
    return setting


def clear_api_key(session: Session, setting: IntegrationSetting) -> IntegrationSetting:
    setting.api_key_encrypted = None
    setting.api_key_set_at = None
    setting.api_key_last_used_at = None
    setting.credential_status = CREDENTIAL_STATUS_NOT_CONFIGURED
    session.flush()
    return setting


def touch_api_key_use(session: Session, setting: IntegrationSetting) -> None:
    setting.api_key_last_used_at = datetime.now(UTC)
    session.flush()
