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
# Webhook intake secret management (Sprint Webhooks Agile Real-Time).
# ---------------------------------------------------------------------------
#
# AgileCRM only — Brevo uses a single deployment-wide secret env var.
# The route validates that explicitly so the operator can't generate
# a secret for a system whose receiver doesn't use it.


def _webhook_intake_url(
    request: Request, account_id: str, token: str
) -> str:
    """Compose the externally-visible AgileCRM webhook URL the operator
    pastes into Agile. Honours `X-Forwarded-Proto` / `X-Forwarded-Host`
    so the value matches whatever the reverse proxy publishes; falls
    back to `request.url_for` otherwise."""
    scheme = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host") or request.url.hostname
    if not scheme:
        scheme = request.url.scheme
    base = f"{scheme}://{host}" if host else str(request.base_url).rstrip("/")
    return (
        f"{base}/api/webhooks/agilecrm/{account_id}/incoming?token={token}"
    )


@router.post("/{system}/{account_id}/webhook-secret/generate")
def generate_webhook_secret_route(
    system: ExternalSystem,
    account_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, object]:
    """Mint a fresh shared secret for an AgileCRM account that hasn't
    enabled real-time intake yet. Returns 409 if the column already
    holds a value — operators rotate via /regenerate instead so the
    diff between "first-time setup" and "rotation" stays visible in
    the audit log."""
    from app.integrations.agilecrm.webhook_intake import (  # noqa: PLC0415
        generate_webhook_secret,
    )

    if system != ExternalSystem.AGILECRM:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook-secret management is AgileCRM-only.",
        )
    account = get_integration_account(session, system, account_id)
    if not account:
        raise not_found("Integration account")
    if account.webhook_secret:
        raise conflict(
            "Account already has a webhook secret — use /regenerate to rotate."
        )

    secret = generate_webhook_secret()
    account.webhook_secret = secret
    record_event(
        session,
        action=Action.INTEGRATION_WEBHOOK_SECRET_GENERATED,
        target_type="integration_account",
        target_id=account.id,
        actor=current_user,
        metadata=_audit_metadata(account),
        request=request,
    )
    session.commit()
    return {
        "url": _webhook_intake_url(request, account.account_id, secret),
        "secret": secret,
    }


@router.post("/{system}/{account_id}/webhook-secret/regenerate")
def regenerate_webhook_secret_route(
    system: ExternalSystem,
    account_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, object]:
    """Rotate the secret. Invalidates the previous one immediately —
    the operator must re-paste the new URL into AgileCRM."""
    from app.integrations.agilecrm.webhook_intake import (  # noqa: PLC0415
        generate_webhook_secret,
    )

    if system != ExternalSystem.AGILECRM:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook-secret management is AgileCRM-only.",
        )
    account = get_integration_account(session, system, account_id)
    if not account:
        raise not_found("Integration account")

    secret = generate_webhook_secret()
    account.webhook_secret = secret
    record_event(
        session,
        action=Action.INTEGRATION_WEBHOOK_SECRET_REGENERATED,
        target_type="integration_account",
        target_id=account.id,
        actor=current_user,
        metadata=_audit_metadata(account),
        request=request,
    )
    session.commit()
    return {
        "url": _webhook_intake_url(request, account.account_id, secret),
        "secret": secret,
    }


@router.delete("/{system}/{account_id}/webhook-secret")
def delete_webhook_secret_route(
    system: ExternalSystem,
    account_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, str]:
    """Disable real-time intake by clearing the secret. The receiver
    starts returning `status=skipped` for this account; the periodic
    sync keeps running."""
    if system != ExternalSystem.AGILECRM:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook-secret management is AgileCRM-only.",
        )
    account = get_integration_account(session, system, account_id)
    if not account:
        raise not_found("Integration account")
    account.webhook_secret = None
    record_event(
        session,
        action=Action.INTEGRATION_WEBHOOK_SECRET_REGENERATED,
        target_type="integration_account",
        target_id=account.id,
        actor=current_user,
        metadata=_audit_metadata(account, cleared=True),
        request=request,
    )
    session.commit()
    return {"status": "cleared"}


@router.get("/{system}/{account_id}/webhook-stats")
def webhook_stats_route(
    system: ExternalSystem,
    account_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, object]:
    """Counts surfaced on the admin card. Today/total/last-received
    plus a success-rate over the last 24 h so an operator notices a
    silently broken integration without scrolling the audit log."""
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from sqlalchemy import func  # noqa: PLC0415

    from app.models.webhook_events import (  # noqa: PLC0415
        WebhookEvent,
        WebhookEventStatus,
    )

    _ = current_user
    account = get_integration_account(session, system, account_id)
    if not account:
        raise not_found("Integration account")

    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    last_24h = now - timedelta(hours=24)

    base = select(func.count(WebhookEvent.id)).where(
        WebhookEvent.system == system.value,
        WebhookEvent.account_id == account.account_id,
    )
    total = int(session.scalar(base) or 0)
    today = int(
        session.scalar(base.where(WebhookEvent.received_at >= midnight))
        or 0
    )
    last_received_at = session.scalar(
        select(func.max(WebhookEvent.received_at)).where(
            WebhookEvent.system == system.value,
            WebhookEvent.account_id == account.account_id,
        )
    )
    last_24h_total = int(
        session.scalar(base.where(WebhookEvent.received_at >= last_24h))
        or 0
    )
    last_24h_processed = int(
        session.scalar(
            base.where(
                WebhookEvent.received_at >= last_24h,
                WebhookEvent.status == WebhookEventStatus.PROCESSED,
            )
        )
        or 0
    )
    success_rate = (
        round(last_24h_processed / last_24h_total, 4)
        if last_24h_total > 0
        else None
    )

    return {
        "received_total": total,
        "received_today": today,
        "received_last_24h": last_24h_total,
        "processed_last_24h": last_24h_processed,
        "success_rate_last_24h": success_rate,
        "last_received_at": last_received_at.isoformat()
        if last_received_at
        else None,
        "has_secret": bool(account.webhook_secret),
        "enabled": account.enabled,
    }


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
