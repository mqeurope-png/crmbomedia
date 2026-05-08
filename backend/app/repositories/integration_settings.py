from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationSetting

INTEGRATION_DISPLAY_NAMES = {
    ExternalSystem.AGILECRM: "AgileCRM",
    ExternalSystem.BREVO: "Brevo",
    ExternalSystem.FRESHDESK: "Freshdesk",
    ExternalSystem.FACTUSOL: "FactuSOL",
}


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
