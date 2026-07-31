"""BoHub ERP — dependencias de autorización (Fase A PR 3).

El ERP NO usa la escalera lineal del CRM (viewer<user<manager<admin):
PEDIDOS y SAT son roles operativos con permisos por CONJUNTO. Matriz
cerrada con Bart:

  - VER (bandejas, ficha, timeline): admin, manager, pedidos, sat, user
    (los comerciales ven pero no tocan). VIEWER no accede al ERP.
  - CREAR/EDITAR pedidos: admin, pedidos.
  - APROBAR (Cola PEDIDOS): admin, pedidos.
  - Transiciones de estado: gate de VER aquí; la matriz fina por arco la
    aplica el engine (TransitionError role_forbidden → 403).
"""
from __future__ import annotations

from fastapi import Depends

from app.core.auth import get_current_user
from app.core.errors import forbidden
from app.models.crm import User, UserRole

ERP_VIEW_ROLES = frozenset({
    UserRole.ADMIN, UserRole.MANAGER, UserRole.PEDIDOS, UserRole.SAT,
    UserRole.USER,
})
ERP_EDIT_ROLES = frozenset({UserRole.ADMIN, UserRole.PEDIDOS})
ERP_APPROVE_ROLES = frozenset({UserRole.ADMIN, UserRole.PEDIDOS})


def _require(roles: frozenset[UserRole]):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise forbidden()
        return current_user

    return dependency


require_erp_view = _require(ERP_VIEW_ROLES)
require_erp_edit = _require(ERP_EDIT_ROLES)
require_erp_approve = _require(ERP_APPROVE_ROLES)
