# ruff: noqa: I001
from app.core.audit import Action, record_event
from app.core.auth import require_admin, require_manager
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import ExternalSystem, User
from app.models.integration_settings import IntegrationSetting
from app.repositories.integration_settings import (
    clear_api_key,
    get_integration_setting as get_setting,
    list_integration_settings as list_settings,
    set_api_key,
)
from app.schemas.integration_settings import (
    IntegrationApiKeyUpdate,
    IntegrationSettingRead,
    IntegrationSettingUpdate,
)
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/integration-settings", tags=["integration settings"])


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
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> IntegrationSetting:
    setting = get_setting(session, system)
    if not setting:
        raise not_found("Integration setting")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(setting, field, value)
    record_event(
        session,
        action=Action.INTEGRATION_SETTING_UPDATED,
        target_type="integration_setting",
        target_id=setting.id,
        actor=current_user,
        metadata={"system": setting.system.value, "changed_fields": sorted(changes.keys())},
        request=request,
    )
    session.commit()
    session.refresh(setting)
    return setting


@router.put(
    "/{system}/api-key",
    response_model=IntegrationSettingRead,
    status_code=status.HTTP_200_OK,
)
def set_integration_api_key(
    system: ExternalSystem,
    payload: IntegrationApiKeyUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> IntegrationSetting:
    setting = get_setting(session, system)
    if not setting:
        raise not_found("Integration setting")
    set_api_key(session, setting, payload.api_key)
    record_event(
        session,
        action=Action.INTEGRATION_API_KEY_SET,
        target_type="integration_setting",
        target_id=setting.id,
        actor=current_user,
        # NEVER include the API key value (plaintext or ciphertext) in metadata.
        metadata={"system": setting.system.value},
        request=request,
    )
    session.commit()
    session.refresh(setting)
    return setting


@router.delete(
    "/{system}/api-key",
    response_model=IntegrationSettingRead,
    status_code=status.HTTP_200_OK,
)
def delete_integration_api_key(
    system: ExternalSystem,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> IntegrationSetting:
    setting = get_setting(session, system)
    if not setting:
        raise not_found("Integration setting")
    clear_api_key(session, setting)
    record_event(
        session,
        action=Action.INTEGRATION_API_KEY_DELETED,
        target_type="integration_setting",
        target_id=setting.id,
        actor=current_user,
        metadata={"system": setting.system.value},
        request=request,
    )
    session.commit()
    session.refresh(setting)
    return setting
