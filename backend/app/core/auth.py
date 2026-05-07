from collections.abc import Callable

from fastapi import Depends
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


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> User:
    if credentials is None:
        raise unauthorized()
    payload = decode_access_token(credentials.credentials)
    if not payload or not payload.get("sub"):
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
require_admin = require_role(UserRole.ADMIN)
