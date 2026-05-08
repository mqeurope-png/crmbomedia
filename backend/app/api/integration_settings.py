# ruff: noqa: I001
from app.core.auth import require_admin, require_manager
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import ExternalSystem, User
from app.models.integration_settings import IntegrationSetting
from app.repositories.integration_settings import (
    get_integration_setting as get_setting,
)
from app.repositories.integration_settings import (
    list_integration_settings as list_settings,
)
from app.schemas.integration_settings import (
    IntegrationSettingRead,
    IntegrationSettingUpdate,
)
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

router = APIRouter(prefix="/integration-settings", tags=["integration settings"])


def record_integration_settings_audit(
    session: Session,
    actor: User,
    setting: IntegrationSetting,
) -> None:
    # Reuse the existing audit repository in the scaffold without creating external side effects.
    from app.repositories import crm as crm_repository

    crm_repository.create_audit_log(
        session=session,
        actor_user_id=actor.id,
        action="update_integration_setting",
        entity_type="integration_setting",
        entity_id=setting.id,
        message=setting.system.value,
    )


@router.get("", response_model=list[IntegrationSettingRead])
def list_integration_settings(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> list[IntegrationSetting]:
    _ = current_user
    settings = list_settings(session)
    session.commit()
    return settings


@router.get("/{system}", response_model=IntegrationSettingRead)
def read_integration_setting(
    system: ExternalSystem,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> IntegrationSetting:
    _ = current_user
    setting = get_setting(session, system)
    session.commit()
    if not setting:
        raise not_found("Integration setting")
    return setting


@router.patch("/{system}", response_model=IntegrationSettingRead)
def update_integration_setting(
    system: ExternalSystem,
    payload: IntegrationSettingUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> IntegrationSetting:
    setting = get_setting(session, system)
    if not setting:
        raise not_found("Integration setting")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(setting, field, value)
    record_integration_settings_audit(session, current_user, setting)
    session.commit()
    session.refresh(setting)
    return setting
