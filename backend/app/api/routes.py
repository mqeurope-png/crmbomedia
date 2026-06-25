# ruff: noqa: I001
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.brevo import (
    _require_brevo_account as require_brevo_account,
    router as brevo_router,
)
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
from app.integrations.errors import IntegrationError, IntegrationServerError
from app.models.crm import (
    Company,
    Contact,
    ContactPipelineStage,
    ContactTag,
    ContactView,
    CustomFieldDefinition,
    ExternalReference,
    ExternalSystem,
    Pipeline,
    PipelineStage,
    Segment,
    TaskStatus,
    User,
    UserRole,
)
from app.models.integration_settings import IntegrationAccount
from app.repositories import contact_views as contact_views_repository
from app.repositories import crm as crm_repository
from app.repositories import pipelines as pipelines_repository
from app.repositories import segments as segments_repository
from app.workers.jobs import enqueue_sync_job
from app.services import assignment_rules as assignment_rules_engine
from app.services import llm as llm_service
from app.services import pipeline_templates as pipeline_templates_service
from app.services.email import EmailService, get_email_service
from app.services.segments import engine as segment_engine
from app.services.segments import fields as segment_fields
from app.services.segments import templates as segments_templates
from app.schemas.crm import (
    ActivityEventListPage,
    ActivityEventRead,
    AuditLogRead,
    BulkContactTagRequest,
    BulkContactTagResult,
    ChangePasswordRequest,
    CompanyCreate,
    CompanyRead,
    CompanyUpdate,
    ContactCreate,
    ContactDetailRead,
    ContactListPage,
    ContactRead,
    ContactSearchRequest,
    ContactTagAssignRequest,
    ContactViewPushToBrevoRequest,
    ContactViewPushToBrevoResponse,
    ContactViewSaveAsSegmentRequest,
    ContactUpdate,
    ContactPipelineAddRequest,
    ContactPipelineMoveRequest,
    ContactPipelineStageRead,
    ContactPipelineSummary,
    ContactViewCreate,
    ContactViewDuplicateRequest,
    ContactViewFilters,
    ContactViewRead,
    ContactViewUpdate,
    CountRead,
    ExternalReferenceRead,
    ExternalRefreshRead,
    PipelineContactCard,
    PipelineContactsResponse,
    PipelineCreate,
    PipelineDuplicateRequest,
    PipelineRead,
    PipelineReportResponse,
    PipelineStageCreate,
    PipelineStageGroup,
    PipelineStageMetric,
    PipelineStageRead,
    PipelineStageReorderRequest,
    PipelineStageUpdate,
    PipelineFromTemplateRequest,
    PipelineGenerateAIRequest,
    PipelineProposal,
    PipelineProposalStage,
    PipelineTemplate,
    PipelineUpdate,
    SegmentAIExplainRequest,
    SegmentAIExplainResponse,
    SegmentAIGenerateRequest,
    SegmentAIGenerateResponse,
    SegmentCountryOption,
    SegmentCreate,
    SegmentDuplicateRequest,
    SegmentFieldDescriptor,
    SegmentOriginAccountOption,
    SegmentPreviewContactCard,
    SegmentPreviewRequest,
    SegmentPreviewResponse,
    SegmentRead,
    SegmentTemplate,
    SegmentUpdate,
    StalledContactRow,
    TagCreate,
    TagDetailRead,
    TagListPage,
    TagRead,
    TagUpdate,
    CurrentUserRead,
    ErrorResponse,
    HealthRead,
    UserPreferencesRead,
    UserPreferencesWrite,
    IntegrationAccountSummary,
    IntegrationSystemGroup,
    LoginRequest,
    MessageRead,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetRequestRead,
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
    return HealthRead(
        status="ok",
        app_name=settings.app_name,
        environment=settings.environment,
        ai_features_enabled=settings.ai_features_enabled,
    )

# PR-F: cookie de sesión nombrada `bohub_token`. Sin Max-Age para que
# el browser la borre al cerrar la ventana completa (Bart spec — cierre
# de UNA pestaña con otras abiertas debe preservarla). Read by the
# frontend para inyectar el `Authorization: Bearer` header — backend
# sigue autenticando vía Bearer. `secure` se activa cuando la request
# entró por HTTPS (Nginx setea X-Forwarded-Proto en prod).
_AUTH_COOKIE_NAME = "bohub_token"


def _set_auth_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        key=_AUTH_COOKIE_NAME,
        value=token,
        path="/",
        max_age=None,
        expires=None,
        secure=request.url.scheme == "https",
        httponly=False,
        samesite="lax",
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=_AUTH_COOKIE_NAME, path="/")


@router.post("/auth/login", response_model=TokenRead, responses=ERROR_RESPONSES, tags=["auth"])
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
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
        # NO seteamos cookie aquí — el pre-2FA token no debe permitir
        # acceso a endpoints normales. El cookie definitivo se setea al
        # completar /auth/2fa/verify.
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
    _set_auth_cookie(response, token, request)
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
    response: Response,
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
    _set_auth_cookie(response, token, request)
    return TokenRead(access_token=token)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["auth"],
)
def logout(response: Response) -> Response:
    """Clear the session cookie. PR-F idle timeout + cierre de ventana
    llaman aquí desde el frontend. El backend no mantiene estado de
    sesión (JWT stateless), así que el server-side es solo el
    Set-Cookie con Max-Age=0."""
    _clear_auth_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


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
        email_include_unsubscribe_default=(
            current_user.email_include_unsubscribe_default
        ),
    )


@router.get(
    "/users/me/preferences",
    response_model=UserPreferencesRead,
    responses=ERROR_RESPONSES,
    tags=["users"],
)
def read_my_preferences(
    current_user: User = Depends(get_current_user),
) -> UserPreferencesRead:
    return UserPreferencesRead.model_validate(current_user)


