# ruff: noqa: I001
import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.gdpr import router as gdpr_router
from app.api.integration_settings import (
    deprecated_router as integration_settings_deprecated_router,
    router as integration_accounts_router,
)
from app.api.sync import router as sync_router
from app.api.webhooks import router as webhooks_router
from app.core.audit import Action, record_event
from app.core.auth import (
    get_current_user,
    require_admin,
    require_manager,
    require_user,
    require_viewer,
)
from app.core.config import Settings, get_settings
from app.core.crypto import decrypt, encrypt
from app.core.errors import conflict, not_found, unauthorized
from app.core.security import (
    PRE_2FA_TOKEN_TTL_MINUTES,
    create_access_token,
    create_reset_token,
    decode_access_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.core.totp import (
    build_provisioning_uri,
    generate_backup_codes,
    generate_secret,
    hash_backup_codes,
    verify_and_consume_backup_code,
    verify_totp_code,
)
from app.db.session import get_session
from app.models.crm import Company, Contact, ExternalSystem, Note, Task, User
from app.repositories import crm as crm_repository
from app.services.email import EmailService, get_email_service
from app.schemas.crm import (
    ActivityEventListPage,
    ActivityEventRead,
    AuditLogRead,
    ChangePasswordRequest,
    CompanyCreate,
    CompanyRead,
    CompanyUpdate,
    ContactCreate,
    ContactDetailRead,
    ContactListPage,
    ContactRead,
    ContactUpdate,
    CountRead,
    CurrentUserRead,
    ErrorResponse,
    HealthRead,
    LoginRequest,
    MessageRead,
    NoteCreate,
    NoteRead,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetRequestRead,
    TaskCreate,
    TaskRead,
    TokenRead,
    TotpConfirmRead,
    TotpConfirmRequest,
    TotpDisableRequest,
    TotpSetupRead,
    TotpVerifyRequest,
    UserCreate,
    UserPasswordUpdate,
    UserRead,
    UserUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()
ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication required"},
    403: {"model": ErrorResponse, "description": "Not enough permissions"},
    404: {"model": ErrorResponse, "description": "Resource not found"},
    409: {"model": ErrorResponse, "description": "Conflict with an existing resource"},
}


EXPORT_MAX_ROWS = 50_000
EXPORT_DEFAULT_WINDOW_DAYS = 365


@router.get("/health", response_model=HealthRead, tags=["system"])
def health(settings: Settings = Depends(get_settings)) -> HealthRead:
    return HealthRead(status="ok", app_name=settings.app_name, environment=settings.environment)

@router.post("/auth/login", response_model=TokenRead, responses=ERROR_RESPONSES, tags=["auth"])
def login(
    payload: LoginRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> TokenRead:
    attempted_email = str(payload.email)
    user = crm_repository.get_user_by_email(session, attempted_email)
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        # Audit the failed attempt with the captured email + IP + UA so an
        # operator can spot brute-force patterns. The DB-level user lookup
        # may have miss; we still record the email actually tried.
        reason = "user_not_found" if not user else (
            "user_inactive" if not user.is_active else "invalid_password"
        )
        record_event(
            session,
            action=Action.AUTH_LOGIN_FAILED,
            target_type="user",
            target_id=user.id if user else None,
            actor=user,
            actor_email=attempted_email,
            metadata={"reason": reason},
            request=request,
        )
        session.commit()
        raise unauthorized("Invalid email or password")
    record_event(
        session,
        action=Action.AUTH_LOGIN_SUCCESS,
        target_type="user",
        target_id=user.id,
        actor=user,
        request=request,
    )
    session.commit()

    if user.totp_enabled:
        # 2FA enrolled → return a short-lived token good only for /2fa/verify.
        temp_token = create_access_token(
            subject=user.id,
            role=user.role.value,
            expires_minutes=PRE_2FA_TOKEN_TTL_MINUTES,
            pre_2fa=True,
        )
        return TokenRead(access_token=temp_token, requires_2fa=True)

    # 2FA is fully optional, including for admins. Anyone without 2FA gets a
    # normal access token; the `limited` claim is never set anymore. The flag
    # is left in place in create_access_token so old tokens with limited=true
    # still parse cleanly while they live out their 8-hour TTL.
    token = create_access_token(subject=user.id, role=user.role.value)
    return TokenRead(access_token=token, limited=False)


@router.post(
    "/auth/2fa/verify",
    response_model=TokenRead,
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
def verify_2fa(
    payload: TotpVerifyRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> TokenRead:
    """Second step of login: exchanges a pre-2FA token + TOTP code (or a
    backup code) for the final JWT. The temp token must come from
    /auth/login on a user that has totp_enabled=true.

    The temp token travels inside the request body (not in the Authorization
    header) because the client doesn't yet have a "session" — this endpoint
    is the moment that session is created.
    """
    decoded = decode_access_token(payload.temp_token)
    if not decoded or not decoded.get("pre_2fa") or not decoded.get("sub"):
        raise unauthorized("Invalid or expired 2FA session")
    user = session.get(User, decoded["sub"])
    if not user or not user.is_active:
        raise unauthorized()
    if not user.totp_enabled or not user.totp_secret_encrypted:
        raise unauthorized("2FA is not enabled for this account")

    cleaned = payload.code.strip()
    secret = decrypt(user.totp_secret_encrypted)
    ok = verify_totp_code(secret, cleaned)
    used_backup = False
    if not ok:
        consumed, remaining_json = verify_and_consume_backup_code(
            user.backup_codes_hash, cleaned
        )
        if consumed:
            user.backup_codes_hash = remaining_json
            ok = True
            used_backup = True

    if not ok:
        raise unauthorized("Invalid 2FA code")

    record_event(
        session,
        action=(
            Action.AUTH_2FA_VERIFIED_BACKUP_CODE
            if used_backup
            else Action.AUTH_2FA_VERIFIED
        ),
        target_type="user",
        target_id=user.id,
        actor=user,
        request=request,
    )
    session.commit()

    token = create_access_token(subject=user.id, role=user.role.value)
    return TokenRead(access_token=token)


@router.post(
    "/auth/2fa/setup",
    response_model=TotpSetupRead,
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
def setup_2fa(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(get_current_user),
) -> TotpSetupRead:
    """Generate a fresh secret and return the provisioning URI for QR display.

    The secret is encrypted at rest immediately; `totp_enabled` stays false
    until /auth/2fa/confirm verifies the user has actually scanned it.
    Re-runs are allowed only when 2FA is NOT yet enabled (use /2fa/disable
    first to rotate)."""
    if current_user.totp_enabled:
        raise conflict("2FA is already enabled; disable it first to rotate the secret")
    secret = generate_secret()
    current_user.totp_secret_encrypted = encrypt(secret)
    current_user.totp_confirmed_at = None
    record_event(
        session,
        action=Action.AUTH_2FA_SETUP_STARTED,
        target_type="user",
        target_id=current_user.id,
        actor=current_user,
        request=request,
    )
    session.commit()
    uri = build_provisioning_uri(
        secret,
        account_name=current_user.email,
        issuer=settings.app_name,
    )
    return TotpSetupRead(secret=secret, otpauth_uri=uri)


@router.post(
    "/auth/2fa/confirm",
    response_model=TotpConfirmRead,
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
def confirm_2fa(
    payload: TotpConfirmRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TotpConfirmRead:
    """Verify a code from the authenticator app, flip totp_enabled to true,
    and return the freshly generated backup codes (shown once)."""
    if current_user.totp_enabled:
        raise conflict("2FA is already enabled")
    if not current_user.totp_secret_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run /auth/2fa/setup before confirming",
        )
    secret = decrypt(current_user.totp_secret_encrypted)
    if not verify_totp_code(secret, payload.code):
        raise unauthorized("Invalid TOTP code")
    current_user.totp_enabled = True
    current_user.totp_confirmed_at = datetime.now(UTC)
    codes = generate_backup_codes()
    current_user.backup_codes_hash = hash_backup_codes(codes)
    record_event(
        session,
        action=Action.AUTH_2FA_ENABLED,
        target_type="user",
        target_id=current_user.id,
        actor=current_user,
        request=request,
    )
    session.commit()
    return TotpConfirmRead(backup_codes=codes, enabled=True)


@router.post(
    "/auth/2fa/disable",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
def disable_2fa(
    payload: TotpDisableRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MessageRead:
    """Disabling 2FA requires re-authenticating with the current password —
    a stolen session cookie alone can't downgrade the account."""
    if not verify_password(payload.password, current_user.password_hash):
        raise unauthorized("Invalid password")
    current_user.totp_secret_encrypted = None
    current_user.totp_enabled = False
    current_user.totp_confirmed_at = None
    current_user.backup_codes_hash = None
    record_event(
        session,
        action=Action.AUTH_2FA_DISABLED,
        target_type="user",
        target_id=current_user.id,
        actor=current_user,
        request=request,
    )
    session.commit()
    return MessageRead(message="2FA disabled")


@router.post(
    "/auth/change-password",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MessageRead:
    if not verify_password(payload.current_password, current_user.password_hash):
        raise unauthorized("Invalid current password")
    current_user.password_hash = hash_password(payload.new_password)
    current_user.password_reset_token_hash = None
    current_user.password_reset_requested_at = None
    record_event(
        session,
        action=Action.AUTH_PASSWORD_CHANGED,
        target_type="user",
        target_id=current_user.id,
        actor=current_user,
        request=request,
    )
    session.commit()
    return MessageRead(message="Password changed")


@router.post(
    "/auth/password-reset/request",
    tags=["auth"],
    responses={
        200: {
            "model": PasswordResetRequestRead,
            "description": (
                "Development / test environments only: returns the reset token in the body so "
                "Codespaces and the CI suite can complete the flow without an email service."
            ),
        },
        202: {
            "model": MessageRead,
            "description": (
                "Production: request accepted. The response is the same regardless of whether "
                "the email exists, to prevent account enumeration. The token is delivered out "
                "of band (email)."
            ),
        },
    },
)
def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    email_service: EmailService = Depends(get_email_service),
) -> JSONResponse:
    is_production = settings.environment.lower() == "production"
    user = crm_repository.get_user_by_email(session, str(payload.email))
    reset_token: str | None = None

    if user and user.is_active:
        reset_token = create_reset_token()
        user.password_reset_token_hash = hash_reset_token(reset_token)
        user.password_reset_requested_at = datetime.now(UTC)
        record_event(
            session,
            action=Action.AUTH_PASSWORD_RESET_REQUESTED,
            target_type="user",
            target_id=user.id,
            actor=user,
            request=request,
        )
        session.commit()

        try:
            email_service.send_password_reset(
                to_email=user.email,
                to_name=user.full_name,
                token=reset_token,
            )
        except Exception as exc:  # noqa: BLE001 - we want to swallow any provider error
            # Production: never reveal whether the email exists; just log so an
            # operator can investigate. Dev: noisy stack so the failure is obvious.
            if is_production:
                logger.warning(
                    "Password reset email could not be delivered for user_id=%s: %s",
                    user.id,
                    exc,
                )
            else:
                logger.error(
                    "Password reset email failed for user_id=%s",
                    user.id,
                    exc_info=True,
                )

    if is_production:
        # Always 202 + neutral message to avoid revealing whether the email exists.
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"message": "If the email exists, a reset link has been sent."},
        )

    # Development / test: keep the legacy behaviour so the existing flow can be
    # exercised end-to-end without an email service.
    if reset_token is None:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"message": "If the user exists, a reset token was generated"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "message": "Password reset token generated",
            "reset_token": reset_token,
        },
    )


@router.post(
    "/auth/password-reset/confirm",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
def confirm_password_reset(
    payload: PasswordResetConfirm,
    request: Request,
    session: Session = Depends(get_session),
) -> MessageRead:
    token_hash = hash_reset_token(payload.token)
    user = crm_repository.get_user_by_reset_token_hash(session, token_hash)
    if not user or not user.is_active:
        raise unauthorized("Invalid reset token")
    user.password_hash = hash_password(payload.new_password)
    user.password_reset_token_hash = None
    user.password_reset_requested_at = None
    record_event(
        session,
        action=Action.AUTH_PASSWORD_RESET_CONFIRMED,
        target_type="user",
        target_id=user.id,
        actor=user,
        request=request,
    )
    session.commit()
    return MessageRead(message="Password reset completed")


@router.get(
    "/auth/me", response_model=CurrentUserRead, responses=ERROR_RESPONSES, tags=["auth"]
)
def read_current_user(current_user: User = Depends(get_current_user)) -> CurrentUserRead:
    return CurrentUserRead(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
        is_active=current_user.is_active,
        totp_enabled=current_user.totp_enabled,
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
        # 2FA is opt-in for every role; the field is kept in the response for
        # backward compatibility and is always False.
        requires_2fa_setup=False,
    )


@router.post(
    "/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["users"],
)
def create_user(
    payload: UserCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> User:
    email = str(payload.email).lower()
    if crm_repository.get_user_by_email(session, email):
        raise conflict("A user with this email already exists")
    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=payload.is_active,
    )
    session.add(user)
    session.flush()
    record_event(
        session,
        action=Action.USER_CREATED,
        target_type="user",
        target_id=user.id,
        actor=current_user,
        metadata={
            "target_email": user.email,
            "target_role": user.role.value,
        },
        request=request,
    )
    session.commit()
    session.refresh(user)
    return user


@router.get("/users", response_model=list[UserRead], responses=ERROR_RESPONSES, tags=["users"])
def list_users(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> list[User]:
    _ = current_user
    return crm_repository.list_users(session=session, skip=skip, limit=limit)


@router.patch(
    "/users/{user_id}",
    response_model=UserRead,
    responses=ERROR_RESPONSES,
    tags=["users"],
)
def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> User:
    user = session.get(User, user_id)
    if not user:
        raise not_found("User")
    changes = payload.model_dump(exclude_unset=True)
    role_before = user.role
    for field, value in changes.items():
        if field == "full_name" and value is not None:
            value = value.strip()
        setattr(user, field, value)
    record_event(
        session,
        action=Action.USER_UPDATED,
        target_type="user",
        target_id=user.id,
        actor=current_user,
        metadata={
            "target_email": user.email,
            "changed_fields": sorted(changes.keys()),
        },
        request=request,
    )
    # When the role actually flips, write a dedicated audit row so role
    # transitions are easy to filter for compliance reports.
    if "role" in changes and changes["role"] != role_before:
        record_event(
            session,
            action=Action.USER_ROLE_CHANGED,
            target_type="user",
            target_id=user.id,
            actor=current_user,
            metadata={
                "target_email": user.email,
                "from_role": role_before.value,
                "to_role": user.role.value,
            },
            request=request,
        )
    session.commit()
    session.refresh(user)
    return user


@router.patch(
    "/users/{user_id}/password",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["users"],
)
def admin_update_user_password(
    user_id: str,
    payload: UserPasswordUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> MessageRead:
    user = session.get(User, user_id)
    if not user:
        raise not_found("User")
    user.password_hash = hash_password(payload.new_password)
    user.password_reset_token_hash = None
    user.password_reset_requested_at = None
    record_event(
        session,
        action=Action.USER_PASSWORD_SET_BY_ADMIN,
        target_type="user",
        target_id=user.id,
        actor=current_user,
        metadata={"target_email": user.email},
        request=request,
    )
    session.commit()
    return MessageRead(message="Password updated")


@router.patch(
    "/users/{user_id}/deactivate",
    response_model=UserRead,
    responses=ERROR_RESPONSES,
    tags=["users"],
)
def deactivate_user(
    user_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> User:
    user = session.get(User, user_id)
    if not user:
        raise not_found("User")
    user.is_active = False
    record_event(
        session,
        action=Action.USER_DEACTIVATED,
        target_type="user",
        target_id=user.id,
        actor=current_user,
        metadata={"target_email": user.email},
        request=request,
    )
    session.commit()
    session.refresh(user)
    return user


@router.patch(
    "/users/{user_id}/reactivate",
    response_model=UserRead,
    responses=ERROR_RESPONSES,
    tags=["users"],
)
def reactivate_user(
    user_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> User:
    user = session.get(User, user_id)
    if not user:
        raise not_found("User")
    user.is_active = True
    record_event(
        session,
        action=Action.USER_REACTIVATED,
        target_type="user",
        target_id=user.id,
        actor=current_user,
        metadata={"target_email": user.email},
        request=request,
    )
    session.commit()
    session.refresh(user)
    return user


@router.get(
    "/audit-logs",
    response_model=list[AuditLogRead],
    responses=ERROR_RESPONSES,
    tags=["audit"],
)
def list_audit_logs(
    response: Response,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    action: str | None = Query(default=None, description="Exact action match"),
    action_prefix: str | None = Query(
        default=None, description="Action prefix filter (e.g. auth.)"
    ),
    actor_user_id: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    from_date: datetime | None = Query(default=None, alias="from"),
    to_date: datetime | None = Query(default=None, alias="to"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> list[AuditLogRead]:
    _ = current_user
    logs = crm_repository.list_audit_logs(
        session=session,
        skip=skip,
        limit=limit,
        action=action,
        action_prefix=action_prefix,
        actor_user_id=actor_user_id,
        target_type=target_type,
        from_date=from_date,
        to_date=to_date,
    )
    total = crm_repository.count_audit_logs(
        session=session,
        action=action,
        action_prefix=action_prefix,
        actor_user_id=actor_user_id,
        target_type=target_type,
        from_date=from_date,
        to_date=to_date,
    )
    response.headers["X-Total-Count"] = str(total)
    return [AuditLogRead.from_audit_log(log) for log in logs]


@router.get(
    "/audit-logs/export",
    responses=ERROR_RESPONSES,
    tags=["audit"],
)
def export_audit_logs(
    request: Request,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    action: str | None = Query(default=None),
    action_prefix: str | None = Query(default=None),
    actor_user_id: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    from_date: datetime | None = Query(default=None, alias="from"),
    to_date: datetime | None = Query(default=None, alias="to"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> Response:
    # Default to the last 12 months when the caller doesn't specify a
    # range — keeps the export bounded even for old install where the
    # table has grown unbounded.
    if from_date is None and to_date is None:
        to_date = datetime.now(UTC)
        from_date = to_date - timedelta(days=EXPORT_DEFAULT_WINDOW_DAYS)

    logs = crm_repository.list_audit_logs_for_export(
        session=session,
        max_rows=EXPORT_MAX_ROWS,
        action=action,
        action_prefix=action_prefix,
        actor_user_id=actor_user_id,
        target_type=target_type,
        from_date=from_date,
        to_date=to_date,
    )
    if len(logs) > EXPORT_MAX_ROWS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Export exceeds {EXPORT_MAX_ROWS} rows. Narrow the range with "
                f"`from` / `to` or filter by `action` / `actor_user_id`."
            ),
        )

    # The export itself is an audited action: who pulled how many rows,
    # for what filters, when.
    record_event(
        session,
        action=Action.AUDIT_EXPORTED,
        target_type="audit_log",
        actor=current_user,
        metadata={
            "format": format,
            "rows": len(logs),
            "filters": {
                "action": action,
                "action_prefix": action_prefix,
                "actor_user_id": actor_user_id,
                "target_type": target_type,
                "from": from_date.isoformat() if from_date else None,
                "to": to_date.isoformat() if to_date else None,
            },
        },
        request=request,
    )
    session.commit()

    rows = [
        {
            "id": log.id,
            "actor_user_id": log.actor_user_id or "",
            "actor_email": log.actor_email or "",
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id or "",
            "metadata": log.metadata_json or "",
            "message": log.message or "",
            "ip_address": log.ip_address or "",
            "user_agent": log.user_agent or "",
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]
    if format == "json":
        return Response(
            content=json.dumps(rows),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=audit_logs.json"},
        )
    header = [
        "id",
        "actor_user_id",
        "actor_email",
        "action",
        "target_type",
        "target_id",
        "metadata",
        "message",
        "ip_address",
        "user_agent",
        "created_at",
    ]
    csv_lines = [",".join(header)]
    for row in rows:
        # Strip embedded commas and newlines so the CSV parses without quoting;
        # for production-grade CSV pipelines the consumer should already cope,
        # but defensive escaping keeps spreadsheets happy.
        csv_lines.append(
            ",".join(
                str(row[key]).replace(",", " ").replace("\n", " ").replace("\r", " ")
                for key in header
            )
        )
    return Response(
        content="\n".join(csv_lines) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )


@router.post(
    "/companies",
    response_model=CompanyRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def create_company(
    payload: CompanyCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Company:
    company = Company(**payload.model_dump())
    session.add(company)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict("A company with this tax_id already exists") from exc
    record_event(
        session,
        action=Action.COMPANY_CREATED,
        target_type="company",
        target_id=company.id,
        actor=current_user,
        metadata={"name": company.name},
        request=request,
    )
    session.commit()
    session.refresh(company)
    return company


@router.get("/companies", response_model=list[CompanyRead], responses=ERROR_RESPONSES, tags=["crm"])
def list_companies(
    q: str | None = Query(default=None, description="Filtro por nombre"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[Company]:
    _ = current_user
    return crm_repository.list_companies(
        session=session, q=q, skip=skip, limit=limit, include_inactive=include_inactive
    )


@router.get(
    "/companies/count",
    response_model=CountRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def count_companies(
    q: str | None = Query(default=None, description="Filtro por nombre"),
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> CountRead:
    """Total de empresas que pasarían los mismos filtros que
    `GET /companies`. El dashboard lo consulta para los stat-cards en
    vez de tomar `len(items)` de la primera página paginada."""
    _ = current_user
    total = crm_repository.count_companies(
        session=session, q=q, include_inactive=include_inactive
    )
    return CountRead(total=total)


@router.patch(
    "/companies/{company_id}",
    response_model=CompanyRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def update_company(
    company_id: str,
    payload: CompanyUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Company:
    company = crm_repository.get_company(session, company_id)
    if not company:
        raise not_found("Company")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(company, field, value)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict("A company with this tax_id already exists") from exc
    record_event(
        session,
        action=Action.COMPANY_UPDATED,
        target_type="company",
        target_id=company.id,
        actor=current_user,
        metadata={"name": company.name, "changed_fields": sorted(changes.keys())},
        request=request,
    )
    session.commit()
    session.refresh(company)
    return company


@router.patch(
    "/companies/{company_id}/deactivate",
    response_model=CompanyRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def deactivate_company(
    company_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Company:
    company = crm_repository.get_company(session, company_id)
    if not company:
        raise not_found("Company")
    company.is_active = False
    record_event(
        session,
        action=Action.COMPANY_DEACTIVATED,
        target_type="company",
        target_id=company.id,
        actor=current_user,
        metadata={"name": company.name},
        request=request,
    )
    session.commit()
    session.refresh(company)
    return company


@router.post(
    "/contacts",
    response_model=ContactRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def create_contact(
    payload: ContactCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Contact:
    email = str(payload.email).lower()
    if crm_repository.get_contact_by_email(session, email):
        raise conflict("A contact with this email already exists")
    if payload.company_id and not crm_repository.get_company(session, payload.company_id):
        raise not_found("Company")

    data = payload.model_dump()
    data["email"] = email
    contact = Contact(**data)
    session.add(contact)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict("A contact with this email already exists") from exc
    record_event(
        session,
        action=Action.CONTACT_CREATED,
        target_type="contact",
        target_id=contact.id,
        actor=current_user,
        metadata={"email": contact.email},
        request=request,
    )
    session.commit()
    session.refresh(contact)
    return contact


@router.get(
    "/contacts",
    response_model=ContactListPage,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_contacts(
    q: str | None = Query(
        default=None, description="Busca por nombre, apellidos, email o teléfono"
    ),
    tag: str | None = Query(default=None, description="Filtra por un tag exacto del CSV `tags`"),
    origin_system: ExternalSystem | None = Query(
        default=None,
        description="Filtra por sistema de origen vía external_references.system",
    ),
    origin_account_id: str | None = Query(
        default=None,
        description="Filtra por cuenta de integración vía external_references.account_id",
    ),
    commercial_status: str | None = Query(default=None, max_length=80),
    marketing_consent: str | None = Query(default=None, max_length=40),
    sort_by: str = Query(
        default="created_at",
        description="created_at | updated_at | name | email",
    ),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> ContactListPage:
    _ = current_user
    items = crm_repository.list_contacts(
        session=session,
        q=q,
        tag=tag,
        origin_system=origin_system,
        origin_account_id=origin_account_id,
        commercial_status=commercial_status,
        marketing_consent=marketing_consent,
        skip=skip,
        limit=limit,
        include_inactive=include_inactive,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    total = crm_repository.count_contacts(
        session=session,
        q=q,
        tag=tag,
        origin_system=origin_system,
        origin_account_id=origin_account_id,
        commercial_status=commercial_status,
        marketing_consent=marketing_consent,
        include_inactive=include_inactive,
    )
    return ContactListPage(
        items=[ContactRead.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=skip,
    )


@router.get(
    "/contacts/count",
    response_model=CountRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def count_contacts(
    q: str | None = Query(default=None, description="Busca por nombre, apellidos o email"),
    tag: str | None = Query(default=None),
    origin_system: ExternalSystem | None = Query(default=None),
    origin_account_id: str | None = Query(default=None),
    commercial_status: str | None = Query(default=None, max_length=80),
    marketing_consent: str | None = Query(default=None, max_length=40),
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> CountRead:
    """Total de contactos que pasarían los mismos filtros que
    `GET /contacts`. El dashboard lo consulta para mostrar el contador
    real en vez de la longitud de la primera página paginada."""
    _ = current_user
    total = crm_repository.count_contacts(
        session=session,
        q=q,
        tag=tag,
        origin_system=origin_system,
        origin_account_id=origin_account_id,
        commercial_status=commercial_status,
        marketing_consent=marketing_consent,
        include_inactive=include_inactive,
    )
    return CountRead(total=total)


@router.get(
    "/contacts/{contact_id}",
    response_model=ContactDetailRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def get_contact(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> ContactDetailRead:
    _ = current_user
    bundle = crm_repository.get_contact_with_timeline(
        session,
        contact_id,
        timeline_limit=crm_repository.ACTIVITY_EVENTS_INLINE_LIMIT,
    )
    if bundle is None:
        raise not_found("Contact")
    contact, events, _total = bundle
    # FastAPI would call model_validate on `contact` for us, but the
    # latest events live on a sibling list — we hand-build the response
    # so the timeline tail is always exactly the rows the dashboard
    # paginates over.
    detail = ContactDetailRead.model_validate(contact)
    detail.activity_events = [ActivityEventRead.model_validate(e) for e in events]
    return detail


@router.get(
    "/contacts/{contact_id}/activity-events",
    response_model=ActivityEventListPage,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_contact_activity_events(
    contact_id: str,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> ActivityEventListPage:
    """Full paginated timeline for one contact. The detail endpoint
    embeds the most recent 50 events; this one is the "Ver todos"
    backing call."""
    _ = current_user
    if not crm_repository.get_contact(session, contact_id):
        raise not_found("Contact")
    items = crm_repository.list_activity_events(
        session, contact_id, skip=skip, limit=limit
    )
    total = crm_repository.count_activity_events(session, contact_id)
    return ActivityEventListPage(
        items=[ActivityEventRead.model_validate(e) for e in items],
        total=total,
        limit=limit,
        offset=skip,
    )


@router.patch(
    "/contacts/{contact_id}",
    response_model=ContactRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def update_contact(
    contact_id: str,
    payload: ContactUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Contact:
    contact = crm_repository.get_contact(session, contact_id)
    if not contact:
        raise not_found("Contact")
    data = payload.model_dump(exclude_unset=True)
    if "email" in data and data["email"] is not None:
        email = str(data["email"]).lower()
        existing = crm_repository.get_contact_by_email(session, email)
        if existing and existing.id != contact.id:
            raise conflict("A contact with this email already exists")
        data["email"] = email
    if data.get("company_id") and not crm_repository.get_company(session, data["company_id"]):
        raise not_found("Company")
    for field, value in data.items():
        setattr(contact, field, value)
    record_event(
        session,
        action=Action.CONTACT_UPDATED,
        target_type="contact",
        target_id=contact.id,
        actor=current_user,
        metadata={"email": contact.email, "changed_fields": sorted(data.keys())},
        request=request,
    )
    session.commit()
    session.refresh(contact)
    return contact


@router.patch(
    "/contacts/{contact_id}/deactivate",
    response_model=ContactRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def deactivate_contact(
    contact_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Contact:
    contact = crm_repository.get_contact(session, contact_id)
    if not contact:
        raise not_found("Contact")
    contact.is_active = False
    record_event(
        session,
        action=Action.CONTACT_DEACTIVATED,
        target_type="contact",
        target_id=contact.id,
        actor=current_user,
        metadata={"email": contact.email},
        request=request,
    )
    session.commit()
    session.refresh(contact)
    return contact


@router.get(
    "/contacts/{contact_id}/notes",
    response_model=list[NoteRead],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_contact_notes(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[Note]:
    _ = current_user
    if not crm_repository.get_contact(session, contact_id):
        raise not_found("Contact")
    return crm_repository.list_notes(session, contact_id)


@router.post(
    "/contacts/{contact_id}/notes",
    response_model=NoteRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def create_note(
    contact_id: str,
    payload: NoteCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Note:
    if not crm_repository.get_contact(session, contact_id):
        raise not_found("Contact")
    data = payload.model_dump()
    data["author_user_id"] = data.get("author_user_id") or current_user.id
    note = Note(contact_id=contact_id, **data)
    session.add(note)
    session.flush()
    record_event(
        session,
        action=Action.NOTE_CREATED,
        target_type="note",
        target_id=note.id,
        actor=current_user,
        metadata={"contact_id": contact_id},
        request=request,
    )
    session.commit()
    session.refresh(note)
    return note


@router.get(
    "/contacts/{contact_id}/tasks",
    response_model=list[TaskRead],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_contact_tasks(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[Task]:
    _ = current_user
    if not crm_repository.get_contact(session, contact_id):
        raise not_found("Contact")
    return crm_repository.list_tasks(session, contact_id)


@router.post(
    "/contacts/{contact_id}/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def create_task(
    contact_id: str,
    payload: TaskCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Task:
    if not crm_repository.get_contact(session, contact_id):
        raise not_found("Contact")
    task = Task(contact_id=contact_id, **payload.model_dump())
    session.add(task)
    session.flush()
    record_event(
        session,
        action=Action.TASK_CREATED,
        target_type="task",
        target_id=task.id,
        actor=current_user,
        metadata={"contact_id": contact_id, "title": task.title},
        request=request,
    )
    session.commit()
    session.refresh(task)
    return task


router.include_router(integration_accounts_router)
router.include_router(integration_settings_deprecated_router)
router.include_router(sync_router)
router.include_router(webhooks_router)
router.include_router(gdpr_router)
