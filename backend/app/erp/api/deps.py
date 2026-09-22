"""BoHub ERP — dependencias de autorización por CAPACIDAD.

La autorización del ERP se hace por CAPACIDAD (ver `app/erp/capabilities.py`),
no por rol suelto: cada endpoint exige la capacidad que le corresponde en la
matriz, y el permiso efectivo del usuario es la unión de las capacidades de
todos sus roles (rol principal + `erp_roles`).

`require_erp_view` = «puede ver el ERP» (capacidad `erp.access`). Los guards con
nombre (`require_orders_create`, `require_cobro_register`, …) exigen su capacidad.
Se conservan `require_erp_edit`/`require_erp_approve` (conjunto de roles antiguo)
solo como respaldo seguro para endpoints aún no migrados: nunca amplían permisos.
"""
from __future__ import annotations

from fastapi import Depends

from app.core.auth import get_current_user
from app.core.errors import forbidden
from app.erp.capabilities import Cap, has_capability
from app.models.crm import User, UserRole

# --- conjuntos de roles legacy (respaldo; ya no son la fuente de verdad) ------
ERP_VIEW_ROLES = frozenset({
    UserRole.ADMIN, UserRole.MANAGER, UserRole.PEDIDOS, UserRole.SAT,
    UserRole.USER, UserRole.COMERCIAL,
})
ERP_EDIT_ROLES = frozenset({UserRole.ADMIN, UserRole.PEDIDOS})
ERP_APPROVE_ROLES = frozenset({UserRole.ADMIN, UserRole.PEDIDOS})
ERP_ADMIN_ROLES = frozenset({UserRole.ADMIN})


def _require(roles: frozenset[UserRole]):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise forbidden()
        return current_user

    return dependency


def require_capability(cap: str):
    """Guard por CAPACIDAD: 403 si el usuario no la tiene (unión de sus roles)."""

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if not has_capability(current_user, cap):
            raise forbidden()
        return current_user

    return dependency


# --- guards por capacidad (los que usan los endpoints) ------------------------
require_erp_view = require_capability(Cap.ACCESS)          # ver el ERP
require_orders_create = require_capability(Cap.ORDERS_CREATE)
require_orders_approve = require_capability(Cap.ORDERS_APPROVE)
require_orders_cancel = require_capability(Cap.ORDERS_CANCEL)
require_albaran_create = require_capability(Cap.ALBARAN_CREATE)
require_invoice_emit = require_capability(Cap.INVOICE_EMIT)
require_cobro_register = require_capability(Cap.COBRO_REGISTER)
require_email_sat = require_capability(Cap.EMAIL_SAT)
require_email_client = require_capability(Cap.EMAIL_CLIENT)
require_proformas = require_capability(Cap.PROFORMAS)
require_documents = require_capability(Cap.DOCUMENTS)
require_companies = require_capability(Cap.COMPANIES)
require_sat_view = require_capability(Cap.SAT_VIEW)
require_sat_prepare = require_capability(Cap.SAT_PREPARE)
require_sat_shipping = require_capability(Cap.SAT_SHIPPING)
require_sat_tracking = require_capability(Cap.SAT_TRACKING)
require_sat_no_shipping = require_capability(Cap.SAT_NO_SHIPPING)
require_seguimiento = require_capability(Cap.SEGUIMIENTO)
require_conciliacion = require_capability(Cap.CONCILIACION)
require_config = require_capability(Cap.CONFIG)
require_integraciones = require_capability(Cap.INTEGRACIONES)

# --- legacy (respaldo): «edición de oficina» = quien puede crear pedidos
# (admin, pedidos, comercial), seguro para multi-rol. Los endpoints sensibles
# (cobro, seguimiento, SAT, conciliación, config, integraciones) usan su guard
# específico y NO este. ---
require_erp_edit = require_capability(Cap.ORDERS_CREATE)
require_erp_approve = require_capability(Cap.ORDERS_APPROVE)
require_erp_admin = _require(ERP_ADMIN_ROLES)