@router.put(
    "/users/me/preferences",
    response_model=UserPreferencesRead,
    responses=ERROR_RESPONSES,
    tags=["users"],
)
def update_my_preferences(
    payload: UserPreferencesWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UserPreferencesRead:
    current_user.email_include_unsubscribe_default = (
        payload.email_include_unsubscribe_default
    )
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return UserPreferencesRead.model_validate(current_user)


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
    # Email travels through `EmailStr` (RFC 5321 normalised) and we
    # additionally trim + lowercase. The repository lookup is also
    # case-insensitive, so a casing variant of an existing email won't
    # silently slip past — historically the conflict surfaced as the
    # opaque IntegrityError on the unique index.
    email = str(payload.email).strip().lower()
    existing = crm_repository.get_user_by_email(session, email)
    if existing is not None:
        # The previous wording ("A user with this email already
        # exists") was confusing when the existing row was a
        # soft-deleted user (`is_active=False`): the operator saw a
        # collision on an email they "knew" they had freed. Split the
        # two cases and point at the reactivation flow when applicable.
        if not existing.is_active:
            raise conflict(
                "A deactivated user with this email already exists. "
                "Reactivate it from the users list or pick a different "
                "email — the `email` column is uniquely indexed."
            )
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
    # PR-Cg: `q` para alimentar el UserPicker server-side; el
    # admin module sigue funcionando sin pasarlo. `require_viewer`
    # en lugar de `require_admin` porque el picker de owner se usa
    # desde la lista de contactos (cualquier usuario logueado debe
    # poder ver la lista de usuarios para asignar / filtrar). El
    # PATCH/DELETE siguen siendo admin-only.
    q: str | None = Query(
        default=None,
        description="Filtro substring case-insensitive sobre email + full_name",
    ),
    skip: int = Query(default=0, ge=0),
    # PR-Ea hotfix: cap subido de 100 a 500. Pickers de la UI
    # (RuleEditor primary/secundarios, OwnerPicker bulk, etc.) cargan
    # la lista entera de usuarios activos para mostrarla sin paginar.
    # 500 cubre cualquier equipo realista; con autocomplete server-side
    # vía `q` el coste de un dropdown es despreciable.
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[User]:
    _ = current_user
    return crm_repository.list_users(session=session, skip=skip, limit=limit, q=q)


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


@router.get("/contacts/custom-field-keys")
def list_contact_custom_field_keys(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[dict[str, str]]:
    """PR-Fixes-Pase-3 Bug 6 + Pase-4 Bug 6. Devuelve la unión de
    claves del catálogo `custom_field_definitions` (creadas
    manualmente desde admin u otros integradores) y las que el
    inspector encuentra en `contacts.custom_fields` (típicamente
    importadas de AgileCRM). Cada entrada lleva `source` para que el
    dropdown del editor anote el origen al usuario.

    Tipo inferido (`text`/`number`/`date`/`boolean`):
        - Definiciones manuales: `field_type` de la fila.
        - Inferido del primer valor no-null encontrado en contactos."""
    _ = current_user

    # 1) Definiciones explícitas — tienen prioridad sobre lo inferido.
    keys: dict[str, dict[str, str]] = {}
    for definition in session.scalars(select(CustomFieldDefinition)):
        keys[definition.key] = {
            "key": definition.key,
            "type": definition.field_type or "text",
            "source": definition.source or "manual",
            "label": definition.label or definition.key,
        }

    # 2) Lo que veamos en los contactos, sin pisar definiciones.
    rows = list(session.scalars(select(Contact.custom_fields)))
    for raw in rows:
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        for k, v in parsed.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if k in keys:
                continue
            keys[k] = {
                "key": k,
                "type": _infer_field_type(v),
                "source": "inferred",
                "label": k,
            }
    return [keys[k] for k in sorted(keys)]


def _infer_field_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        # Heurística mínima: ISO 8601 (YYYY-MM-DD) → date.
        if len(value) >= 10 and value[4] == "-" and value[7] == "-":
            return "date"
        return "text"
    return "text"


# ---------------------------------------------------------------------
# PR-Fixes-Pase-4 Bug 6 — admin CRUD para definiciones de custom fields
# ---------------------------------------------------------------------


_ALLOWED_CUSTOM_FIELD_TYPES = {"text", "number", "date", "boolean"}


@router.get("/admin/custom-fields", tags=["admin"])
def list_custom_field_definitions(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> list[dict[str, str | None]]:
    """Lista las definiciones manuales (las inferidas no viven aquí).
    Solo admins porque crear/borrar es admin-only y mantener la lista
    centralizada simplifica permisos."""
    _ = current_user
    rows = list(
        session.scalars(
            select(CustomFieldDefinition).order_by(
                CustomFieldDefinition.key
            )
        )
    )
    return [
        {
            "id": d.id,
            "key": d.key,
            "label": d.label,
            "type": d.field_type,
            "source": d.source,
            "description": d.description,
        }
        for d in rows
    ]


@router.post(
    "/admin/custom-fields",
    status_code=status.HTTP_201_CREATED,
    tags=["admin"],
)
def create_custom_field_definition(
    payload: dict[str, str],
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, str | None]:
    key = (payload.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    if len(key) > 120:
        raise HTTPException(status_code=400, detail="key too long")
    field_type = (payload.get("type") or "text").lower()
    if field_type not in _ALLOWED_CUSTOM_FIELD_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"type must be one of {sorted(_ALLOWED_CUSTOM_FIELD_TYPES)}"
            ),
        )
    label = (payload.get("label") or "").strip() or None
    description = (payload.get("description") or "").strip() or None
    existing = session.scalar(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.key == key
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"custom field '{key}' already exists"
        )
    definition = CustomFieldDefinition(
        key=key,
        label=label,
        field_type=field_type,
        source="manual",
        description=description,
        created_by_user_id=current_user.id,
    )
    session.add(definition)
    session.commit()
    session.refresh(definition)
    return {
        "id": definition.id,
        "key": definition.key,
        "label": definition.label,
        "type": definition.field_type,
        "source": definition.source,
        "description": definition.description,
    }


@router.delete(
    "/admin/custom-fields/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["admin"],
)
def delete_custom_field_definition(
    key: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> Response:
    _ = current_user
    definition = session.scalar(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.key == key
        )
    )
    if definition is None:
        raise HTTPException(
            status_code=404, detail=f"custom field '{key}' not found"
        )
    session.delete(definition)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------
# PR-Consolidado — Fix dispatcher sync: backfill manual
# ---------------------------------------------------------------------


@router.post(
    "/admin/workflows/replay-contact-created",
    tags=["admin"],
)
def replay_contact_created_event(
    payload: dict[str, Any],
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Backfill manual: re-dispatcha el evento `contact.created` para
    una lista de contact_ids.

    Pensado para reproducir manualmente los contactos que fueron
    creados durante el bug del PR #221 (sync periódico Agile no
    invocaba el dispatcher por el string-reference roto) sin
    esperar al próximo sync. Body:

        {"contact_ids": ["uuid1", "uuid2", ...]}

    Por cada UUID hace `dispatch_event(session, "contact.created",
    id, {"source": "manual_replay", ...})`. Idempotente sobre
    `workflow_runs`: el motor ya tiene su propio dedup_key.

    Admin-only. No silencia errores per-contact: si uno falla, los
    siguientes siguen, pero el response incluye un `failures` list
    con los IDs que petaron.
    """
    raw_ids = payload.get("contact_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(
            status_code=400,
            detail="`contact_ids` debe ser una lista no vacía de UUIDs",
        )
    ids: list[str] = [str(x) for x in raw_ids]

    from app.workflows.dispatcher import dispatch_event  # noqa: PLC0415

    dispatched: list[str] = []
    failures: list[dict[str, str]] = []
    for contact_id in ids:
        contact = session.get(Contact, contact_id)
        if contact is None:
            failures.append({"contact_id": contact_id, "error": "not_found"})
            continue
        try:
            dispatch_event(
                session,
                "contact.created",
                contact_id,
                {
                    "source": "manual_replay",
                    "actor_id": current_user.id,
                },
            )
            dispatched.append(contact_id)
        except Exception as exc:  # noqa: BLE001 — return per-contact status, never abort
            failures.append({"contact_id": contact_id, "error": str(exc)})
    session.commit()
    return {
        "dispatched": dispatched,
        "failures": failures,
        "total": len(ids),
    }


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
    # PR-Ca hotfix: bajado de require_manager a require_user — la
    # creación de contactos no es operación admin: cualquier comercial
    # que captura un lead debería poder dar de alta el contacto en CRM
    # (la decisión §1 del spec extendió este criterio a todo el flujo
    # de asignación). Manager+ ya tiene acceso vía la jerarquía.
    current_user: User = Depends(require_user),
) -> Contact:
    email = str(payload.email).lower()
    if crm_repository.get_contact_by_email(session, email):
        raise conflict("A contact with this email already exists")
    if payload.company_id and not crm_repository.get_company(session, payload.company_id):
        raise not_found("Company")

    data = payload.model_dump()
    data["email"] = email
    # PR-Fix-Creación-Manual-Contacto. El "Origen" de un contacto
    # creado a mano se fija server-side a "Manual" y `origin_account_id`
    # a NULL. El front ya no expone input libre, pero también lo
    # forzamos aquí para que payloads legacy / clientes externos no
    # inyecten valores arbitrarios ("Tel", "Web", etc.) que ensuciaban
    # filtros y reportes. Las rutas de sync (Agile/Brevo) escriben
    # directo al ORM, no a través de este endpoint, así que no las
    # tocamos.
    data["origin"] = "Manual"
    data["origin_account_id"] = None
    contact = Contact(**data)
    session.add(contact)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict("A contact with this email already exists") from exc
    # Bridge the legacy CSV column to the new M:N table so a caller
    # using the pre-Sprint-P.1 API contract still ends up with proper
    # tag rows. The CSV stays writable for backwards compat.
    _mirror_csv_tags_into_table(
        session,
        contact=contact,
        csv=contact.tags,
        actor=current_user,
        source="manual",
    )
    record_event(
        session,
        action=Action.CONTACT_CREATED,
        target_type="contact",
        target_id=contact.id,
        actor=current_user,
        metadata={"email": contact.email},
        request=request,
    )
    # PR-Fix-Creación-Manual-Contacto. Auto-asignamos al creador como
    # owner primary ANTES de que el motor de reglas evalúe. Reglas con
    # `apply_to=unassigned_only` (default histórico) ya NO reasignan
    # porque ven `has_assignments=True` — el creador queda como owner.
    # Solo reglas con `override_existing=True` siguen pudiendo tomar
    # la ownership (force-reassign explícito).
    from app.repositories import assignments as assignments_repo  # noqa: PLC0415

    assignments_repo.add_assignment(
        session,
        contact_id=contact.id,
        user_id=current_user.id,
        is_primary=True,
        assigned_by_user_id=current_user.id,
        source="manual_creator",
    )
    # Sprint Reglas-Assign PR-C — fire-on-create del motor de reglas.
    # Antes del commit: las assignments + audit rows entran en la misma
    # transacción que el CONTACT_CREATED. El motor no commitea él mismo.
    assignment_rules_engine.evaluate_for_contact(
        session, contact, trigger="manual"
    )
    session.commit()
    session.refresh(contact)

    # Sprint Workflows Bloque 1 — hook explícito post-commit. Encolamos
    # a RQ para no bloquear el response; en tests / Redis caído cae a
    # processing inline. Decisión arquitectónica: hook explícito y NO
    # listener SQLAlchemy para que bulk imports no disparen workflows.
    from app.workflows.dispatcher import dispatch_event  # noqa: PLC0415

    dispatch_event(
        session,
        "contact.created",
        contact.id,
        {"source": "manual", "actor_id": current_user.id},
    )
    return contact


def _mirror_csv_tags_into_table(
    session: Session,
    *,
    contact: Contact,
    csv: str | None,
    actor: User,
    source: str,
) -> None:
    if not csv:
        return
    seen: set[str] = set()
    for raw in csv.split(","):
        cleaned = raw.strip()
        if not cleaned:
            continue
        normalized = cleaned.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        tag, _ = crm_repository.upsert_tag(
            session, name=cleaned, created_by_user_id=actor.id
        )
        crm_repository.assign_tag_to_contact(
            session,
            contact_id=contact.id,
            tag_id=tag.id,
            assigned_by_user_id=actor.id,
            source=source,
        )


@router.get(
    "/contacts",
    response_model=ContactListPage,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_contacts(
    request: Request,
    q: str | None = Query(
        default=None, description="Busca por nombre, apellidos, email o teléfono"
    ),
    tag: str | None = Query(
        default=None,
        description="Legacy: filtra por nombre exacto de un tag (case-insensitive)",
    ),
    tag_ids: list[str] | None = Query(
        default=None,
        description="UUIDs de tags. Combinado con `tag_match_mode`",
    ),
    tag_match_mode: str = Query(
        default="any",
        pattern="^(any|all)$",
        description="`any`: al menos uno; `all`: todos",
    ),
    origin_system: ExternalSystem | None = Query(
        default=None,
        description=(
            "DEPRECATED — use `origin_account_keys` instead. "
            "Filtra por sistema de origen vía external_references.system. "
            "Mantenido por compatibilidad con URLs guardadas y vistas "
            "anteriores; nuevo código debe enviar pares concretos."
        ),
    ),
    origin_account_id: str | None = Query(
        default=None,
        description=(
            "DEPRECATED — use `origin_account_keys` instead. "
            "Filtra por cuenta de integración vía external_references.account_id."
        ),
    ),
    origin_account_keys: list[str] | None = Query(
        default=None,
        description=(
            "Lista de claves `system:account_id` (ej: "
            "`agilecrm:agile-mbomedia,brevo:brevo-mbomedia`). Si se pasa, "
            "tiene prioridad sobre `origin_system` y `origin_account_id`."
        ),
    ),
    commercial_status: str | None = Query(default=None, max_length=80),
    marketing_consent: str | None = Query(default=None, max_length=40),
    lead_score_min: int | None = Query(default=None),
    lead_score_max: int | None = Query(default=None),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    sort_by: str = Query(
        default="created_at",
        description="created_at | updated_at | name | email",
    ),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    include_inactive: bool = Query(default=False),
    view_id: str | None = Query(
        default=None,
        description=(
            "Saved view UUID; its filters are applied as defaults that "
            "individual URL params override key by key."
        ),
    ),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> ContactListPage:
    _ = current_user
    # Merge the view's saved filters on top of URL params, but only for
    # params the operator didn't include in the URL — we look at the
    # raw query string to tell "I passed include_inactive=false" from
    # "I didn't pass include_inactive at all". A view with broad
    # defaults can still be narrowed with a single URL param.
    view_filters: dict[str, Any] = {}
    if view_id:
        view = contact_views_repository.get_view(session, view_id)
        if view and (
            view.owner_user_id == current_user.id or view.is_shared
        ):
            view_filters, _columns, view_sort = contact_views_repository.view_to_dicts(view)
            if "sort_by" not in request.query_params and isinstance(view_sort, dict):
                sort_by = view_sort.get("sort_by") or sort_by
                sort_dir = view_sort.get("sort_dir") or sort_dir

    def _from_view(key: str, current: Any) -> Any:
        if key in request.query_params:
            return current
        return view_filters.get(key, current)

    q = _from_view("q", q)
    tag = _from_view("tag", tag)
    if "tag_ids" not in request.query_params:
        tag_ids = view_filters.get("tag_ids") or tag_ids
    tag_match_mode = _from_view("tag_match_mode", tag_match_mode)
    if "origin_system" not in request.query_params:
        raw_origin = view_filters.get("origin_system")
        if raw_origin and origin_system is None:
            try:
                origin_system = ExternalSystem(raw_origin)
            except ValueError:
                origin_system = None
    origin_account_id = _from_view("origin_account_id", origin_account_id)
    if "origin_account_keys" not in request.query_params and not origin_account_keys:
        # `filters_json` may carry the new shape (a list) or the
        # legacy `origin_system + origin_account_id` pair. Normalise
        # them into one place so the repository only has to read a
        # single param.
        stored_keys = view_filters.get("origin_account_keys")
        if isinstance(stored_keys, list) and stored_keys:
            origin_account_keys = [str(k) for k in stored_keys if k]
    commercial_status = _from_view("commercial_status", commercial_status)
    marketing_consent = _from_view("marketing_consent", marketing_consent)
    lead_score_min = _from_view("lead_score_min", lead_score_min)
    lead_score_max = _from_view("lead_score_max", lead_score_max)
    if "include_inactive" not in request.query_params:
        is_active_pref = view_filters.get("is_active")
        if is_active_pref is False:
            include_inactive = True

    items = crm_repository.list_contacts(
        session=session,
        q=q,
        tag=tag,
        tag_ids=tag_ids,
        tag_match_mode=tag_match_mode,
        origin_system=origin_system,
        origin_account_id=origin_account_id,
        origin_account_keys=origin_account_keys,
        commercial_status=commercial_status,
        marketing_consent=marketing_consent,
        lead_score_min=lead_score_min,
        lead_score_max=lead_score_max,
        created_after=created_after,
        created_before=created_before,
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
        tag_ids=tag_ids,
        tag_match_mode=tag_match_mode,
        origin_system=origin_system,
        origin_account_id=origin_account_id,
        origin_account_keys=origin_account_keys,
        commercial_status=commercial_status,
        marketing_consent=marketing_consent,
        lead_score_min=lead_score_min,
        lead_score_max=lead_score_max,
        created_after=created_after,
        created_before=created_before,
        include_inactive=include_inactive,
    )
    return ContactListPage(
        items=[ContactRead.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=skip,
    )


@router.post(
    "/contacts/search",
    response_model=ContactListPage,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def search_contacts_endpoint(
    payload: ContactSearchRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> ContactListPage:
    """Brevo-style contacts search: a `rules_json` boolean tree drives
    the WHERE, reusing the same engine that powers `/api/segments`.

    The body is optional — an empty `rules_json` returns every active
    contact, mirroring `GET /api/contacts` without filters. Callers
    that want to layer a saved view over a free-text search pass `q`
    alongside the tree.
    """
    _ = current_user
    from app.services.segments.engine import (  # noqa: PLC0415
        SegmentRuleError,
        build_filter,
    )
    from app.models.crm import Segment as _Segment  # noqa: PLC0415

    def _segment_resolver(segment_id: str, _visited: set[str]) -> dict[str, Any] | None:
        seg = session.get(_Segment, segment_id)
        if seg is None or not seg.rules_json:
            return None
        try:
            return json.loads(seg.rules_json)
        except (TypeError, ValueError):
            return None

    try:
        filter_clause = build_filter(
            payload.rules_json or {},
            segment_resolver=_segment_resolver,
        )
    except SegmentRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if payload.assigned_to_me:
        from sqlalchemy import and_ as _and_  # noqa: PLC0415

        owner_clause = Contact.owner_user_id == current_user.id
        filter_clause = (
            owner_clause if filter_clause is None else _and_(filter_clause, owner_clause)
        )

    items, total = crm_repository.search_contacts(
        session,
        filter_clause=filter_clause,
        q=payload.q,
        skip=payload.offset,
        limit=payload.limit,
        include_inactive=payload.include_inactive,
        sort_by=payload.sort_by,
        sort_dir=payload.sort_dir,
    )
    return ContactListPage(
        items=[ContactRead.model_validate(c) for c in items],
        total=total,
        limit=payload.limit,
        offset=payload.offset,
    )


@router.post(
    "/contacts/search/ids",
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def search_contact_ids_endpoint(
    payload: ContactSearchRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> dict[str, Any]:
    """Same WHERE as `/contacts/search` but returns only the matching
    UUIDs (no payload). Drives the "Seleccionar los X contactos del
    filtro" link in the bulk-action banner — clients use this to expand
    the visible-page selection into the whole filter cohort without
    pulling every Contact body.

    Hard-capped at 10 000 ids per call. When the filter would return
    more, the response includes `truncated: true` and the operator
    sees a warning in the UI before any bulk action runs.
    """
    from app.services.segments.engine import (  # noqa: PLC0415
        SegmentRuleError,
        build_filter,
    )
    from app.models.crm import Segment as _Segment  # noqa: PLC0415

    def _segment_resolver(segment_id: str, _visited: set[str]) -> dict[str, Any] | None:
        seg = session.get(_Segment, segment_id)
        if seg is None or not seg.rules_json:
            return None
        try:
            return json.loads(seg.rules_json)
        except (TypeError, ValueError):
            return None

    try:
        filter_clause = build_filter(
            payload.rules_json or {},
            segment_resolver=_segment_resolver,
        )
    except SegmentRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if payload.assigned_to_me:
        from sqlalchemy import and_ as _and_  # noqa: PLC0415

        owner_clause = Contact.owner_user_id == current_user.id
        filter_clause = (
            owner_clause if filter_clause is None else _and_(filter_clause, owner_clause)
        )

    MAX_IDS = 10_000  # noqa: N806
    stmt = select(Contact.id)
    if filter_clause is not None:
        stmt = stmt.where(filter_clause)
    if not payload.include_inactive:
        stmt = stmt.where(Contact.is_active.is_(True))
    stmt = stmt.limit(MAX_IDS + 1)
    raw_ids = list(session.scalars(stmt))
    truncated = len(raw_ids) > MAX_IDS
    ids = raw_ids[:MAX_IDS]
    return {
        "ids": ids,
        "count": len(ids),
        "truncated": truncated,
        "max_ids": MAX_IDS,
    }


@router.get(
    "/contacts/count",
    response_model=CountRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def count_contacts(
    q: str | None = Query(default=None, description="Busca por nombre, apellidos o email"),
    tag: str | None = Query(default=None),
    tag_ids: list[str] | None = Query(default=None),
    tag_match_mode: str = Query(default="any", pattern="^(any|all)$"),
    origin_system: ExternalSystem | None = Query(default=None),
    origin_account_id: str | None = Query(default=None),
    origin_account_keys: list[str] | None = Query(default=None),
    commercial_status: str | None = Query(default=None, max_length=80),
    marketing_consent: str | None = Query(default=None, max_length=40),
    lead_score_min: int | None = Query(default=None),
    lead_score_max: int | None = Query(default=None),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
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
        tag_ids=tag_ids,
        tag_match_mode=tag_match_mode,
        origin_system=origin_system,
        origin_account_id=origin_account_id,
        origin_account_keys=origin_account_keys,
        commercial_status=commercial_status,
        marketing_consent=marketing_consent,
        lead_score_min=lead_score_min,
        lead_score_max=lead_score_max,
        created_after=created_after,
        created_before=created_before,
        include_inactive=include_inactive,
    )
    return CountRead(total=total)


_INTEGRATIONS_LABELS = {
    "agilecrm": "AgileCRM",
    "brevo": "Brevo",
    "freshdesk": "Freshdesk",
    "factusol": "FactuSOL",
}


@router.get(
    "/integrations/accounts",
    response_model=list[IntegrationSystemGroup],
    responses=ERROR_RESPONSES,
    tags=["integration accounts"],
)
def list_integration_account_groups(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[IntegrationSystemGroup]:
    """Group every configured account by system, including a count of
    contacts that carry an `external_references` row for it.

    Drives the new "Origen" picker on `/contacts` (and `/segments`).
    Returned even for disabled accounts so an operator can build a
    segment over a paused integration and re-enable it later without
    the rules silently dropping matches.
    """
    _ = current_user

    accounts = list(
        session.scalars(
            select(IntegrationAccount).order_by(
                IntegrationAccount.system,
                IntegrationAccount.display_name,
                IntegrationAccount.account_id,
            )
        )
    )

    counts_rows = session.execute(
        select(
            ExternalReference.system,
            ExternalReference.account_id,
            func.count(func.distinct(ExternalReference.contact_id)),
        ).group_by(ExternalReference.system, ExternalReference.account_id)
    ).all()
    counts: dict[tuple[str, str], int] = {}
    for system_value, account_id, total in counts_rows:
        key_system = (
            system_value.value
            if hasattr(system_value, "value")
            else str(system_value)
        )
        counts[(key_system, account_id)] = int(total or 0)

    groups: dict[str, IntegrationSystemGroup] = {}
    for account in accounts:
        system_slug = (
            account.system.value
            if hasattr(account.system, "value")
            else str(account.system)
        )
        group = groups.setdefault(
            system_slug,
            IntegrationSystemGroup(
                system=system_slug,
                system_label=_INTEGRATIONS_LABELS.get(system_slug, system_slug),
                accounts=[],
            ),
        )
        group.accounts.append(
            IntegrationAccountSummary(
                account_id=account.account_id,
                label=account.display_name or account.account_id,
                contacts_count=counts.get((system_slug, account.account_id), 0),
                enabled=account.enabled,
            )
        )
    # Stable system order: known systems first, unknowns at the end.
    order = ["agilecrm", "brevo", "freshdesk", "factusol"]
    return sorted(
        groups.values(),
        key=lambda group: (
            order.index(group.system) if group.system in order else len(order),
            group.system,
        ),
    )


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
    detail.last_external_refresh_at = contact.external_data_refreshed_at
    detail.external_data_freshness = _freshness_label(
        contact.external_data_refreshed_at
    )
    _enrich_external_refs(session, detail.external_refs)
    return detail


def _enrich_external_refs(
    session: Session, refs: list[ExternalReferenceRead]
) -> None:
    """Fill each reference's `external_url` (deep link into the source
    system) and friendly `account_label` (the IntegrationAccount's
    display name) — both need the account row the schema layer can't
    reach. One batched query for every (system, account) the contact
    touches."""
    if not refs:
        return
    from app.integrations.external_links import build_external_url  # noqa: PLC0415
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    # A deployment has a handful of integration accounts; loading them
    # all once is cheaper than a composite-key IN (which isn't portable
    # across SQLite + MySQL) and the lookup stays in memory.
    accounts = {
        (acc.system.value, acc.account_id): acc
        for acc in session.scalars(select(IntegrationAccount))
    }
    for ref in refs:
        ref_system = ref.system.value if hasattr(ref.system, "value") else str(ref.system)
        account = accounts.get((ref_system, ref.account_id))
        if account is not None and account.display_name:
            # Prefer the operator-facing account name over the
            # mapper-set per-payload label (e.g. AgileCRM lead_status).
            ref.account_label = account.display_name
        ref.external_url = build_external_url(
            ref.system,
            ref.external_id,
            api_base_url=account.api_base_url if account else None,
        )


def _freshness_label(refreshed_at: datetime | None) -> str:
    """Bucket the last-refresh timestamp into one of three tiers the UI
    renders different banners for. Reused by the refresh endpoint so
    the freshness shown after a click matches the GET response.

    SQLite (the test backend) drops the tzinfo on DateTime(timezone=True)
    columns; we coerce naive datetimes to UTC so the subtraction below
    doesn't TypeError on the test DB."""
    if refreshed_at is None:
        return "outdated"
    if refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=UTC)
    age = datetime.now(UTC) - refreshed_at
    if age < timedelta(hours=1):
        return "fresh"
    if age < timedelta(hours=24):
        return "stale"
    return "outdated"


