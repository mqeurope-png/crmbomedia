# ruff: noqa: I001
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.integration_settings import router as integration_settings_router
from app.core.auth import (
    get_current_user,
    require_admin,
    require_manager,
    require_user,
    require_viewer,
)
from app.core.config import Settings, get_settings
from app.core.errors import conflict, not_found, unauthorized
from app.core.security import (
    create_access_token,
    create_reset_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.db.session import get_session
from app.models.crm import AuditLog, Company, Contact, Note, Task, User
from app.repositories import crm as crm_repository
from app.schemas.crm import (
    AuditLogRead,
    ChangePasswordRequest,
    CompanyCreate,
    CompanyRead,
    CompanyUpdate,
    ContactCreate,
    ContactDetailRead,
    ContactRead,
    ContactUpdate,
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
    UserCreate,
    UserPasswordUpdate,
    UserRead,
    UserUpdate,
)

router = APIRouter()
ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication required"},
    403: {"model": ErrorResponse, "description": "Not enough permissions"},
    404: {"model": ErrorResponse, "description": "Resource not found"},
    409: {"model": ErrorResponse, "description": "Conflict with an existing resource"},
}


def record_audit(
    session: Session,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    message: str | None = None,
) -> None:
    crm_repository.create_audit_log(
        session=session,
        actor_user_id=actor.id if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        message=message,
    )


@router.get("/health", response_model=HealthRead, tags=["system"])
def health(settings: Settings = Depends(get_settings)) -> HealthRead:
    return HealthRead(status="ok", app_name=settings.app_name, environment=settings.environment)

@router.post("/auth/login", response_model=TokenRead, responses=ERROR_RESPONSES, tags=["auth"])
def login(payload: LoginRequest, session: Session = Depends(get_session)) -> TokenRead:
    user = crm_repository.get_user_by_email(session, str(payload.email))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise unauthorized("Invalid email or password")
    record_audit(session, user, "login", "user", user.id)
    session.commit()
    return TokenRead(access_token=create_access_token(subject=user.id, role=user.role.value))


