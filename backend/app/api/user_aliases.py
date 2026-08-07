"""CRM-GMAIL Parte A — CRUD de alias de correo entrante por usuario.

Registro de PROPIEDAD del correo entrante: cada `alias_email` pertenece a un
solo usuario (unique global). Distinto de las preferencias Send-As
(`/api/emails/aliases`), que son outbound y no únicas.

Reglas de acceso:
  - GET  /api/users/{id}/aliases          — admin cualquiera; no-admin, solo el suyo.
  - POST /api/users/{id}/aliases          — admin.
  - PATCH /api/users/{id}/aliases/{aid}   — admin (toggle active).
  - DELETE /api/users/{id}/aliases/{aid}  — admin.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import get_current_user, require_admin
from app.core.errors import conflict, forbidden, not_found
from app.db.session import get_session
from app.models.crm import User, UserEmailAlias, UserRole
from app.schemas.user_aliases import (
    UserEmailAliasCreate,
    UserEmailAliasRead,
    UserEmailAliasUpdate,
)

router = APIRouter(prefix="/api/users", tags=["users"])
logger = logging.getLogger(__name__)


def _load_user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise not_found("User")
    return user


def _load_alias(
    session: Session, user_id: str, alias_id: str
) -> UserEmailAlias:
    alias = session.get(UserEmailAlias, alias_id)
    if alias is None or alias.user_id != user_id:
        raise not_found("Alias")
    return alias


@router.get("/{user_id}/aliases", response_model=list[UserEmailAliasRead])
def list_user_aliases(
    user_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[UserEmailAlias]:
    # Admin ve el de cualquiera; el resto solo el suyo.
    if (
        current_user.role != UserRole.ADMIN
        and current_user.id != user_id
    ):
        raise forbidden()
    _load_user(session, user_id)
    return list(
        session.scalars(
            select(UserEmailAlias)
            .where(UserEmailAlias.user_id == user_id)
            .order_by(UserEmailAlias.alias_email.asc())
        )
    )


@router.post(
    "/{user_id}/aliases",
    response_model=UserEmailAliasRead,
    status_code=201,
)
def create_user_alias(
    user_id: str,
    payload: UserEmailAliasCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> UserEmailAlias:
    _load_user(session, user_id)
    alias_email = str(payload.alias_email).strip().lower()
    # Unique global: un alias pertenece a un solo usuario. Damos un 409
    # legible en vez del IntegrityError del índice único.
    existing = session.scalar(
        select(UserEmailAlias).where(
            UserEmailAlias.alias_email == alias_email
        )
    )
    if existing is not None:
        raise conflict(
            "Ese alias ya está asignado a un usuario. Un alias solo puede "
            "pertenecer a uno (índice único global sobre alias_email)."
        )
    alias = UserEmailAlias(
        user_id=user_id, alias_email=alias_email, active=True
    )
    session.add(alias)
    session.flush()
    record_event(
        session,
        action=Action.USER_UPDATED,
        target_type="user_email_alias",
        target_id=alias.id,
        actor=current_user,
        metadata={"target_user_id": user_id, "alias_email": alias_email},
        request=request,
    )
    session.commit()
    session.refresh(alias)
    return alias


@router.patch(
    "/{user_id}/aliases/{alias_id}",
    response_model=UserEmailAliasRead,
)
def update_user_alias(
    user_id: str,
    alias_id: str,
    payload: UserEmailAliasUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> UserEmailAlias:
    alias = _load_alias(session, user_id, alias_id)
    alias.active = payload.active
    record_event(
        session,
        action=Action.USER_UPDATED,
        target_type="user_email_alias",
        target_id=alias.id,
        actor=current_user,
        metadata={
            "target_user_id": user_id,
            "alias_email": alias.alias_email,
            "active": payload.active,
        },
        request=request,
    )
    session.commit()
    session.refresh(alias)
    return alias


@router.delete(
    "/{user_id}/aliases/{alias_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_user_alias(
    user_id: str,
    alias_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> Response:
    alias = _load_alias(session, user_id, alias_id)
    record_event(
        session,
        action=Action.USER_UPDATED,
        target_type="user_email_alias",
        target_id=alias.id,
        actor=current_user,
        metadata={"target_user_id": user_id, "alias_email": alias.alias_email},
        request=request,
    )
    session.delete(alias)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