@router.post(
    "/contacts/{contact_id}/refresh-external-data",
    response_model=ExternalRefreshRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
async def refresh_contact_external_data(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ExternalRefreshRead:
    """On-demand pull of notes / tasks / events from the contact's
    external systems. The bulk `sync_contacts` job no longer carries
    them — that change cut the per-sync API call count from 4N down to
    ~N/page so the AgileCRM Free quota survives a full re-sync.

    `viewer` cannot trigger this endpoint (only sees cached data); the
    audit row links the actor to the burst of API calls so a saturated
    account can be traced back to a specific operator."""
    from app.integrations.agilecrm.refresh import refresh_contact_external_data as _do

    contact = crm_repository.get_contact(session, contact_id)
    if not contact:
        raise not_found("Contact")
    result = await _do(session, contact=contact, actor=current_user)
    return ExternalRefreshRead(
        refreshed_at=result.refreshed_at,
        sources_refreshed=result.sources_refreshed,
        notes_count=result.notes_count,
        tasks_count=result.tasks_count,
        events_count=result.events_count,
        warnings=result.warnings,
        status=result.status,
    )


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


@router.get(
    "/contacts/{contact_id}/engagement-stats",
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def get_contact_engagement_stats(
    contact_id: str,
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> dict[str, int]:
    """PR-Fix-Widget-Engagement-Email. Aperturas / clics / respuestas
    de los emails enviados al contacto en los últimos `days` días.

    Antes el widget contaba sobre `activity_events` (`EMAIL_OPENED`,
    `EMAIL_CLICKED`, `email.reply_received`) pero el tracking real de
    aperturas escribe a `email_message_events` (event_type='open' /
    'click'), no a `activity_events`. El widget mostraba 0 aunque la
    BD tuviera la apertura registrada.

    Fix: leer directo de `email_message_events` igual que el
    aggregator de la lista global `/emails` (ver
    `_INBOX_EVENT_TYPES` en `api/emails.py:1113`). Las respuestas
    cuentan como mensajes inbound (`email_messages.direction='inbound'`)
    en el mismo contacto y ventana.

    Idéntico para todos los users del CRM (no filtra por
    `current_user.id`) — Bart validó con admin + otros users del
    equipo y todos deben ver los mismos números.
    """
    _ = current_user
    if not crm_repository.get_contact(session, contact_id):
        raise not_found("Contact")

    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from app.models.crm import (  # noqa: PLC0415
        EmailDirection,
        EmailEventType,
        EmailMessage,
        EmailMessageEvent,
    )

    cutoff = datetime.now(UTC) - timedelta(days=days)

    def _count_event(event_type: EmailEventType) -> int:
        stmt = (
            select(func.count(EmailMessageEvent.id))
            .join(
                EmailMessage,
                EmailMessage.id == EmailMessageEvent.message_id,
            )
            .where(EmailMessage.contact_id == contact_id)
            .where(EmailMessageEvent.event_type == event_type)
            .where(EmailMessageEvent.occurred_at >= cutoff)
        )
        return session.scalar(stmt) or 0

    opens = _count_event(EmailEventType.OPEN)
    clicks = _count_event(EmailEventType.CLICK)
    # Replies = mensajes inbound del contacto. Cuenta `sent_at` (no
    # `created_at`) para alinear con la ventana visible al usuario;
    # los mensajes scheduled (sin sent_at) no son respuestas, así
    # que el filtro is_not(None) los descarta naturalmente.
    replies = session.scalar(
        select(func.count(EmailMessage.id))
        .where(EmailMessage.contact_id == contact_id)
        .where(EmailMessage.direction == EmailDirection.INBOUND)
        .where(EmailMessage.sent_at.is_not(None))
        .where(EmailMessage.sent_at >= cutoff)
    ) or 0

    return {"opens": opens, "clicks": clicks, "replies": replies}


@router.get(
    "/contacts/{contact_id}/brevo-engagement",
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def get_contact_brevo_engagement(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> dict[str, Any]:
    """Resumen del engagement del contacto con campañas Brevo.

    Agrega `activity_events` filtrados por `contact_id` y
    `campaign_brevo_id IS NOT NULL` agrupados por campaña:

    - `campaigns_total`: nº de campañas en las que figura como
      destinatario (cualquier evento, opened o no).
    - `opens`: distinct campaigns con event_type que contiene "open".
    - `clicks`: distinct campaigns con event_type que contiene
      "click".
    - `opens_pct` / `clicks_pct`: porcentajes vs `campaigns_total`.
    - `recent`: top 3 campañas más recientes con
      `{brevo_campaign_id, name, sent_at, status}` donde status =
      "clicked" / "opened" / "no_open" según el evento más
      "engaged" disponible.

    Sin endpoint dedicado en Brevo para esta agregación — calculamos
    en la BD local porque ya tenemos los events sincronizados por el
    webhook + backfill (PR #176).
    """
    from sqlalchemy import func  # noqa: PLC0415

    from app.models.brevo import BrevoCampaignCache  # noqa: PLC0415
    from app.models.crm import ActivityEvent as _ActivityEvent  # noqa: PLC0415

    _ = current_user
    if not crm_repository.get_contact(session, contact_id):
        raise not_found("Contact")

    base = (
        select(_ActivityEvent.campaign_brevo_id, _ActivityEvent.event_type)
        .where(_ActivityEvent.contact_id == contact_id)
        .where(_ActivityEvent.campaign_brevo_id.isnot(None))
    )
    rows = list(session.execute(base))

    seen_campaigns: set[int] = set()
    opened_campaigns: set[int] = set()
    clicked_campaigns: set[int] = set()
    for campaign_id, event_type in rows:
        seen_campaigns.add(campaign_id)
        if event_type and "click" in event_type.lower():
            clicked_campaigns.add(campaign_id)
            opened_campaigns.add(campaign_id)  # click implica open
        elif event_type and "open" in event_type.lower():
            opened_campaigns.add(campaign_id)

    campaigns_total = len(seen_campaigns)
    opens = len(opened_campaigns)
    clicks = len(clicked_campaigns)

    def pct(n: int) -> float:
        return round((n / campaigns_total) * 100, 1) if campaigns_total else 0.0

    recent_rows: list[dict[str, Any]] = []
    if seen_campaigns:
        # Última fecha de evento por campaña + status (clicked > opened > no_open).
        last_event = (
            select(
                _ActivityEvent.campaign_brevo_id,
                func.max(_ActivityEvent.occurred_at).label("last_at"),
            )
            .where(_ActivityEvent.contact_id == contact_id)
            .where(_ActivityEvent.campaign_brevo_id.in_(seen_campaigns))
            .group_by(_ActivityEvent.campaign_brevo_id)
            .order_by(func.max(_ActivityEvent.occurred_at).desc())
            .limit(3)
        )
        recent_pairs = list(session.execute(last_event))
        # Trae los nombres desde el cache local.
        cache_rows = list(
            session.execute(
                select(
                    BrevoCampaignCache.brevo_campaign_id,
                    BrevoCampaignCache.name,
                    BrevoCampaignCache.sent_at,
                ).where(
                    BrevoCampaignCache.brevo_campaign_id.in_(
                        [c for c, _ in recent_pairs]
                    )
                )
            )
        )
        cache_by_id: dict[int, dict[str, Any]] = {
            cid: {"name": name, "sent_at": sent_at}
            for cid, name, sent_at in cache_rows
        }
        for campaign_id, _last_at in recent_pairs:
            cached = cache_by_id.get(campaign_id, {})
            status_str = (
                "clicked"
                if campaign_id in clicked_campaigns
                else "opened"
                if campaign_id in opened_campaigns
                else "no_open"
            )
            recent_rows.append(
                {
                    "brevo_campaign_id": campaign_id,
                    "name": cached.get("name"),
                    "sent_at": cached.get("sent_at"),
                    "status": status_str,
                }
            )

    return {
        "campaigns_total": campaigns_total,
        "opens": opens,
        "opens_pct": pct(opens),
        "clicks": clicks,
        "clicks_pct": pct(clicks),
        "recent": recent_rows,
    }


# PR-Ficha-Fix. `is_active` se desactiva vía el endpoint dedicado
# `/contacts/{id}/deactivate` (require_manager). Bloqueamos su llegada
# vía el PATCH genérico para que un `user` no pueda colarlo. Mismo
# spirit para `company_id`: re-empresar un contacto altera reporting
# y asignaciones — manager+ exclusivo.
_MANAGER_ONLY_PATCH_FIELDS = frozenset({"is_active", "company_id"})


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
    # PR-Ficha-Fix. Bart: cualquier user normal debe poder editar los
    # campos del strip (nombre, puesto, email, teléfono, score, estado
    # ciclo, etiquetas). Antes el handler exigía manager+ y los inline
    # edits fallaban con 403 "Not enough permissions". Bajamos a
    # require_user y rechazamos a mano los campos manager-only.
    current_user: User = Depends(require_user),
) -> Contact:
    from app.models.crm import (  # noqa: PLC0415
        ContactPhone,
        ContactTag,
        EmailUnsubscribe,
        Tag,
    )
    from app.repositories import assignments as _assignments  # noqa: PLC0415

    contact = crm_repository.get_contact(session, contact_id)
    if not contact:
        raise not_found("Contact")
    data = payload.model_dump(exclude_unset=True)

    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        blocked = _MANAGER_ONLY_PATCH_FIELDS & data.keys()
        if blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Estos campos solo los puede modificar un manager+: "
                    + ", ".join(sorted(blocked))
                ),
            )

    # PR-Editar-Completo. `unsubscribe_action='resubscribe'` solo
    # admin. Lo evaluamos antes que el resto para devolver 403
    # rápido en vez de aplicar cambios parciales.
    unsubscribe_action = data.pop("unsubscribe_action", None)
    if unsubscribe_action == "resubscribe" and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Solo un admin puede reactivar envíos comerciales "
                "(borrar la baja del contacto)."
            ),
        )

    # PR-Editar-Completo. Phones reemplaza la lista completa de
    # contact_phones. Los procesamos al final (tras flush de scalars)
    # para que el contact existe y el cascade no rompa.
    phones_payload = data.pop("phones", None)
    # owner_id: pasa a primary en contact_assignments. None → quita
    # el primary actual (todos los assignments quedan como
    # secundarios o ninguno si no había nada).
    owner_payload_set = "owner_id" in data
    owner_value = data.pop("owner_id", None)

    if "email" in data and data["email"] is not None:
        email = str(data["email"]).lower()
        existing = crm_repository.get_contact_by_email(session, email)
        if existing and existing.id != contact.id:
            raise conflict("A contact with this email already exists")
        data["email"] = email
    if data.get("company_id") and not crm_repository.get_company(session, data["company_id"]):
        raise not_found("Company")
    # `custom_fields` viene como dict en el modal "Editar"; lo
    # serializamos a JSON antes de pegárselo a la columna Text. Si el
    # caller manda un string ya hecho (legacy), lo respetamos.
    if "custom_fields" in data and isinstance(data["custom_fields"], dict):
        data["custom_fields"] = json.dumps(data["custom_fields"])
    # PR-Consolidado — Star Rating. Capturamos el valor previo ANTES
    # del setattr loop para emitir el audit `star_rating_changed` con
    # metadata {old, new} cuando el campo cambia (no cuando se manda
    # el mismo valor — eso ensuciaría el audit log).
    star_rating_old: int | None = (
        contact.star_rating if "star_rating" in data else None
    )
    star_rating_changed = (
        "star_rating" in data and data["star_rating"] != contact.star_rating
    )
    # PR-Fix-Sync-No-Sobreescribe-Cambios-CRM +
    # PR-Fix-Patch-No-Marca-Manual-Edits.
    #
    # Capturamos qué campos cambian REALMENTE (no marcamos si el
    # valor coincide con el actual: ensuciaría la protección y
    # bloquearía syncs futuros sin razón). Marca tanto Capa A como
    # Capa B — para Capa B es solo trazabilidad / UI (el sync nunca
    # toca Capa B aunque no esté en el array).
    #
    # Mappings especiales:
    #  - `phones` (lista) → `phone` (string) cuando el primary cambia.
    #    El bloque de phones más abajo actualiza `contact.phone` desde
    #    `phones_payload`; aquí solo marcamos la intención.
    #  - `owner_id` → `owner_user_id`. El bloque de owner más abajo
    #    cambia `contact.owner_user_id`; aquí marcamos la intención.
    from app.services import contact_sync_protection as _sync_protection  # noqa: PLC0415

    fields_to_mark: list[str] = []
    for field, value in data.items():
        if field in _sync_protection.MARKABLE_FIELDS:
            current = getattr(contact, field, None)
            if current != value:
                fields_to_mark.append(field)
        setattr(contact, field, value)
    # phones array → marca el campo escalar `phone` si la firma
    # cambió (el bloque inferior lo recalcula a partir del primary).
    if phones_payload is not None:
        try:
            import json as _json  # noqa: PLC0415

            new_sig = _json.dumps(
                sorted(
                    [
                        (
                            p.get("number") if isinstance(p, dict)
                            else getattr(p, "number", "")
                        )
                        for p in phones_payload
                    ]
                )
            )
        except Exception:  # noqa: BLE001
            new_sig = ""
        if new_sig and "phone" not in fields_to_mark:
            fields_to_mark.append("phone")
    # owner_id (alias del cliente) → owner_user_id (columna real).
    if owner_payload_set and owner_value != contact.owner_user_id:
        if "owner_user_id" not in fields_to_mark:
            fields_to_mark.append("owner_user_id")
    _sync_protection.mark_manually_edited(contact, fields_to_mark)
    session.flush()

    changed_fields: list[str] = sorted(data.keys())

    if star_rating_changed:
        record_event(
            session,
            action=Action.CONTACT_STAR_RATING_CHANGED,
            target_type="contact",
            target_id=contact.id,
            actor=current_user,
            metadata={
                "email": contact.email,
                "old": star_rating_old,
                "new": data["star_rating"],
            },
            request=request,
        )

    # Phones: replace strategy. Más simple y atómico que diff —
    # ContactPhone.source se preserva como "manual" para los que
    # vienen del modal. Los que tenían provenance Brevo/Agile y NO
    # estaban en el payload se pierden, que es lo correcto: si el
    # operador los quitó del modal, conscientemente los borra.
    if phones_payload is not None:
        # Soporta tanto list[Pydantic] como list[dict] (model_dump).
        # Cuando el payload llega de la red Pydantic ya lo convirtió
        # a dicts; los tests pueden pasar el modelo crudo.
        normalised: list[dict[str, Any]] = []
        for entry in phones_payload:
            if isinstance(entry, dict):
                normalised.append(entry)
            else:
                normalised.append(entry.model_dump())
        # Borra las rows existentes.
        session.execute(
            ContactPhone.__table__.delete().where(  # type: ignore[attr-defined]
                ContactPhone.contact_id == contact.id
            )
        )
        session.flush()
        # Garantiza UNA primary (la marcada como tal, o la primera si
        # ninguna). Esto cubre el caso UX: el operador quitó el
        # primary y olvidó marcar otro; mejor que dejar al contacto
        # sin primary.
        any_primary = any(p.get("is_primary") for p in normalised)
        for idx, phone in enumerate(normalised):
            is_primary = bool(phone.get("is_primary"))
            if not any_primary and idx == 0:
                is_primary = True
                any_primary = True
            session.add(
                ContactPhone(
                    contact_id=contact.id,
                    label=(phone.get("label") or None),
                    number=str(phone["number"]).strip(),
                    is_primary=is_primary,
                    source="manual",
                )
            )
        # `contact.phone` legacy (single) refleja el primary number
        # para que el resto del CRM siga funcionando. Si no hay
        # phones, lo limpiamos.
        if normalised:
            primary_entry = next(
                (p for p in normalised if p.get("is_primary")),
                normalised[0],
            )
            contact.phone = str(primary_entry["number"]).strip()
        else:
            contact.phone = None
        changed_fields.append("phones")

    # owner_id → swap del primary en contact_assignments.
    if owner_payload_set:
        if owner_value:
            # Asegúrate de que el user existe + activo.
            target_user = session.get(User, owner_value)
            if target_user is None:
                raise not_found("User")
            if not target_user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="El usuario destino no está activo.",
                )
            _assignments.add_assignment(
                session,
                contact_id=contact.id,
                user_id=owner_value,
                is_primary=True,
                assigned_by_user_id=current_user.id,
                source="manual",
            )
        else:
            # Limpia el primary sin asignar nuevo — todos los
            # assignments quedan como secundarios o sin primary.
            _assignments._demote_other_primaries(session, contact.id)  # noqa: SLF001
            _assignments.recompute_primary_cache(session, contact.id)
        changed_fields.append("owner_id")

    # unsubscribe_action='resubscribe' → borra rows + tag.
    if unsubscribe_action == "resubscribe":
        unsub_rows = list(
            session.scalars(
                select(EmailUnsubscribe).where(
                    EmailUnsubscribe.contact_id == contact.id
                )
            )
        )
        deleted_scopes = sorted({r.scope for r in unsub_rows})
        for row in unsub_rows:
            session.delete(row)
        tag = session.scalar(select(Tag).where(Tag.name == "unsubscribed"))
        if tag is not None:
            session.execute(
                ContactTag.__table__.delete().where(  # type: ignore[attr-defined]
                    ContactTag.contact_id == contact.id,
                    ContactTag.tag_id == tag.id,
                )
            )
        record_event(
            session,
            action=Action.CONTACT_RESUBSCRIBED,
            target_type="contact",
            target_id=contact.id,
            actor=current_user,
            metadata={
                "email": contact.email,
                "scopes_cleared": deleted_scopes,
                "via": "edit_modal",
            },
            request=request,
        )
        changed_fields.append("unsubscribe_action")

    # PR-Ficha-Fix. El modal "Editar completo" puede tocar 10+ campos
    # a la vez; el evento `contact.bulk_updated` los distingue del
    # PATCH single-field del inline edit. El audit del inline edit
    # sigue siendo `contact.updated` para no romper dashboards
    # existentes.
    bulk_threshold = 3
    action = (
        Action.CONTACT_BULK_UPDATED
        if len(changed_fields) >= bulk_threshold
        else Action.CONTACT_UPDATED
    )
    # Si SOLO hizo resubscribe (sin otros cambios), saltamos el
    # contact.updated/bulk genérico — el resubscribed ya quedó arriba.
    if changed_fields and changed_fields != ["unsubscribe_action"]:
        record_event(
            session,
            action=action,
            target_type="contact",
            target_id=contact.id,
            actor=current_user,
            metadata={"email": contact.email, "changed_fields": changed_fields},
            request=request,
        )
    session.commit()
    session.refresh(contact)
    return contact