@router.post(
    "/auth/change-password",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
def change_password(
    payload: ChangePasswordRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MessageRead:
    if not verify_password(payload.current_password, current_user.password_hash):
        raise unauthorized("Invalid current password")
    current_user.password_hash = hash_password(payload.new_password)
    current_user.password_reset_token_hash = None
    current_user.password_reset_requested_at = None
    record_audit(session, current_user, "change_password", "user", current_user.id)
    session.commit()
    return MessageRead(message="Password changed")


@router.post(
    "/auth/password-reset/request",
    response_model=PasswordResetRequestRead,
    tags=["auth"],
)
def request_password_reset(
    payload: PasswordResetRequest, session: Session = Depends(get_session)
) -> PasswordResetRequestRead:
    user = crm_repository.get_user_by_email(session, str(payload.email))
    if not user or not user.is_active:
        return PasswordResetRequestRead(message="If the user exists, a reset token was generated")
    reset_token = create_reset_token()
    user.password_reset_token_hash = hash_reset_token(reset_token)
    user.password_reset_requested_at = datetime.now(UTC)
    record_audit(session, user, "request_password_reset", "user", user.id)
    session.commit()
    return PasswordResetRequestRead(
        message="Password reset token generated", reset_token=reset_token
    )


@router.post(
    "/auth/password-reset/confirm",
    response_model=MessageRead,
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
def confirm_password_reset(
    payload: PasswordResetConfirm, session: Session = Depends(get_session)
) -> MessageRead:
    token_hash = hash_reset_token(payload.token)
    user = crm_repository.get_user_by_reset_token_hash(session, token_hash)
    if not user or not user.is_active:
        raise unauthorized("Invalid reset token")
    user.password_hash = hash_password(payload.new_password)
    user.password_reset_token_hash = None
    user.password_reset_requested_at = None
    record_audit(session, user, "confirm_password_reset", "user", user.id)
    session.commit()
    return MessageRead(message="Password reset completed")


@router.get("/auth/me", response_model=UserRead, responses=ERROR_RESPONSES, tags=["auth"])
def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post(
    "/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    tags=["users"],
)
def create_user(
    payload: UserCreate,
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
    record_audit(session, current_user, "create_user", "user", user.id, user.email)
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
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> User:
    user = session.get(User, user_id)
    if not user:
        raise not_found("User")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "full_name" and value is not None:
            value = value.strip()
        setattr(user, field, value)
    record_audit(session, current_user, "update_user", "user", user.id, user.email)
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
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> MessageRead:
    user = session.get(User, user_id)
    if not user:
        raise not_found("User")
    user.password_hash = hash_password(payload.new_password)
    user.password_reset_token_hash = None
    user.password_reset_requested_at = None
    record_audit(session, current_user, "admin_update_password", "user", user.id, user.email)
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
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> User:
    user = session.get(User, user_id)
    if not user:
        raise not_found("User")
    user.is_active = False
    record_audit(session, current_user, "deactivate_user", "user", user.id, user.email)
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
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> User:
    user = session.get(User, user_id)
    if not user:
        raise not_found("User")
    user.is_active = True
    record_audit(session, current_user, "reactivate_user", "user", user.id, user.email)
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
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> list[AuditLog]:
    _ = current_user
    return crm_repository.list_audit_logs(session=session, skip=skip, limit=limit)


@router.get(
    "/audit-logs/export",
    responses=ERROR_RESPONSES,
    tags=["audit"],
)
def export_audit_logs(
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> Response:
    _ = current_user
    logs = crm_repository.list_all_audit_logs(session)
    rows = [
        {
            "id": log.id,
            "actor_user_id": log.actor_user_id or "",
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id or "",
            "message": log.message or "",
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
    header = ["id", "actor_user_id", "action", "entity_type", "entity_id", "message", "created_at"]
    csv_lines = [",".join(header)]
    for row in rows:
        csv_lines.append(",".join(str(row[key]).replace(",", " ") for key in header))
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
    record_audit(session, current_user, "create_company", "company", company.id, company.name)
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


@router.patch(
    "/companies/{company_id}",
    response_model=CompanyRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def update_company(
    company_id: str,
    payload: CompanyUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Company:
    company = crm_repository.get_company(session, company_id)
    if not company:
        raise not_found("Company")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, field, value)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict("A company with this tax_id already exists") from exc
    record_audit(session, current_user, "update_company", "company", company.id, company.name)
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
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Company:
    company = crm_repository.get_company(session, company_id)
    if not company:
        raise not_found("Company")
    company.is_active = False
    record_audit(session, current_user, "deactivate_company", "company", company.id, company.name)
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
    record_audit(session, current_user, "create_contact", "contact", contact.id, contact.email)
    session.commit()
    session.refresh(contact)
    return contact


@router.get("/contacts", response_model=list[ContactRead], responses=ERROR_RESPONSES, tags=["crm"])
def list_contacts(
    q: str | None = Query(default=None, description="Busca por nombre, apellidos o email"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[Contact]:
    _ = current_user
    return crm_repository.list_contacts(
        session=session, q=q, skip=skip, limit=limit, include_inactive=include_inactive
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
) -> Contact:
    _ = current_user
    contact = crm_repository.get_contact(session, contact_id)
    if not contact:
        raise not_found("Contact")
    return contact


@router.patch(
    "/contacts/{contact_id}",
    response_model=ContactRead,
    responses=ERROR_RESPONSES,
    tags=["crm"],
)
def update_contact(
    contact_id: str,
    payload: ContactUpdate,
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
    record_audit(session, current_user, "update_contact", "contact", contact.id, contact.email)
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
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Contact:
    contact = crm_repository.get_contact(session, contact_id)
    if not contact:
        raise not_found("Contact")
    contact.is_active = False
    record_audit(session, current_user, "deactivate_contact", "contact", contact.id, contact.email)
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
    record_audit(session, current_user, "create_note", "note", note.id, contact_id)
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
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Task:
    if not crm_repository.get_contact(session, contact_id):
        raise not_found("Contact")
    task = Task(contact_id=contact_id, **payload.model_dump())
    session.add(task)
    session.flush()
    record_audit(session, current_user, "create_task", "task", task.id, contact_id)
    session.commit()
    session.refresh(task)
    return task


router.include_router(integration_settings_router)
