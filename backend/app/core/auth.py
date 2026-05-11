from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import forbidden, unauthorized
from app.core.security import decode_access_token
from app.db.session import get_session
from app.models.crm import User, UserRole

bearer_scheme = HTTPBearer(auto_error=False)

ROLE_LEVELS = {
    UserRole.VIEWER: 0,
    UserRole.USER: 1,
    UserRole.MANAGER: 2,
    UserRole.ADMIN: 3,
}


def get_token_payload(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    """Decode and validate the JWT signature/exp; return the raw payload.

    Used as a building block by `get_current_user` and `get_pre_2fa_user`,
    which inspect the `pre_2fa` claim to decide whether the request can
    proceed.
    """
    if credentials is None:
        raise unauthorized()
    payload = decode_access_token(credentials.credentials)
    if not payload or not payload.get("sub"):
        raise unauthorized()
    return payload


def get_current_user(
    payload: dict[str, Any] = Depends(get_token_payload),
    session: Session = Depends(get_session),
) -> User:
    """Resolve the authenticated user. Rejects pre-2FA tokens outright."""
    if payload.get("pre_2fa"):
        # The token is only good for /api/auth/2fa/verify; treat any other
        # request as unauthenticated. The body hints at the next step.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Complete 2FA verification first",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = session.get(User, payload["sub"])
    if not user or not user.is_active:
        raise unauthorized()
    return user


def get_pre_2fa_user(
    payload: dict[str, Any] = Depends(get_token_payload),
    session: Session = Depends(get_session),
) -> User:
    """Accept ONLY pre-2FA tokens; used by /api/auth/2fa/verify."""
    if not payload.get("pre_2fa"):
        raise unauthorized()
    user = session.get(User, payload["sub"])
    if not user or not user.is_active:
        raise unauthorized()
    return user


def require_role(minimum_role: UserRole) -> Callable[[User], User]:
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if ROLE_LEVELS[current_user.role] < ROLE_LEVELS[minimum_role]:
            raise forbidden()
        return current_user

    return dependency


require_viewer = require_role(UserRole.VIEWER)
require_user = require_role(UserRole.USER)
require_manager = require_role(UserRole.MANAGER)


def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Admin role required.

    Note: 2FA is fully optional for every role, admin included. Sensitive
    admin endpoints used to refuse JWTs marked `limited` (issued to admins
    who logged in without 2FA), but the policy is no longer enforced; the
    claim is no longer set at login time and any leftover `limited` tokens
    are accepted normally until they expire.
    """
    if ROLE_LEVELS[current_user.role] < ROLE_LEVELS[UserRole.ADMIN]:
        raise forbidden()
    return current_user