@router.post(
    "/contacts/{contact_id}/reset-manual-edits",
    response_model=ContactRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def reset_manual_edits(
    contact_id: str,
    payload: dict[str, Any] | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Contact:
    """PR-Fix-Sync-No-Sobreescribe-Cambios-CRM. Endpoint para
    "volver a aceptar el valor de Agile/Brevo" en uno o varios
    campos de Capa A previamente editados manualmente.

    Body opcional:
        {"fields": ["phone", "email"]}  # reset selectivo

    Sin body o `fields=[]` → vacía toda la lista (el próximo sync
    sobrescribirá cualquier campo de Capa A con la versión externa).

    Permisos: cualquier user (no expone datos; solo cambia el flag
    de protección de un contacto al que ya tiene acceso).
    """
    from app.services import contact_sync_protection as _sync_protection  # noqa: PLC0415

    contact = crm_repository.get_contact(session, contact_id)
    if not contact:
        raise not_found("Contact")

    fields: list[str] | None = None
    if isinstance(payload, dict):
        raw = payload.get("fields")
        if isinstance(raw, list):
            fields = [str(f) for f in raw]

    _sync_protection.reset_manual_edits(contact, fields)
    session.commit()
    session.refresh(contact)
    _ = current_user
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


# ---------------------------------------------------------------------
# PR-Backlog-Consolidado B1 — Hard delete del contacto.
# ---------------------------------------------------------------------


@router.delete(
    "/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def delete_contact_hard(
    contact_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Response:
    """Borra el contacto definitivamente: el row desaparece de la BD
    junto con tasks/notes/assignments (cascade) y los workflow_runs
    activos quedan cancelados. Los emails históricos quedan
    preservados con `contact_id = NULL` por auditoría de la
    conversación.

    Restringido a admin/manager. Si el contacto tiene oportunidades
    activas (stage no won/lost), devuelve 409 — el operador debe
    cerrarlas antes."""
    from app.models.crm import (  # noqa: PLC0415
        EmailMessage,
        PipelineStage,
    )
    from app.models.workflows import (  # noqa: PLC0415
        WorkflowRun,
        WorkflowRunState,
    )

    contact = session.get(Contact, contact_id)
    if contact is None:
        raise not_found("Contact")

    # 1. Bloquea si hay oportunidad activa.
    active_opps = list(
        session.scalars(
            select(ContactPipelineStage)
            .join(
                PipelineStage,
                PipelineStage.id == ContactPipelineStage.stage_id,
            )
            .where(
                ContactPipelineStage.contact_id == contact_id,
                PipelineStage.is_won.is_(False),
                PipelineStage.is_lost.is_(False),
            )
        )
    )
    if active_opps:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Este contacto tiene {len(active_opps)} oportunidad"
                f"{'es' if len(active_opps) != 1 else ''} activa"
                f"{'s' if len(active_opps) != 1 else ''}. Ciérralas "
                f"(ganada/perdida) antes de borrar."
            ),
        )

    # 2. Snapshot para el audit log.
    snapshot = {
        "email": contact.email,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "owner_user_id": contact.owner_user_id,
        "lifecycle_status": contact.commercial_status,
        "lead_score": contact.lead_score,
        "created_at": (
            contact.created_at.isoformat() if contact.created_at else None
        ),
    }

    # 3. Cancela workflow runs activos.
    active_runs = list(
        session.scalars(
            select(WorkflowRun).where(
                WorkflowRun.contact_id == contact_id,
                WorkflowRun.state.in_(
                    [
                        WorkflowRunState.RUNNING,
                        WorkflowRunState.WAITING,
                        WorkflowRunState.WAITING_FOR_EVENT,
                    ]
                ),
            )
        )
    )
    for run in active_runs:
        run.state = WorkflowRunState.CANCELLED
        run.completed_at = datetime.now(UTC)
        run.error_summary = "contact_deleted"

    # 4. Preserva email_messages con contact_id NULL.
    session.execute(
        EmailMessage.__table__.update()
        .where(EmailMessage.contact_id == contact_id)
        .values(contact_id=None)
    )

    # 5. Audit ANTES del delete — necesitamos el target_id resoluble.
    record_event(
        session,
        action=Action.CONTACT_DELETED,
        target_type="contact",
        target_id=contact_id,
        actor=current_user,
        metadata={
            "snapshot": snapshot,
            "cancelled_runs": len(active_runs),
        },
        request=request,
    )

    # 6. Delete — cascade en tasks/notes/contact_assignments/
    #    contact_phones/contact_pipeline_stages/email_unsubscribes
    #    (definido en los FKs del modelo).
    session.delete(contact)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# PR-Contact-Unsubscribe-Admin. Bart reportó 422 al enviar email:
# "Este contacto se ha dado de baja". El backend tiene el modelo
# EmailUnsubscribe + la guard en `_send_email_core`, pero no había
# UI para que el admin gestionara el estado de baja desde la ficha
# del contacto. Estos dos endpoints + un card en el sidebar
# (frontend) cierran el loop.

class UnsubscribeStatusItem(BaseModel):
    """Una row de `email_unsubscribes` proyectada para el sidebar de
    la ficha. NO incluimos `token` (lo usa la unsubscribe page
    pública y no quiero filtrarlo en una respuesta admin)."""

    id: str
    scope: str
    source: str
    unsubscribed_at: datetime
    message_id: str | None

    model_config = ConfigDict(from_attributes=True)


class UnsubscribeStatusResponse(BaseModel):
    """Devuelto por GET /api/contacts/{id}/unsubscribe-status.
    `is_unsubscribed` es un flag computed (cualquier scope marketing/
    all bloquea el send) para que el frontend pinte el badge sin
    tener que re-evaluar la lógica de scope."""

    is_unsubscribed: bool
    rows: list[UnsubscribeStatusItem]


@router.get(
    "/contacts/{contact_id}/unsubscribe-status",
    response_model=UnsubscribeStatusResponse,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def get_contact_unsubscribe_status(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> UnsubscribeStatusResponse:
    """Devuelve las rows `email_unsubscribes` del contacto + flag
    computed `is_unsubscribed`. require_user porque cualquiera que
    abre la ficha debe ver el estado (es info diagnóstica del por
    qué no puede enviar). El borrado sigue siendo admin-only."""
    _ = current_user
    from app.models.crm import EmailUnsubscribe  # noqa: PLC0415

    contact = crm_repository.get_contact(session, contact_id)
    if not contact:
        raise not_found("Contact")
    rows = list(
        session.scalars(
            select(EmailUnsubscribe)
            .where(EmailUnsubscribe.contact_id == contact_id)
            .order_by(EmailUnsubscribe.unsubscribed_at.desc())
        )
    )
    is_unsubscribed = any(
        row.scope in ("marketing", "all") for row in rows
    )
    return UnsubscribeStatusResponse(
        is_unsubscribed=is_unsubscribed,
        rows=[UnsubscribeStatusItem.model_validate(r) for r in rows],
    )


@router.delete(
    "/contacts/{contact_id}/unsubscribes",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def clear_contact_unsubscribes(
    contact_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> Response:
    """Borra TODAS las rows `email_unsubscribes` del contacto. El
    contacto vuelve a recibir envíos comerciales en el siguiente
    POST /emails/send.

    Admin-only: un user normal NO debería poder re-subscribir a un
    contacto que opt-eó out conscientemente. Si Bart quiere que un
    manager pueda hacerlo, lo discutimos — por ahora cierro al rol
    más restrictivo.

    También limpia el tag `unsubscribed` del contacto si lo tiene
    (se añade automáticamente al opt-out vía
    `_ensure_unsubscribed_tag`). Sin esto el tag queda colgando y la
    UI sigue mostrando "contacto dado de baja" aunque el bloqueo de
    send ya esté levantado.
    """
    from app.models.crm import (  # noqa: PLC0415
        ContactTag,
        EmailUnsubscribe,
        Tag,
    )

    contact = crm_repository.get_contact(session, contact_id)
    if not contact:
        raise not_found("Contact")

    rows = list(
        session.scalars(
            select(EmailUnsubscribe).where(
                EmailUnsubscribe.contact_id == contact_id
            )
        )
    )
    deleted_scopes = sorted({r.scope for r in rows})
    for row in rows:
        session.delete(row)

    # Limpia el tag "unsubscribed" (lo añadía `_ensure_unsubscribed_tag`
    # del router de tracking). Si el tag no existe nada que limpiar.
    tag = session.scalar(select(Tag).where(Tag.name == "unsubscribed"))
    if tag is not None:
        session.execute(
            ContactTag.__table__.delete().where(  # type: ignore[attr-defined]
                ContactTag.contact_id == contact_id,
                ContactTag.tag_id == tag.id,
            )
        )

    record_event(
        session,
        action=Action.CONTACT_RESUBSCRIBED,
        target_type="contact",
        target_id=contact.id,
        actor=current_user,
        metadata={"email": contact.email, "scopes_cleared": deleted_scopes},
        request=request,
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/contacts/{contact_id}/tasks",
    response_model=list[TaskRead],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_contact_tasks(
    contact_id: str,
    include_completed: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[TaskRead]:
    """Tasks attached to a contact — drives the "Tareas" tab on the
    contact detail page. By default the completed ones stay out of
    sight; pass `include_completed=true` to see the full history."""
    _ = current_user
    from app.repositories import tasks as tasks_repository  # noqa: PLC0415

    items = tasks_repository.list_tasks(
        session,
        contact_id=contact_id,
        statuses=None
        if include_completed
        else [TaskStatus.PENDING, TaskStatus.IN_PROGRESS],
        limit=200,
    )
    return [TaskRead.model_validate(t) for t in items]


# Sprint Empresas — sub-PR 4: `/api/contacts/{id}/notes` moved to
# `app/api/contact_notes.py`, backed by the new `contact_notes`
# table (cleaner schema with pinning + source provenance). The
# legacy `notes` table + sync (`_sync_contact_notes` in the
# AgileCRM jobs) still populate activity-stream notes embedded in
# the contact detail response — they just no longer get a
# standalone API endpoint, which the frontend never called.
#
# Legacy `/contacts/{contact_id}/tasks` GET + POST replaced by the
# Mini-PR C productivity layer (`app/api/tasks.py`). The GET at the
# same path lives there now; the POST belongs on `/api/tasks` so a
# task can be created without scoping to a contact.


# ---------------------------------------------------------------------------
# Tags (Sprint P.1 ampliado)
# ---------------------------------------------------------------------------


@router.get(
    "/tags",
    response_model=TagListPage,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_tags(
    q: str | None = Query(default=None, description="Búsqueda parcial sobre nombre"),
    skip: int = Query(default=0, ge=0),
    # PR-Fix-Filtros-Lista-Cortada. El cap previo era `le=200` con
    # default 50 — pickers que hidrataban todos los tags via
    # `listTags(undefined)` (TagPicker / WorkflowTagsPicker / etc.)
    # solo pedían 200 y el dropdown perdía la cola alfabética. Bart
    # reportó que `webformde` (W) no aparecía. Para tags el dominio
    # realista es < pocos miles por tenant, así que subimos el cap
    # a 5000 — todos los pickers funcionan con UNA request sin
    # paginar. Si algún tenant excede esa cifra, el frontend siempre
    # puede recurrir a `?q=substring` (ya implementado).
    limit: int = Query(default=5000, ge=1, le=5000),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> TagListPage:
    _ = current_user
    tags, counts, total = crm_repository.list_tags(
        session=session, q=q, skip=skip, limit=limit
    )
    items = [
        TagDetailRead(
            id=tag.id,
            name=tag.name,
            color=tag.color,
            description=tag.description,
            created_by_user_id=tag.created_by_user_id,
            created_at=tag.created_at,
            updated_at=tag.updated_at,
            contact_count=count,
        )
        for tag, count in zip(tags, counts, strict=True)
    ]
    return TagListPage(items=items, total=total, limit=limit, offset=skip)


@router.get(
    "/tags/{tag_id}",
    response_model=TagDetailRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def read_tag(
    tag_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> TagDetailRead:
    _ = current_user
    tag = crm_repository.get_tag(session, tag_id)
    if not tag:
        raise not_found("Tag")
    count = int(
        session.scalar(
            select(func.count())
            .select_from(ContactTag)
            .where(ContactTag.tag_id == tag.id)
        )
        or 0
    )
    return TagDetailRead(
        id=tag.id,
        name=tag.name,
        color=tag.color,
        description=tag.description,
        created_by_user_id=tag.created_by_user_id,
        created_at=tag.created_at,
        updated_at=tag.updated_at,
        contact_count=count,
    )


@router.post(
    "/tags",
    response_model=TagDetailRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def create_tag(
    payload: TagCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> TagDetailRead:
    tag, created = crm_repository.upsert_tag(
        session,
        name=payload.name,
        color=payload.color,
        description=payload.description,
        created_by_user_id=current_user.id,
    )
    if not created:
        raise conflict("A tag with this name already exists")
    record_event(
        session,
        action=Action.TAG_CREATED,
        target_type="tag",
        target_id=tag.id,
        actor=current_user,
        metadata={"name": tag.name},
        request=request,
    )
    session.commit()
    session.refresh(tag)
    return TagDetailRead(
        id=tag.id,
        name=tag.name,
        color=tag.color,
        description=tag.description,
        created_by_user_id=tag.created_by_user_id,
        created_at=tag.created_at,
        updated_at=tag.updated_at,
        contact_count=0,
    )


@router.patch(
    "/tags/{tag_id}",
    response_model=TagDetailRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def update_tag(
    tag_id: str,
    payload: TagUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> TagDetailRead:
    tag = crm_repository.get_tag(session, tag_id)
    if not tag:
        raise not_found("Tag")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"]:
        new_name = changes["name"]
        normalized = crm_repository.normalize_tag_name(new_name)
        if normalized != tag.name_normalized:
            collision = crm_repository.get_tag_by_name(session, new_name)
            if collision and collision.id != tag.id:
                raise conflict("A tag with this name already exists")
            tag.name = new_name
            tag.name_normalized = normalized
        changes.pop("name")
    for field, value in changes.items():
        setattr(tag, field, value)
    record_event(
        session,
        action=Action.TAG_UPDATED,
        target_type="tag",
        target_id=tag.id,
        actor=current_user,
        metadata={
            "name": tag.name,
            "changed_fields": sorted(payload.model_dump(exclude_unset=True).keys()),
        },
        request=request,
    )
    session.commit()
    session.refresh(tag)
    count = int(
        session.scalar(
            select(func.count())
            .select_from(ContactTag)
            .where(ContactTag.tag_id == tag.id)
        )
        or 0
    )
    return TagDetailRead(
        id=tag.id,
        name=tag.name,
        color=tag.color,
        description=tag.description,
        created_by_user_id=tag.created_by_user_id,
        created_at=tag.created_at,
        updated_at=tag.updated_at,
        contact_count=count,
    )


@router.delete(
    "/tags/{tag_id}",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def delete_tag(
    tag_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> MessageRead:
    tag = crm_repository.get_tag(session, tag_id)
    if not tag:
        raise not_found("Tag")
    record_event(
        session,
        action=Action.TAG_DELETED,
        target_type="tag",
        target_id=tag.id,
        actor=current_user,
        metadata={"name": tag.name},
        request=request,
    )
    session.delete(tag)
    session.commit()
    return MessageRead(message="Tag deleted")


@router.post(
    "/contacts/{contact_id}/tags",
    response_model=TagRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def add_tag_to_contact(
    contact_id: str,
    payload: ContactTagAssignRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> TagRead:
    contact = crm_repository.get_contact(session, contact_id)
    if not contact:
        raise not_found("Contact")
    if payload.tag_id:
        tag = crm_repository.get_tag(session, payload.tag_id)
        if not tag:
            raise not_found("Tag")
    elif payload.tag_name:
        tag, _ = crm_repository.upsert_tag(
            session,
            name=payload.tag_name,
            color=payload.color,
            created_by_user_id=current_user.id,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either tag_id or tag_name is required",
        )
    added = crm_repository.assign_tag_to_contact(
        session,
        contact_id=contact.id,
        tag_id=tag.id,
        assigned_by_user_id=current_user.id,
        source="manual",
    )
    if added:
        record_event(
            session,
            action=Action.CONTACT_TAG_ADDED,
            target_type="contact",
            target_id=contact.id,
            actor=current_user,
            metadata={"tag_id": tag.id, "tag_name": tag.name},
            request=request,
        )
        session.commit()
    return TagRead(id=tag.id, name=tag.name, color=tag.color)


@router.delete(
    "/contacts/{contact_id}/tags/{tag_id}",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def remove_tag_from_contact(
    contact_id: str,
    tag_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> MessageRead:
    if not crm_repository.get_contact(session, contact_id):
        raise not_found("Contact")
    removed = crm_repository.remove_tag_from_contact(
        session, contact_id=contact_id, tag_id=tag_id
    )
    if removed:
        record_event(
            session,
            action=Action.CONTACT_TAG_REMOVED,
            target_type="contact",
            target_id=contact_id,
            actor=current_user,
            metadata={"tag_id": tag_id},
            request=request,
        )
        session.commit()
    return MessageRead(message="Tag removed" if removed else "Tag was not attached")


@router.post(
    "/contacts/bulk-tag",
    response_model=BulkContactTagResult,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def bulk_contact_tag(
    payload: BulkContactTagRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BulkContactTagResult:
    """Apply one tag add/remove to up to 500 contacts in a single
    audited operation. Used by the list-page bulk actions menu."""
    tag = crm_repository.get_tag(session, payload.tag_id)
    if not tag:
        raise not_found("Tag")

    affected = 0
    skipped = 0
    if payload.action == "add":
        for contact_id in payload.contact_ids:
            if not crm_repository.get_contact(session, contact_id):
                skipped += 1
                continue
            added = crm_repository.assign_tag_to_contact(
                session,
                contact_id=contact_id,
                tag_id=tag.id,
                assigned_by_user_id=current_user.id,
                source="manual",
            )
            if added:
                affected += 1
            else:
                skipped += 1
    else:
        for contact_id in payload.contact_ids:
            removed = crm_repository.remove_tag_from_contact(
                session, contact_id=contact_id, tag_id=tag.id
            )
            if removed:
                affected += 1
            else:
                skipped += 1

    record_event(
        session,
        action=Action.CONTACT_TAGS_BULK_ACTION,
        target_type="tag",
        target_id=tag.id,
        actor=current_user,
        metadata={
            "action": payload.action,
            "tag_name": tag.name,
            "requested": len(payload.contact_ids),
            "affected": affected,
            "skipped": skipped,
        },
        request=request,
    )
    session.commit()
    return BulkContactTagResult(
        action=payload.action,
        tag_id=tag.id,
        affected=affected,
        skipped=skipped,
    )


# ---------------------------------------------------------------------------
# Saved contact views (Sprint P.1 ampliado PR-B)
# ---------------------------------------------------------------------------


def _view_to_read(view, *, current_user: User) -> ContactViewRead:
    filters, columns, sort = contact_views_repository.view_to_dicts(view)
    return ContactViewRead(
        id=view.id,
        name=view.name,
        description=view.description,
        owner_user_id=view.owner_user_id,
        is_owner=view.owner_user_id == current_user.id,
        is_shared=view.is_shared,
        is_default=view.is_default,
        filters=ContactViewFilters(**filters) if filters else ContactViewFilters(),
        columns=columns or {"visible": [], "order": [], "widths": {}},  # type: ignore[arg-type]
        sort=sort or {"sort_by": "created_at", "sort_dir": "desc"},  # type: ignore[arg-type]
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.get(
    "/contact-views",
    response_model=list[ContactViewRead],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_contact_views(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[ContactViewRead]:
    rows = contact_views_repository.list_views_for_user(
        session, user_id=current_user.id
    )
    return [_view_to_read(row, current_user=current_user) for row in rows]


@router.get(
    "/contact-views/{view_id}",
    response_model=ContactViewRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def read_contact_view(
    view_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> ContactViewRead:
    view = contact_views_repository.get_view(session, view_id)
    if not view:
        raise not_found("Contact view")
    if view.owner_user_id != current_user.id and not view.is_shared:
        raise not_found("Contact view")
    return _view_to_read(view, current_user=current_user)


@router.post(
    "/contact-views",
    response_model=ContactViewRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def create_contact_view(
    payload: ContactViewCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> ContactViewRead:
    view = contact_views_repository.create_view(
        session,
        owner_user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        is_shared=payload.is_shared,
        is_default=payload.is_default,
        filters=payload.filters.model_dump(exclude_none=True),
        columns=payload.columns.model_dump(),
        sort=payload.sort.model_dump(),
    )
    record_event(
        session,
        action=Action.CONTACT_VIEW_CREATED,
        target_type="contact_view",
        target_id=view.id,
        actor=current_user,
        metadata={"name": view.name, "is_shared": view.is_shared},
        request=request,
    )
    session.commit()
    session.refresh(view)
    return _view_to_read(view, current_user=current_user)


@router.patch(
    "/contact-views/{view_id}",
    response_model=ContactViewRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def update_contact_view(
    view_id: str,
    payload: ContactViewUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> ContactViewRead:
    view = contact_views_repository.get_view(session, view_id)
    if not view:
        raise not_found("Contact view")
    if view.owner_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not owner")
    changes = payload.model_dump(exclude_unset=True)
    contact_views_repository.update_view(
        session,
        view=view,
        name=changes.get("name"),
        description=changes.get("description"),
        is_shared=changes.get("is_shared"),
        is_default=changes.get("is_default"),
        filters=(
            payload.filters.model_dump(exclude_none=True)
            if payload.filters is not None
            else None
        ),
        columns=(payload.columns.model_dump() if payload.columns is not None else None),
        sort=(payload.sort.model_dump() if payload.sort is not None else None),
    )
    record_event(
        session,
        action=Action.CONTACT_VIEW_UPDATED,
        target_type="contact_view",
        target_id=view.id,
        actor=current_user,
        metadata={"name": view.name, "changed_fields": sorted(changes.keys())},
        request=request,
    )
    session.commit()
    session.refresh(view)
    return _view_to_read(view, current_user=current_user)


@router.delete(
    "/contact-views/{view_id}",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def delete_contact_view(
    view_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> MessageRead:
    view = contact_views_repository.get_view(session, view_id)
    if not view:
        raise not_found("Contact view")
    if view.owner_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not owner")
    record_event(
        session,
        action=Action.CONTACT_VIEW_DELETED,
        target_type="contact_view",
        target_id=view.id,
        actor=current_user,
        metadata={"name": view.name},
        request=request,
    )
    contact_views_repository.delete_view(session, view=view)
    session.commit()
    return MessageRead(message="Contact view deleted")


@router.post(
    "/contact-views/{view_id}/duplicate",
    response_model=ContactViewRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def duplicate_contact_view(
    view_id: str,
    payload: ContactViewDuplicateRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> ContactViewRead:
    source = contact_views_repository.get_view(session, view_id)
    if not source:
        raise not_found("Contact view")
    # Anyone with read access can duplicate; the new row is fully
    # owned by the duplicator with sharing/default flags reset.
    if source.owner_user_id != current_user.id and not source.is_shared:
        raise not_found("Contact view")
    duplicate = contact_views_repository.duplicate_view(
        session,
        source=source,
        owner_user_id=current_user.id,
        name=payload.name,
    )
    record_event(
        session,
        action=Action.CONTACT_VIEW_DUPLICATED,
        target_type="contact_view",
        target_id=duplicate.id,
        actor=current_user,
        metadata={"source_view_id": source.id, "name": duplicate.name},
        request=request,
    )
    session.commit()
    session.refresh(duplicate)
    return _view_to_read(duplicate, current_user=current_user)


@router.post(
    "/contact-views/{view_id}/set-default",
    response_model=ContactViewRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def set_default_contact_view(
    view_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> ContactViewRead:
    view = contact_views_repository.get_view(session, view_id)
    if not view:
        raise not_found("Contact view")
    if view.owner_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not owner")
    contact_views_repository.update_view(
        session, view=view, is_default=True
    )
    record_event(
        session,
        action=Action.CONTACT_VIEW_DEFAULT_SET,
        target_type="contact_view",
        target_id=view.id,
        actor=current_user,
        metadata={"name": view.name},
        request=request,
    )
    session.commit()
    session.refresh(view)
    return _view_to_read(view, current_user=current_user)


# ---------------------------------------------------------------------------
# Contact view → segment / brevo list bridges (Sprint UX)
# ---------------------------------------------------------------------------


def _view_rules_tree(view: ContactView) -> dict[str, Any]:
    """Extract the rules tree from a view's stored filters_json.

    New views (Sprint UX) store the segments engine's
    `{operator, children}` tree under `filters.rules_json`. Old views
    stored only the legacy flat dropdown fields. The action endpoints
    return an empty tree for legacy payloads so the engine compiles
    to "match every contact" instead of crashing.
    """
    if not view.filters_json:
        return {}
    try:
        decoded = json.loads(view.filters_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    # Preferred: wrapped under `rules_json` alongside legacy fields.
    nested = decoded.get("rules_json")
    if isinstance(nested, dict) and (
        "operator" in nested or nested.get("type") == "rule"
    ):
        return nested
    # Edge-case migration path: an old view that was POSTed with the
    # rule tree as the WHOLE filters payload (early UI experiment).
    if "operator" in decoded or decoded.get("type") == "rule":
        return decoded
    return {}


@router.post(
    "/contact-views/{view_id}/save-as-segment",
    response_model=SegmentRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def save_view_as_segment(
    view_id: str,
    payload: ContactViewSaveAsSegmentRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> SegmentRead:
    """Materialise a contact view as a reusable segment.

    The view's stored filter tree (when it's already in the new
    segments-engine shape) is reused verbatim as the segment's
    `rules_json`. The operator owns the new segment; sharing /
    static-list behaviour is left at the segment defaults
    (`is_dynamic=True`, no static ids) — they can tune those later
    from `/segments/{id}`.
    """
    view = contact_views_repository.get_view(session, view_id)
    if view is None:
        raise not_found("Contact view")
    if view.owner_user_id != current_user.id and not view.is_shared:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes acceso a esta vista",
        )

    rules = _view_rules_tree(view)
    try:
        if rules:
            segment_engine.build_filter(rules)
    except segment_engine.SegmentRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    segment = segments_repository.create_segment(
        session,
        owner_user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        rules=rules,
        is_dynamic=True,
        static_contact_ids=None,
        is_shared=payload.is_shared,
        color=payload.color,
    )
    count, duration = segments_repository.evaluate_segment(session, segment)
    record_event(
        session,
        action=Action.SEGMENT_CREATED,
        target_type="segment",
        target_id=segment.id,
        actor=current_user,
        metadata={
            "name": segment.name,
            "count": count,
            "source": "contact_view",
            "view_id": view.id,
        },
        request=request,
    )
    record_event(
        session,
        action=Action.SEGMENT_EVALUATED,
        target_type="segment",
        target_id=segment.id,
        actor=current_user,
        metadata={"count": count, "duration_ms": int(duration * 1000)},
        request=request,
    )
    session.commit()
    session.refresh(segment)
    return _segment_to_read(segment, current_user=current_user)


@router.post(
    "/contact-views/{view_id}/push-to-brevo-list",
    response_model=ContactViewPushToBrevoResponse,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def push_view_to_brevo_list(
    view_id: str,
    payload: ContactViewPushToBrevoRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> ContactViewPushToBrevoResponse:
    """One-shot push of the view's matching contacts to a Brevo list.

    Internally:
      1. Materialise a hidden segment that mirrors the view's tree —
         `BrevoSyncTarget` needs a segment_id and we don't want every
         push to spawn a visible segment in `/segments`.
      2. Resolve `brevo_list_id` (either the one passed in or the one
         Brevo returns after creating `new_list_name`).
      3. Create a `BrevoSyncTarget` and enqueue its `push_target`
         job — same machinery the persistent sync targets use, so we
         get retries, audit and de-dup for free.
    """
    from app.integrations.brevo.client import BrevoClient  # noqa: PLC0415
    from app.models.brevo import BrevoSyncTarget, SyncDirection  # noqa: PLC0415

    if (payload.brevo_list_id is None) == (payload.new_list_name is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pasa exactamente uno: brevo_list_id O new_list_name",
        )

    view = contact_views_repository.get_view(session, view_id)
    if view is None:
        raise not_found("Contact view")
    if view.owner_user_id != current_user.id and not view.is_shared:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes acceso a esta vista",
        )

    rules = _view_rules_tree(view)
    try:
        filter_clause = segment_engine.build_filter(rules)
    except segment_engine.SegmentRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    contacts_to_push = int(
        session.scalar(
            select(func.count()).select_from(
                select(Contact.id).where(filter_clause).subquery()
            )
        )
        or 0
    )

    # Ensure the target Brevo list exists (create-then-id when the
    # caller asked for a fresh one). We do this BEFORE creating the
    # auxiliary segment so a Brevo 4xx aborts cleanly.
    require_brevo_account(session, payload.brevo_account_id)
    list_id = payload.brevo_list_id
    if list_id is None:
        async def _create() -> int:
            async with BrevoClient(session, payload.brevo_account_id) as client:
                created = await client.create_list(payload.new_list_name)
                raw = created.get("id")
                if raw is None:
                    raise IntegrationServerError(
                        "Brevo create_list response missing id",
                        system="brevo",
                        account_id=payload.brevo_account_id,
                    )
                return int(raw)

        try:
            list_id = asyncio.run(_create())
        except IntegrationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
            ) from exc

    # The auxiliary segment is name-stamped so the audit log makes the
    # provenance obvious — it's also visible in `/segments`, but that's
    # fine, the operator can rename or delete it later. `is_shared=False`
    # keeps it scoped to the pusher.
    aux_name = f"Push vista: {view.name}"[:100]
    aux_segment = segments_repository.create_segment(
        session,
        owner_user_id=current_user.id,
        name=aux_name,
        description=f"Auto-creado para push a Brevo lista {list_id}",
        rules=rules,
        is_dynamic=True,
        static_contact_ids=None,
        is_shared=False,
        color=None,
    )

    target = BrevoSyncTarget(
        brevo_account_id=payload.brevo_account_id,
        name=f"Push vista: {view.name}"[:200],
        description=f"Auto-creado desde vista {view.id}",
        segment_id=aux_segment.id,
        brevo_list_id=str(list_id),
        sync_direction=SyncDirection.PUSH_ONLY,
        auto_sync_enabled=False,
        sync_interval_minutes=None,
    )
    session.add(target)
    session.flush()

    sync_log_id, job_id = enqueue_sync_job(
        session,
        system="brevo",
        account_id=payload.brevo_account_id,
        operation="push_target",
        payload={"target_id": target.id},
        triggered_by_user_id=current_user.id,
        request=request,
    )
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_TRIGGERED,
        target_type="brevo_sync_target",
        target_id=target.id,
        actor=current_user,
        metadata={
            "event": "view_pushed_to_brevo_list",
            "view_id": view.id,
            "brevo_list_id": list_id,
            "contacts_to_push": contacts_to_push,
        },
        request=request,
    )
    session.commit()
    return ContactViewPushToBrevoResponse(
        sync_log_id=sync_log_id,
        job_id=job_id,
        target_id=target.id,
        segment_id=aux_segment.id,
        contacts_to_push=contacts_to_push,
        brevo_list_id=list_id,
    )


# ---------------------------------------------------------------------------
# Pipelines (Sprint P.2)
# ---------------------------------------------------------------------------


def _pipeline_to_read(
    session: Session, pipeline: Pipeline
) -> PipelineRead:
    return PipelineRead(
        id=pipeline.id,
        name=pipeline.name,
        description=pipeline.description,
        color=pipeline.color,
        is_active=pipeline.is_active,
        is_shared=pipeline.is_shared,
        owner_user_id=pipeline.owner_user_id,
        stages=[
            PipelineStageRead.model_validate(stage)
            for stage in sorted(pipeline.stages, key=lambda s: s.position)
        ],
        contact_count=pipelines_repository.contact_count(session, pipeline.id),
        created_at=pipeline.created_at,
        updated_at=pipeline.updated_at,
    )


@router.get(
    "/pipelines",
    response_model=list[PipelineRead],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_pipelines(
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[PipelineRead]:
    _ = current_user
    rows = pipelines_repository.list_pipelines(
        session, include_inactive=include_inactive
    )
    return [_pipeline_to_read(session, row) for row in rows]


@router.get(
    "/pipelines/{pipeline_id}",
    response_model=PipelineRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def read_pipeline(
    pipeline_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> PipelineRead:
    _ = current_user
    pipeline = pipelines_repository.get_pipeline(session, pipeline_id)
    if not pipeline:
        raise not_found("Pipeline")
    return _pipeline_to_read(session, pipeline)


@router.post(
    "/pipelines",
    response_model=PipelineRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def create_pipeline(
    payload: PipelineCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> PipelineRead:
    pipeline = pipelines_repository.create_pipeline(
        session,
        owner_user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        color=payload.color,
        is_shared=payload.is_shared,
        stages=[stage.model_dump() for stage in payload.stages],
    )
    record_event(
        session,
        action=Action.PIPELINE_CREATED,
        target_type="pipeline",
        target_id=pipeline.id,
        actor=current_user,
        metadata={"name": pipeline.name, "stage_count": len(payload.stages)},
        request=request,
    )
    session.commit()
    session.refresh(pipeline)
    return _pipeline_to_read(session, pipeline)


@router.patch(
    "/pipelines/{pipeline_id}",
    response_model=PipelineRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def update_pipeline(
    pipeline_id: str,
    payload: PipelineUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> PipelineRead:
    pipeline = pipelines_repository.get_pipeline(session, pipeline_id)
    if not pipeline:
        raise not_found("Pipeline")
    changes = payload.model_dump(exclude_unset=True)
    pipelines_repository.update_pipeline(session, pipeline=pipeline, **changes)
    record_event(
        session,
        action=Action.PIPELINE_UPDATED,
        target_type="pipeline",
        target_id=pipeline.id,
        actor=current_user,
        metadata={"name": pipeline.name, "changed_fields": sorted(changes.keys())},
        request=request,
    )
    session.commit()
    session.refresh(pipeline)
    return _pipeline_to_read(session, pipeline)


@router.delete(
    "/pipelines/{pipeline_id}",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def delete_pipeline(
    pipeline_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> MessageRead:
    pipeline = pipelines_repository.get_pipeline(session, pipeline_id)
    if not pipeline:
        raise not_found("Pipeline")
    pipelines_repository.soft_delete_pipeline(session, pipeline)
    record_event(
        session,
        action=Action.PIPELINE_DELETED,
        target_type="pipeline",
        target_id=pipeline.id,
        actor=current_user,
        metadata={"name": pipeline.name},
        request=request,
    )
    session.commit()
    return MessageRead(message="Pipeline archived")


@router.post(
    "/pipelines/{pipeline_id}/duplicate",
    response_model=PipelineRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def duplicate_pipeline(
    pipeline_id: str,
    payload: PipelineDuplicateRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> PipelineRead:
    source = pipelines_repository.get_pipeline(session, pipeline_id)
    if not source:
        raise not_found("Pipeline")
    duplicate = pipelines_repository.duplicate_pipeline(
        session,
        source=source,
        owner_user_id=current_user.id,
        name=payload.name,
        include_contacts=payload.include_contacts,
    )
    record_event(
        session,
        action=Action.PIPELINE_DUPLICATED,
        target_type="pipeline",
        target_id=duplicate.id,
        actor=current_user,
        metadata={
            "source_pipeline_id": source.id,
            "name": duplicate.name,
            "include_contacts": payload.include_contacts,
        },
        request=request,
    )
    session.commit()
    session.refresh(duplicate)
    return _pipeline_to_read(session, duplicate)


# ----- Stages -----


@router.post(
    "/pipelines/{pipeline_id}/stages",
    response_model=PipelineStageRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def create_pipeline_stage(
    pipeline_id: str,
    payload: PipelineStageCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> PipelineStage:
    pipeline = pipelines_repository.get_pipeline(session, pipeline_id)
    if not pipeline:
        raise not_found("Pipeline")
    stage = pipelines_repository.add_stage(
        session,
        pipeline=pipeline,
        name=payload.name,
        description=payload.description,
        color=payload.color,
        is_won=payload.is_won,
        is_lost=payload.is_lost,
        target_days=payload.target_days,
        position=payload.position,
    )
    record_event(
        session,
        action=Action.PIPELINE_STAGE_CREATED,
        target_type="pipeline_stage",
        target_id=stage.id,
        actor=current_user,
        metadata={
            "pipeline_id": pipeline.id,
            "name": stage.name,
            "position": stage.position,
        },
        request=request,
    )
    session.commit()
    session.refresh(stage)
    return stage


@router.patch(
    "/pipeline-stages/{stage_id}",
    response_model=PipelineStageRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def update_pipeline_stage(
    stage_id: str,
    payload: PipelineStageUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> PipelineStage:
    stage = session.get(PipelineStage, stage_id)
    if not stage:
        raise not_found("Pipeline stage")
    changes = payload.model_dump(exclude_unset=True)
    pipelines_repository.update_stage(session, stage=stage, **changes)
    record_event(
        session,
        action=Action.PIPELINE_STAGE_UPDATED,
        target_type="pipeline_stage",
        target_id=stage.id,
        actor=current_user,
        metadata={
            "pipeline_id": stage.pipeline_id,
            "changed_fields": sorted(changes.keys()),
        },
        request=request,
    )
    session.commit()
    session.refresh(stage)
    return stage


@router.delete(
    "/pipeline-stages/{stage_id}",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def delete_pipeline_stage(
    stage_id: str,
    request: Request,
    move_to_stage_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> MessageRead:
    stage = session.get(PipelineStage, stage_id)
    if not stage:
        raise not_found("Pipeline stage")
    pipeline_id = stage.pipeline_id
    stage_name = stage.name
    try:
        moved = pipelines_repository.delete_stage(
            session, stage=stage, move_to_stage_id=move_to_stage_id
        )
    except pipelines_repository.StageHasContactsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    record_event(
        session,
        action=Action.PIPELINE_STAGE_DELETED,
        target_type="pipeline_stage",
        target_id=stage_id,
        actor=current_user,
        metadata={
            "pipeline_id": pipeline_id,
            "name": stage_name,
            "moved_contacts": moved,
        },
        request=request,
    )
    session.commit()
    return MessageRead(
        message=f"Stage deleted; {moved} contact(s) relocated"
    )


@router.post(
    "/pipelines/{pipeline_id}/stages/reorder",
    response_model=list[PipelineStageRead],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def reorder_pipeline_stages(
    pipeline_id: str,
    payload: PipelineStageReorderRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> list[PipelineStage]:
    pipeline = pipelines_repository.get_pipeline(session, pipeline_id)
    if not pipeline:
        raise not_found("Pipeline")
    try:
        ordered = pipelines_repository.reorder_stages(
            session, pipeline=pipeline, stage_ids=payload.stage_ids
        )
    except pipelines_repository.InvalidStageOrderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    record_event(
        session,
        action=Action.PIPELINE_STAGE_REORDERED,
        target_type="pipeline",
        target_id=pipeline.id,
        actor=current_user,
        metadata={"stage_ids": payload.stage_ids},
        request=request,
    )
    session.commit()
    return ordered


# ----- Contact assignments -----


@router.get(
    "/contacts/{contact_id}/pipelines",
    response_model=list[ContactPipelineSummary],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_contact_pipelines(
    contact_id: str,
    include_archived: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[ContactPipelineSummary]:
    """Pipelines this contact lives in + its current stage in each.
    The contact-detail screen calls this once and renders the
    "Pipelines" section without N round-trips."""
    _ = current_user
    if not crm_repository.get_contact(session, contact_id):
        raise not_found("Contact")
    assignments = pipelines_repository.assignments_for_contact(
        session, contact_id, include_archived=include_archived
    )
    now = datetime.now(UTC)
    out: list[ContactPipelineSummary] = []
    for assignment in assignments:
        pipeline = session.get(Pipeline, assignment.pipeline_id)
        stage = session.get(PipelineStage, assignment.stage_id)
        if pipeline is None or stage is None:
            continue
        entered = assignment.entered_stage_at
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=UTC)
        out.append(
            ContactPipelineSummary(
                assignment_id=assignment.id,
                pipeline_id=pipeline.id,
                pipeline_name=pipeline.name,
                pipeline_color=pipeline.color,
                stage_id=stage.id,
                stage_name=stage.name,
                stage_color=stage.color,
                stage_position=stage.position,
                is_won=stage.is_won,
                is_lost=stage.is_lost,
                days_in_stage=max(0, (now - entered).days),
                entered_stage_at=assignment.entered_stage_at,
                added_to_pipeline_at=assignment.added_to_pipeline_at,
            )
        )
    return out


@router.post(
    "/contacts/{contact_id}/pipelines",
    response_model=ContactPipelineStageRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def add_contact_to_pipeline(
    contact_id: str,
    payload: ContactPipelineAddRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactPipelineStage:
    contact = crm_repository.get_contact(session, contact_id)
    if not contact:
        raise not_found("Contact")
    pipeline = pipelines_repository.get_pipeline(session, payload.pipeline_id)
    if not pipeline:
        raise not_found("Pipeline")
    try:
        assignment = pipelines_repository.add_contact_to_pipeline(
            session,
            contact=contact,
            pipeline=pipeline,
            stage_id=payload.stage_id,
            note=payload.note,
            moved_by_user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    record_event(
        session,
        action=Action.CONTACT_PIPELINE_STAGE_ADDED,
        target_type="contact_pipeline_stage",
        target_id=assignment.id,
        actor=current_user,
        metadata={
            "contact_id": contact.id,
            "pipeline_id": pipeline.id,
            "stage_id": assignment.stage_id,
        },
        request=request,
    )
    session.commit()
    session.refresh(assignment)
    return assignment


@router.patch(
    "/contact-pipeline-stages/{assignment_id}",
    response_model=ContactPipelineStageRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def move_contact_in_pipeline(
    assignment_id: str,
    payload: ContactPipelineMoveRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactPipelineStage:
    assignment = pipelines_repository.get_assignment(session, assignment_id)
    if not assignment:
        raise not_found("Contact pipeline stage")
    try:
        updated = pipelines_repository.move_contact_to_stage(
            session,
            assignment=assignment,
            new_stage_id=payload.stage_id,
            note=payload.note,
            moved_by_user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    record_event(
        session,
        action=Action.CONTACT_PIPELINE_STAGE_CHANGED,
        target_type="contact_pipeline_stage",
        target_id=updated.id,
        actor=current_user,
        metadata={
            "pipeline_id": updated.pipeline_id,
            "stage_id": updated.stage_id,
        },
        request=request,
    )
    session.commit()
    session.refresh(updated)
    return updated


@router.delete(
    "/contact-pipeline-stages/{assignment_id}",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def archive_contact_in_pipeline(
    assignment_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> MessageRead:
    assignment = pipelines_repository.get_assignment(session, assignment_id)
    if not assignment:
        raise not_found("Contact pipeline stage")
    pipelines_repository.archive_assignment(session, assignment)
    record_event(
        session,
        action=Action.CONTACT_PIPELINE_STAGE_ARCHIVED,
        target_type="contact_pipeline_stage",
        target_id=assignment.id,
        actor=current_user,
        metadata={
            "contact_id": assignment.contact_id,
            "pipeline_id": assignment.pipeline_id,
        },
        request=request,
    )
    session.commit()
    return MessageRead(message="Assignment archived")


@router.get(
    "/pipelines/{pipeline_id}/contacts",
    response_model=PipelineContactsResponse,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_pipeline_contacts(
    pipeline_id: str,
    per_stage_limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> PipelineContactsResponse:
    _ = current_user
    pipeline = pipelines_repository.get_pipeline(session, pipeline_id)
    if not pipeline:
        raise not_found("Pipeline")
    now = datetime.now(UTC)
    groups = pipelines_repository.list_contacts_grouped_by_stage(
        session, pipeline, per_stage_limit=per_stage_limit
    )
    stage_groups: list[PipelineStageGroup] = []
    for stage, pairs, total in groups:
        cards: list[PipelineContactCard] = []
        for assignment, contact in pairs:
            entered = assignment.entered_stage_at
            if entered.tzinfo is None:
                entered = entered.replace(tzinfo=UTC)
            days = max(0, (now - entered).days)
            cards.append(
                PipelineContactCard(
                    id=assignment.id,
                    contact_id=contact.id,
                    first_name=contact.first_name,
                    last_name=contact.last_name,
                    email=contact.email,
                    phone=contact.phone,
                    lead_score=contact.lead_score,
                    tags=[
                        TagRead.model_validate(assignment.tag)
                        for assignment in contact.tag_assignments
                    ],
                    entered_stage_at=assignment.entered_stage_at,
                    added_to_pipeline_at=assignment.added_to_pipeline_at,
                    days_in_stage=days,
                )
            )
        stage_groups.append(
            PipelineStageGroup(
                stage_id=stage.id,
                stage_name=stage.name,
                stage_color=stage.color,
                position=stage.position,
                is_won=stage.is_won,
                is_lost=stage.is_lost,
                target_days=stage.target_days,
                total=total,
                contacts=cards,
            )
        )
    return PipelineContactsResponse(
        pipeline=_pipeline_to_read(session, pipeline),
        stages=stage_groups,
    )


@router.get(
    "/pipelines/{pipeline_id}/report",
    response_model=PipelineReportResponse,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def pipeline_report(
    pipeline_id: str,
    from_date: datetime | None = Query(default=None),
    to_date: datetime | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> PipelineReportResponse:
    _ = current_user
    pipeline = pipelines_repository.get_pipeline(session, pipeline_id)
    if not pipeline:
        raise not_found("Pipeline")
    report = pipelines_repository.compute_report(
        session, pipeline, from_date=from_date, to_date=to_date
    )
    return PipelineReportResponse(
        pipeline_id=report["pipeline_id"],
        pipeline_name=report["pipeline_name"],
        total_contacts=report["total_contacts"],
        won_count=report["won_count"],
        lost_count=report["lost_count"],
        metrics=[PipelineStageMetric(**m) for m in report["metrics"]],
    )


@router.get(
    "/pipelines/{pipeline_id}/stalled-contacts",
    response_model=list[StalledContactRow],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def pipeline_stalled_contacts(
    pipeline_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[StalledContactRow]:
    """Surfaces the contacts that have been in their current stage
    longer than its `target_days`. Sorted by overdue days desc so the
    most urgent rows render at the top of the report screen."""
    _ = current_user
    pipeline = pipelines_repository.get_pipeline(session, pipeline_id)
    if not pipeline:
        raise not_found("Pipeline")
    rows = pipelines_repository.list_stalled_contacts(
        session, pipeline, limit=limit
    )
    return [StalledContactRow(**row) for row in rows]


# ---------------------------------------------------------------------------
# Pipeline templates + AI assist (Sprint P.2.5)
# ---------------------------------------------------------------------------


@router.get(
    "/pipeline-templates",
    response_model=list[PipelineTemplate],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_pipeline_templates(
    current_user: User = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Hardcoded library of starter pipelines. Same set per release;
    the wizard passes the chosen `id` back to `/from-template`."""
    _ = current_user
    return pipeline_templates_service.list_templates()


@router.post(
    "/pipelines/from-template",
    response_model=PipelineRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def create_pipeline_from_template(
    payload: PipelineFromTemplateRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> PipelineRead:
    template_payload = pipeline_templates_service.build_pipeline_payload(
        payload.template_id, name=payload.name
    )
    if template_payload is None:
        raise not_found("Pipeline template")
    pipeline = pipelines_repository.create_pipeline(
        session,
        owner_user_id=current_user.id,
        name=template_payload["name"],
        description=template_payload.get("description"),
        color=template_payload.get("color"),
        is_shared=True,
        stages=template_payload["stages"],
    )
    record_event(
        session,
        action=Action.PIPELINE_CREATED,
        target_type="pipeline",
        target_id=pipeline.id,
        actor=current_user,
        metadata={
            "name": pipeline.name,
            "stage_count": len(template_payload["stages"]),
            "source": "template",
            "template_id": payload.template_id,
        },
        request=request,
    )
    session.commit()
    session.refresh(pipeline)
    return _pipeline_to_read(session, pipeline)


@router.post(
    "/pipelines/generate-ai",
    response_model=PipelineProposal,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def generate_pipeline_with_ai(
    payload: PipelineGenerateAIRequest,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(require_manager),
) -> PipelineProposal:
    """Ask Claude to draft a pipeline structure from a natural-language
    description. Returns the proposal WITHOUT persisting — the
    operator must POST it back to `/pipelines` to materialise it."""
    if not settings.ai_features_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI features are not configured on this deployment.",
        )
    try:
        proposal = llm_service.generate_pipeline_proposal(
            payload.description, user_id=current_user.id
        )
    except llm_service.LLMRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    except llm_service.LLMNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI features are not configured on this deployment.",
        ) from exc
    except llm_service.LLMError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    record_event(
        session,
        action=Action.PIPELINE_AI_GENERATED,
        target_type="pipeline",
        actor=current_user,
        # NEVER log the raw description — potential PII / customer
        # secrets. Length + stage count is enough usage signal.
        metadata={
            "description_length": len(payload.description),
            "stages_proposed": len(proposal["stages"]),
        },
        request=request,
    )
    session.commit()
    return PipelineProposal(
        name=proposal["name"],
        description=proposal.get("description"),
        color=proposal.get("color"),
        stages=[PipelineProposalStage(**stage) for stage in proposal["stages"]],
    )


# ---------------------------------------------------------------------------
# Segments (Sprint P.3)
# ---------------------------------------------------------------------------


def _segment_to_read(segment: Segment, *, current_user: User) -> SegmentRead:
    return SegmentRead(
        id=segment.id,
        name=segment.name,
        description=segment.description,
        color=segment.color,
        owner_user_id=segment.owner_user_id,
        is_owner=segment.owner_user_id == current_user.id,
        is_shared=segment.is_shared,
        is_dynamic=segment.is_dynamic,
        rules=segments_repository.decode_rules(segment),
        static_contact_ids=segments_repository.decode_static_ids(segment),
        cached_count=segment.cached_count,
        last_evaluated_at=segment.last_evaluated_at,
        external_source=segment.external_source,
        external_last_refreshed_at=segment.external_last_refreshed_at,
        external_refresh_interval_minutes=segment.external_refresh_interval_minutes,
        created_at=segment.created_at,
        updated_at=segment.updated_at,
    )


@router.get(
    "/segments/available-fields",
    response_model=list[SegmentFieldDescriptor],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def segment_available_fields(
    current_user: User = Depends(require_viewer),
) -> list[dict[str, Any]]:
    _ = current_user
    return segment_fields.list_fields_for_ui()


@router.get(
    "/segments/available-countries",
    response_model=list[SegmentCountryOption],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def segment_available_countries(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[SegmentCountryOption]:
    """Distinct `address_country` values present in the contacts table
    so the value picker for the `address_country` rule shows real
    options, not a free-text input. Ordered by usage descending."""
    _ = current_user
    rows = session.execute(
        select(
            Contact.address_country,
            func.count(Contact.id).label("contact_count"),
        )
        .where(Contact.address_country.is_not(None))
        .where(Contact.address_country != "")
        .group_by(Contact.address_country)
        .order_by(func.count(Contact.id).desc(), Contact.address_country)
        .limit(200)
    ).all()
    return [
        SegmentCountryOption(code=row[0], contact_count=int(row.contact_count))
        for row in rows
        if row[0]
    ]


@router.get(
    "/segments/available-origin-accounts",
    response_model=list[SegmentOriginAccountOption],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def segment_available_origin_accounts(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[SegmentOriginAccountOption]:
    """Enabled integration accounts en formato `{value, label, system}`.

    PR-Db: `value` es ahora la clave compuesta `"system:account_id"`
    para que el motor de filtros (`_compile_external_ref_leaf`) genere
    SQL con la tupla `(system, account_id)`. Antes era `account_id`
    plano y matcheaba cross-system — Brevo y Agile pueden compartir el
    mismo literal "default", así un filtro `origin_account_id = "default"`
    devolvía contactos de ambos sistemas.

    El `label` sigue siendo "{Display Name} · {account_id}" para que el
    operador distinga dos cuentas AgileCRM con el mismo display name.
    """
    _ = current_user
    rows = session.execute(
        select(IntegrationAccount)
        .where(IntegrationAccount.enabled.is_(True))
        .order_by(IntegrationAccount.system, IntegrationAccount.display_name)
    ).scalars().all()
    return [
        SegmentOriginAccountOption(
            value=(
                f"{row.system.value if hasattr(row.system, 'value') else row.system}"
                f":{row.account_id}"
            ),
            label=f"{row.display_name} · {row.account_id}",
            system=row.system.value if hasattr(row.system, "value") else str(row.system),
        )
        for row in rows
    ]


@router.get(
    "/segments/templates",
    response_model=list[SegmentTemplate],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def segment_templates(
    current_user: User = Depends(require_viewer),
) -> list[dict[str, Any]]:
    _ = current_user
    return segments_templates.list_templates()


@router.get(
    "/segments",
    response_model=list[SegmentRead],
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_segments(
    # PR-Cg: `q` + `limit` para el SegmentPicker server-side. Sin
    # params, sigue devolviendo el listado completo igual que /segments.
    q: str | None = Query(
        default=None,
        description="Filtro substring case-insensitive sobre `name`",
    ),
    limit: int | None = Query(default=None, ge=1, le=200),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[SegmentRead]:
    rows = segments_repository.list_segments(
        session, user_id=current_user.id, q=q, limit=limit
    )
    return [_segment_to_read(row, current_user=current_user) for row in rows]


@router.get(
    "/segments/{segment_id}",
    response_model=SegmentRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def read_segment(
    segment_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> SegmentRead:
    segment = segments_repository.get_segment(session, segment_id)
    if not segment or (
        segment.owner_user_id != current_user.id and not segment.is_shared
    ):
        raise not_found("Segment")
    return _segment_to_read(segment, current_user=current_user)


@router.post(
    "/segments",
    response_model=SegmentRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def create_segment(
    payload: SegmentCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> SegmentRead:
    try:
        if payload.rules:
            segment_engine.build_filter(payload.rules)
    except segment_engine.SegmentRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    segment = segments_repository.create_segment(
        session,
        owner_user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        rules=payload.rules,
        is_dynamic=payload.is_dynamic,
        static_contact_ids=payload.static_contact_ids,
        is_shared=payload.is_shared,
        color=payload.color,
    )
    count, duration = segments_repository.evaluate_segment(session, segment)
    record_event(
        session,
        action=Action.SEGMENT_CREATED,
        target_type="segment",
        target_id=segment.id,
        actor=current_user,
        metadata={"name": segment.name, "count": count},
        request=request,
    )
    record_event(
        session,
        action=Action.SEGMENT_EVALUATED,
        target_type="segment",
        target_id=segment.id,
        actor=current_user,
        metadata={"count": count, "duration_ms": int(duration * 1000)},
        request=request,
    )
    session.commit()
    session.refresh(segment)
    return _segment_to_read(segment, current_user=current_user)


@router.patch(
    "/segments/{segment_id}",
    response_model=SegmentRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def update_segment(
    segment_id: str,
    payload: SegmentUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> SegmentRead:
    segment = segments_repository.get_segment(session, segment_id)
    if not segment:
        raise not_found("Segment")
    if segment.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not owner"
        )
    if payload.rules is not None:
        try:
            segment_engine.build_filter(payload.rules)
        except segment_engine.SegmentRuleError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
    changes = payload.model_dump(exclude_unset=True)
    segments_repository.update_segment(
        session,
        segment=segment,
        name=changes.get("name"),
        description=changes.get("description"),
        color=changes.get("color"),
        is_shared=changes.get("is_shared"),
        is_dynamic=changes.get("is_dynamic"),
        rules=changes.get("rules"),
        static_contact_ids=changes.get("static_contact_ids"),
    )
    count, duration = segments_repository.evaluate_segment(session, segment)
    record_event(
        session,
        action=Action.SEGMENT_UPDATED,
        target_type="segment",
        target_id=segment.id,
        actor=current_user,
        metadata={
            "name": segment.name,
            "changed_fields": sorted(changes.keys()),
            "count": count,
        },
        request=request,
    )
    record_event(
        session,
        action=Action.SEGMENT_EVALUATED,
        target_type="segment",
        target_id=segment.id,
        actor=current_user,
        metadata={"count": count, "duration_ms": int(duration * 1000)},
        request=request,
    )
    session.commit()
    session.refresh(segment)
    return _segment_to_read(segment, current_user=current_user)


@router.delete(
    "/segments/{segment_id}",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def delete_segment(
    segment_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> MessageRead:
    segment = segments_repository.get_segment(session, segment_id)
    if not segment:
        raise not_found("Segment")
    if segment.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not owner"
        )
    record_event(
        session,
        action=Action.SEGMENT_DELETED,
        target_type="segment",
        target_id=segment.id,
        actor=current_user,
        metadata={"name": segment.name},
        request=request,
    )
    segments_repository.delete_segment(session, segment)
    session.commit()
    return MessageRead(message="Segment deleted")


@router.post(
    "/segments/{segment_id}/duplicate",
    response_model=SegmentRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def duplicate_segment(
    segment_id: str,
    payload: SegmentDuplicateRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> SegmentRead:
    source = segments_repository.get_segment(session, segment_id)
    if not source or (
        source.owner_user_id != current_user.id and not source.is_shared
    ):
        raise not_found("Segment")
    duplicate = segments_repository.duplicate_segment(
        session,
        source=source,
        owner_user_id=current_user.id,
        name=payload.name,
    )
    segments_repository.evaluate_segment(session, duplicate)
    record_event(
        session,
        action=Action.SEGMENT_DUPLICATED,
        target_type="segment",
        target_id=duplicate.id,
        actor=current_user,
        metadata={"source_segment_id": source.id, "name": duplicate.name},
        request=request,
    )
    session.commit()
    session.refresh(duplicate)
    return _segment_to_read(duplicate, current_user=current_user)


@router.get(
    "/segments/{segment_id}/contacts",
    response_model=ContactListPage,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def list_segment_contacts(
    segment_id: str,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    sort_by: str = Query(default="created_at"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> ContactListPage:
    segment = segments_repository.get_segment(session, segment_id)
    if not segment or (
        segment.owner_user_id != current_user.id and not segment.is_shared
    ):
        raise not_found("Segment")
    items, total = segments_repository.list_segment_contacts(
        session,
        segment,
        skip=skip,
        limit=limit,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    return ContactListPage(
        items=[ContactRead.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=skip,
    )


@router.get(
    "/segments/{segment_id}/count",
    response_model=CountRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def segment_count(
    segment_id: str,
    request: Request,
    force_refresh: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> CountRead:
    segment = segments_repository.get_segment(session, segment_id)
    if not segment or (
        segment.owner_user_id != current_user.id and not segment.is_shared
    ):
        raise not_found("Segment")
    if force_refresh or segment.cached_count is None:
        count, duration = segments_repository.evaluate_segment(session, segment)
        record_event(
            session,
            action=Action.SEGMENT_EVALUATED,
            target_type="segment",
            target_id=segment.id,
            actor=current_user,
            metadata={
                "count": count,
                "duration_ms": int(duration * 1000),
                "trigger": "force_refresh" if force_refresh else "stale",
            },
            request=request,
        )
        session.commit()
        return CountRead(total=count)
    return CountRead(total=segment.cached_count)


@router.post(
    "/segments/preview",
    response_model=SegmentPreviewResponse,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def segment_preview(
    payload: SegmentPreviewRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> SegmentPreviewResponse:
    _ = current_user
    try:
        segment_engine.build_filter(payload.rules)
    except segment_engine.SegmentRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    count, sample = segments_repository.preview_rules(session, payload.rules)
    return SegmentPreviewResponse(
        count=count,
        sample=[
            SegmentPreviewContactCard(
                id=contact.id,
                first_name=contact.first_name,
                last_name=contact.last_name,
                email=contact.email,
                lead_score=contact.lead_score,
            )
            for contact in sample
        ],
    )


@router.post(
    "/segments/ai-generate",
    response_model=SegmentAIGenerateResponse,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def segment_ai_generate(
    payload: SegmentAIGenerateRequest,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(require_user),
) -> SegmentAIGenerateResponse:
    if not settings.ai_features_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI features are not configured on this deployment.",
        )
    try:
        result = llm_service.generate_segment_rules(
            payload.description,
            user_id=current_user.id,
            session=session,
        )
    except llm_service.LLMRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc
    except llm_service.LLMNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except llm_service.LLMUpstreamError as exc:
        # The LLM responded but didn't return parseable JSON. Surface
        # an operator-friendly message instead of leaking provider
        # internals — the actionable hint is to reword the prompt.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "La IA no pudo generar reglas para esta descripción. "
                "Intenta reformular usando los nombres de los campos "
                "disponibles."
            ),
        ) from exc
    except llm_service.LLMError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    rules = result.get("rules")
    response = SegmentAIGenerateResponse(error=result.get("error"))
    if rules is not None:
        try:
            segment_engine.build_filter(rules)
        except segment_engine.SegmentRuleError as exc:
            response.error = f"La IA propuso reglas inválidas: {exc}"
        else:
            response.rules = rules
            count, sample = segments_repository.preview_rules(session, rules)
            response.count = count
            response.sample = [
                SegmentPreviewContactCard(
                    id=contact.id,
                    first_name=contact.first_name,
                    last_name=contact.last_name,
                    email=contact.email,
                    lead_score=contact.lead_score,
                )
                for contact in sample
            ]

    record_event(
        session,
        action=Action.SEGMENT_AI_GENERATED,
        target_type="segment",
        actor=current_user,
        metadata={
            "description_length": len(payload.description),
            "has_rules": rules is not None,
        },
        request=request,
    )
    session.commit()
    return response


@router.post(
    "/segments/ai-explain",
    response_model=SegmentAIExplainResponse,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def segment_ai_explain(
    payload: SegmentAIExplainRequest,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(require_viewer),
) -> SegmentAIExplainResponse:
    if not settings.ai_features_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI features are not configured on this deployment.",
        )
    rules = payload.rules
    if rules is None and payload.segment_id:
        segment = segments_repository.get_segment(session, payload.segment_id)
        if not segment or (
            segment.owner_user_id != current_user.id and not segment.is_shared
        ):
            raise not_found("Segment")
        rules = segments_repository.decode_rules(segment)
    if not rules:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="rules or segment_id is required",
        )
    try:
        explanation = llm_service.explain_segment_rules(
            rules, user_id=current_user.id
        )
    except llm_service.LLMRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc
    except llm_service.LLMNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except llm_service.LLMError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    record_event(
        session,
        action=Action.SEGMENT_AI_EXPLAINED,
        target_type="segment",
        target_id=payload.segment_id,
        actor=current_user,
        metadata={
            "explanation_length": len(explanation),
            "rules_size": len(str(rules)),
        },
        request=request,
    )
    session.commit()
    return SegmentAIExplainResponse(explanation=explanation)


router.include_router(brevo_router)
router.include_router(integration_accounts_router)
router.include_router(integration_settings_deprecated_router)
router.include_router(sync_router)
router.include_router(webhooks_router)
router.include_router(gdpr_router)
