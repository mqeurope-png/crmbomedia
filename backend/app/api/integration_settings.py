# ruff: noqa: I001
"""HTTP layer for the multi-account integration module.

Routes live under `/api/integration-accounts` with the natural key
`(system, account_id)`. The legacy `/api/integration-settings` namespace
is kept as a deprecated alias that returns HTTP 410 Gone — see the
bottom of this file.
"""
from sqlalchemy import select

from app.core.audit import Action, record_event
from app.core.auth import require_admin, require_manager
from app.core.errors import conflict, not_found
from app.db.session import get_session
from app.models.crm import ExternalSystem, User
from app.models.crm import ExternalReference
from app.models.integration_settings import IntegrationAccount
from app.repositories.integration_settings import (
    clear_api_key,
    count_integration_accounts,
    get_integration_account,
    list_integration_accounts,
    set_api_key,
)
from app.schemas.integration_settings import (
    IntegrationAccountCreate,
    IntegrationAccountRead,
    IntegrationAccountUpdate,
    IntegrationApiKeyUpdate,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/integration-accounts", tags=["integration accounts"])


def _audit_metadata(account: IntegrationAccount, **extra: object) -> dict[str, object]:
    """Standardised metadata: system + account_id always present so audit
    queries can pivot by either dimension."""
    payload: dict[str, object] = {
        "system": account.system.value,
        "account_id": account.account_id,
    }
    payload.update(extra)
    return payload


@router.get("", response_model=list[IntegrationAccountRead])
def list_accounts(
    response: Response,
    system: ExternalSystem | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> list[IntegrationAccount]:
    _ = current_user
    accounts = list_integration_accounts(
        session, system=system, enabled=enabled, skip=skip, limit=limit
    )
    response.headers["X-Total-Count"] = str(
        count_integration_accounts(session, system=system, enabled=enabled)
    )
    return accounts


@router.get("/{system}/{account_id}", response_model=IntegrationAccountRead)
def read_account(
    system: ExternalSystem,
    account_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> IntegrationAccount:
    _ = current_user
    account = get_integration_account(session, system, account_id)
    if not account:
        raise not_found("Integration account")
    return account


@router.post(
    "/{system}",
    response_model=IntegrationAccountRead,
    status_code=status.HTTP_201_CREATED,
)
def create_account(
    system: ExternalSystem,
    payload: IntegrationAccountCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> IntegrationAccount:
    if get_integration_account(session, system, payload.account_id):
        raise conflict(
            f"Account '{payload.account_id}' already exists for system '{system.value}'"
        )
    account = IntegrationAccount(
        system=system,
        account_id=payload.account_id,
        display_name=payload.display_name,
        enabled=payload.enabled,
        mode=payload.mode,
        api_base_url=payload.api_base_url,
        account_label=payload.account_label,
        auth_identifier=payload.auth_identifier,
        notes=payload.notes,
        quota_max_contacts=payload.quota_max_contacts,
        quota_strategy=payload.quota_strategy,
        sync_priority=payload.sync_priority,
    )
    session.add(account)
    session.flush()
    record_event(
        session,
        action=Action.INTEGRATION_ACCOUNT_CREATED,
        target_type="integration_account",
        target_id=account.id,
        actor=current_user,
        metadata=_audit_metadata(account, display_name=account.display_name),
        request=request,
    )
    session.commit()
    session.refresh(account)
    return account


@router.patch("/{system}/{account_id}", response_model=IntegrationAccountRead)
def update_account(
    system: ExternalSystem,
    account_id: str,
    payload: IntegrationAccountUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> IntegrationAccount:
    account = get_integration_account(session, system, account_id)
    if not account:
        raise not_found("Integration account")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(account, field, value)
    # `auth_identifier` is plain metadata, but if the operator clears
    # it to empty string treat that as "unset" so the column stays NULL
    # instead of accumulating empty strings.
    if account.auth_identifier == "":
        account.auth_identifier = None
    record_event(
        session,
        action=Action.INTEGRATION_ACCOUNT_UPDATED,
        target_type="integration_account",
        target_id=account.id,
        actor=current_user,
        metadata=_audit_metadata(account, changed_fields=sorted(changes.keys())),
        request=request,
    )
    session.commit()
    session.refresh(account)
    return account


@router.delete(
    "/{system}/{account_id}",
    response_model=IntegrationAccountRead,
)
def delete_account(
    system: ExternalSystem,
    account_id: str,
    request: Request,
    force: bool = Query(
        default=False,
        description="Force delete even if there are external_references pointing at this account.",
    ),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> IntegrationAccount:
    account = get_integration_account(session, system, account_id)
    if not account:
        raise not_found("Integration account")
    # Refuse to delete accounts that may still own data. The check is
    # conservative: any external_reference for the same system is treated
    # as a possible link until per-account tracking lands.
    references = session.scalar(
        select(ExternalReference)
        .where(ExternalReference.system == system)
        .limit(1)
    )
    if references and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Integration account '{account.account_id}' has external references; "
                "pass ?force=true if you are certain you want to delete it."
            ),
        )
    target_id = account.id
    metadata = _audit_metadata(account, display_name=account.display_name, force=force)
    response = IntegrationAccountRead.model_validate(account)
    session.delete(account)
    session.flush()
    record_event(
        session,
        action=Action.INTEGRATION_ACCOUNT_DELETED,
        target_type="integration_account",
        target_id=target_id,
        actor=current_user,
        metadata=metadata,
        request=request,
    )
    session.commit()
    return response  # type: ignore[return-value]


@router.put(
    "/{system}/{account_id}/api-key",
    response_model=IntegrationAccountRead,
)
def set_account_api_key(
    system: ExternalSystem,
    account_id: str,
    payload: IntegrationApiKeyUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> IntegrationAccount:
    account = get_integration_account(session, system, account_id)
    if not account:
        raise not_found("Integration account")
    set_api_key(session, account, payload.api_key)
    record_event(
        session,
        action=Action.INTEGRATION_ACCOUNT_API_KEY_SET,
        target_type="integration_account",
        target_id=account.id,
        actor=current_user,
        # NEVER include the API key value (plaintext or ciphertext) in metadata.
        metadata=_audit_metadata(account),
        request=request,
    )
    session.commit()
    session.refresh(account)
    return account


@router.delete(
    "/{system}/{account_id}/api-key",
    response_model=IntegrationAccountRead,
)
def delete_account_api_key(
    system: ExternalSystem,
    account_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> IntegrationAccount:
    account = get_integration_account(session, system, account_id)
    if not account:
        raise not_found("Integration account")
    clear_api_key(session, account)
    record_event(
        session,
        action=Action.INTEGRATION_ACCOUNT_API_KEY_DELETED,
        target_type="integration_account",
        target_id=account.id,
        actor=current_user,
        metadata=_audit_metadata(account),
        request=request,
    )
    session.commit()
    session.refresh(account)
    return account


# ---------------------------------------------------------------------------
# Deprecated alias: /api/integration-settings/...
# The old single-account namespace is gone. We keep a thin router that
# answers 410 Gone with a hint pointing operators at the new prefix so
# misconfigured clients fail loudly instead of silently.
# ---------------------------------------------------------------------------

deprecated_router = APIRouter(
    prefix="/integration-settings", tags=["integration accounts"]
)


@deprecated_router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
def deprecated_settings(path: str):
    _ = path
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "/api/integration-settings has been replaced by /api/integration-accounts; "
            "see docs/integrations.md for the migration."
        ),
    )


@deprecated_router.api_route(
    "",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
def deprecated_settings_root():
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "/api/integration-settings has been replaced by /api/integration-accounts; "
            "see docs/integrations.md for the migration."
        ),
    )
