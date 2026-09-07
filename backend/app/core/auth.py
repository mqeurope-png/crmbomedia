from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, Request, status
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
    # BoHub ERP Fase A. Roles OPERATIVOS fuera de la escalera lineal del
    # CRM: nivel bajo aquí (SAT=viewer, PEDIDOS=user) para que no hereden
    # endpoints de manager/admin; el acceso ERP real se controla con
    # dependencias por conjunto de roles en app/erp/api/deps.py. Sin
    # nivel aquí, require_role lanzaría KeyError (500) al primer login.
    UserRole.SAT: 0,
    UserRole.PEDIDOS: 1,
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


def _audit_forbidden(
    request: Request,
    session: Session,
    current_user: User,
    minimum_role: UserRole,
) -> None:
    """Persist a record of an attempted access by a user with insufficient role.

    Imported lazily to dodge the circular `app.core.audit` ↔ `app.core.auth`
    dependency that would otherwise show up at module load.
    """
    from app.core.audit import Action, record_event

    record_event(
        session,
        action=Action.ACCESS_FORBIDDEN,
        target_type="endpoint",
        target_id=request.url.path,
        actor=current_user,
        metadata={
            "method": request.method,
            "path": request.url.path,
            "required_role": minimum_role.value,
            "actual_role": current_user.role.value,
        },
        request=request,
    )
    session.commit()


def require_role(minimum_role: UserRole) -> Callable[..., User]:
    def dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> User:
        if ROLE_LEVELS[current_user.role] < ROLE_LEVELS[minimum_role]:
            _audit_forbidden(request, session, current_user, minimum_role)
            raise forbidden()
        return current_user

    return dependency


require_viewer = require_role(UserRole.VIEWER)
require_user = require_role(UserRole.USER)
require_manager = require_role(UserRole.MANAGER)


# ERP-F2 — roles OPERATIVOS de solo-ERP (pedidos/taller). No son usuarios del
# CRM: trabajan en el ERP y no deben ver ni tocar el CRM. La escalera lineal
# (`ROLE_LEVELS`) los coloca bajo (SAT=0, PEDIDOS=1), así que HOY pasarían los
# guards `require_viewer`/`require_user` de los endpoints del CRM — de ahí la
# necesidad de un guard de ÁMBITO explícito.
ERP_ONLY_ROLES = frozenset({UserRole.PEDIDOS, UserRole.SAT})


def is_erp_only_role(role: UserRole | str) -> bool:
    """True si el rol es de solo-ERP (pedidos/taller)."""
    value = role.value if isinstance(role, UserRole) else str(role)
    return value in {r.value for r in ERP_ONLY_ROLES}


def require_crm_access(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> User:
    """Ámbito CRM: cualquier rol del CRM (viewer..admin) pasa; un rol solo-ERP
    (PEDIDOS/SAT) recibe 403. El ERP es su aplicación — no ve la gestión del
    CRM (contactos, emails, marketing, pipelines, segmentos, tags, workflows,
    plantillas). El dato de CLIENTE que el ERP necesita (empresas) NO va por
    aquí. Es un guard de ámbito; el nivel fino lo siguen aplicando
    require_viewer/user/manager en cada endpoint."""
    if is_erp_only_role(current_user.role):
        _audit_forbidden(request, session, current_user, UserRole.USER)
        raise forbidden()
    return current_user


# ERP-F2 — el router MONOLÍTICO (`app/api/routes.py`, montado en /api) mezcla
# features del CRM (contactos, pipelines, segmentos, tags, vistas, marketing…)
# con auth, 2FA, cuenta propia y el shadow de empresas. No se le puede poner
# `require_crm_access` a nivel de router sin romper login/cuenta. Este guard se
# aplica UNA vez al include del monolito y hace DENY-BY-DEFAULT para el perfil
# solo-ERP: bloquea todo /api salvo lo que ese perfil SÍ necesita (auth/cuenta
# propia y el dato de CLIENTE: empresas). Un endpoint CRM nuevo queda bloqueado
# por defecto (fallo seguro); como mucho un self-endpoint no listado saldría
# bloqueado de más (molesto, no un agujero).
_MONOLITH_ERP_EXEMPT_PREFIXES = (
    "/api/auth",        # login, /me, 2FA, logout, reset de contraseña
    "/api/companies",   # dato de CLIENTE (shadow legacy; el router v2 va aparte)
    "/api/users/me",    # preferencias de la propia cuenta
)


def _monolith_path_exempt(path: str) -> bool:
    return any(
        path == p or path.startswith(p + "/")
        for p in _MONOLITH_ERP_EXEMPT_PREFIXES
    )


def crm_scope_monolith(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> None:
    """Ámbito CRM para el router monolítico (deny-by-default para solo-ERP).

    No exige autenticación (el monolito tiene rutas públicas como el login):
    usa el bearer OPCIONAL, y si no hay token deja que el guard propio del
    endpoint responda 401. El rol se comprueba contra la BD (autoritativo, no
    el claim del token que podría estar desactualizado tras un cambio de rol)."""
    if _monolith_path_exempt(request.url.path):
        return
    if credentials is None:
        return
    payload = decode_access_token(credentials.credentials)
    if not payload or not payload.get("sub"):
        return
    user = session.get(User, payload["sub"])
    if user is not None and is_erp_only_role(user.role):
        _audit_forbidden(request, session, user, UserRole.USER)
        raise forbidden()


def require_admin(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> User:
    """Admin role required.

    Note: 2FA is fully optional for every role, admin included. Sensitive
    admin endpoints used to refuse JWTs marked `limited` (issued to admins
    who logged in without 2FA), but the policy is no longer enforced; the
    claim is no longer set at login time and any leftover `limited` tokens
    are accepted normally until they expire.
    """
    if ROLE_LEVELS[current_user.role] < ROLE_LEVELS[UserRole.ADMIN]:
        _audit_forbidden(request, session, current_user, UserRole.ADMIN)
        raise forbidden()
    return current_user


# CRM-PERFIL — mensaje de error consistente para las acciones de perfil que el
# comercial ya NO puede hacer (contraseña, alias Gmail, calendario, prefs). El
# frontend (`formatFastApiDetail`) desempaqueta el `detail` anidado y lo muestra
# en rojo. No reutilizamos `require_admin` para no cambiar el 403 genérico del
# resto de endpoints admin (evita romper sus tests/contrato).
_REQUIRES_ADMIN_DETAIL = (
    "Este cambio solo puede hacerlo un administrador. "
    "Contacta con soporte interno."
)


def require_admin_action(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> User:
    """Como `require_admin` pero devuelve `{code: requires_admin, detail}`."""
    if ROLE_LEVELS[current_user.role] < ROLE_LEVELS[UserRole.ADMIN]:
        _audit_forbidden(request, session, current_user, UserRole.ADMIN)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "requires_admin", "detail": _REQUIRES_ADMIN_DETAIL},
        )
    return current_user
