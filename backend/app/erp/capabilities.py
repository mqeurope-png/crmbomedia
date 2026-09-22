"""BoHub ERP · roles y permisos — modelo de CAPACIDADES.

Un usuario tiene uno o varios roles (rol principal `User.role` + `User.erp_roles`
opcionales). Cada rol agrupa un conjunto de CAPACIDADES; el permiso efectivo del
usuario es la UNIÓN de las capacidades de todos sus roles. Es la ÚNICA fuente de
verdad de la autorización del ERP: los guards de los endpoints y el frontend
(vía `/api/auth/me`) preguntan por capacidad, no por rol.

Roles operativos del ERP:
  - `comercial`   — ventas: trabaja pedidos NO web de punta a punta, pero no
    cobra en FACTUSOL, no ve pedidos web, ni toca seguimiento/config/integ/
    conciliación. En la Cola SAT solo ve y sube etiqueta.
  - `pedidos`     — ERP Pedidos: todo en pedidos (web y no web) + Cola SAT
    completa + cobros FACTUSOL + seguimiento. No config/integ/conciliación.
  - `sat`         — taller: Cola SAT (preparación/embalado) + envío completo
    (etiqueta/tracking/nº serie/WhiteRIP) + enviar al SAT por email. No
    factura/cobra/crea pedidos; ve pedidos (incl. web) en solo lectura.
  - `admin`       — acceso total + asignar roles.

Reconciliación con lo anterior (documentado en el PR): antes solo había guards
por conjunto de roles (`require_erp_view/edit/admin`). Los roles legacy se mapean:
`manager` → como ERP Pedidos (pierde conciliación, que pasa a admin-only);
`user` → solo lectura del ERP (los comerciales deben pasar al nuevo rol
`comercial`); `viewer` → sin ERP.
"""
from __future__ import annotations

import json
from typing import Any


class Cap:
    """Capacidades del ERP (strings estables; se exponen al frontend)."""

    ACCESS = "erp.access"                    # entrar/ver el ERP
    ORDERS_VIEW_WEB = "erp.orders.view_web"  # ver/actuar sobre pedidos WEB
    ORDERS_CREATE = "erp.orders.create"      # crear pedido manual / desde FACTUSOL
    ORDERS_APPROVE = "erp.orders.approve"    # aprobar pedido
    ORDERS_CANCEL = "erp.orders.cancel"      # anular / restaurar
    SAMPLES_CREATE = "erp.samples.create"    # crear muestra / envío no facturable
    ALBARAN_CREATE = "erp.albaran.create"    # crear albarán en FACTUSOL
    INVOICE_EMIT = "erp.invoice.emit"        # emitir factura en FACTUSOL
    COBRO_REGISTER = "erp.cobro.register"    # registrar cobro en FACTUSOL
    EMAIL_SAT = "erp.email.sat"              # enviar el pedido al taller por email
    EMAIL_CLIENT = "erp.email.client"        # enviar factura/pedido al cliente
    PROFORMAS = "erp.proformas"              # proformas: crear/duplicar/convertir
    DOCUMENTS = "erp.documents"              # documentos FACTUSOL: ver/crear/vincular
    COMPANIES = "erp.companies"              # empresas: crear/vincular/editar/traer
    SAT_VIEW = "erp.sat.view"                # ver la Cola SAT
    SAT_PREPARE = "erp.sat.prepare"          # preparación (empezar/embalar/recoger)
    SAT_SHIPPING = "erp.sat.shipping"        # envío: crear / subir etiqueta
    SAT_TRACKING = "erp.sat.tracking"        # tracking / nº serie / WhiteRIP
    SAT_NO_SHIPPING = "erp.sat.no_shipping"  # «No requiere envío» (masivo)
    SEGUIMIENTO = "erp.seguimiento"          # hoja de seguimiento (ver/editar/Drive)
    CONCILIACION = "erp.conciliacion"        # conciliación bancaria
    CONFIG = "erp.config"                    # configuración ERP
    INTEGRACIONES = "erp.integraciones"      # integraciones (Woo, etc.)
    ROLES_ASSIGN = "admin.roles"             # asignar roles a usuarios


#: Todas las capacidades (lo que tiene el admin).
ALL_CAPS: frozenset[str] = frozenset(
    v for k, v in vars(Cap).items() if not k.startswith("_") and isinstance(v, str)
)

# --- conjuntos por rol -------------------------------------------------------

_COMERCIAL_CAPS: frozenset[str] = frozenset({
    Cap.ACCESS, Cap.ORDERS_CREATE, Cap.ORDERS_APPROVE, Cap.ORDERS_CANCEL,
    Cap.ALBARAN_CREATE, Cap.INVOICE_EMIT, Cap.EMAIL_SAT, Cap.EMAIL_CLIENT,
    Cap.PROFORMAS, Cap.DOCUMENTS, Cap.COMPANIES, Cap.SAT_VIEW, Cap.SAT_SHIPPING,
    Cap.SAMPLES_CREATE,
})
#: ERP Pedidos = comercial + web + cobro + Cola SAT completa + seguimiento.
_PEDIDOS_CAPS: frozenset[str] = _COMERCIAL_CAPS | frozenset({
    Cap.ORDERS_VIEW_WEB, Cap.COBRO_REGISTER, Cap.SAT_PREPARE, Cap.SAT_TRACKING,
    Cap.SAT_NO_SHIPPING, Cap.SEGUIMIENTO,
})
#: ERP SAT = taller + envío/técnicos + enviar al SAT + ver pedidos (incl. web).
#: El taller también DA DE ALTA muestras (además de prepararlas y enviarlas):
#: una muestra no es facturable, así que crearla no requiere ser «oficina».
_SAT_CAPS: frozenset[str] = frozenset({
    Cap.ACCESS, Cap.ORDERS_VIEW_WEB, Cap.SAT_VIEW, Cap.SAT_PREPARE,
    Cap.SAT_SHIPPING, Cap.SAT_TRACKING, Cap.SAT_NO_SHIPPING, Cap.EMAIL_SAT,
    Cap.SAMPLES_CREATE,
})
#: Legacy `user`: solo lectura del ERP (sin edición). Reasignar a `comercial`.
_USER_CAPS: frozenset[str] = frozenset({
    Cap.ACCESS, Cap.ORDERS_VIEW_WEB, Cap.SAT_VIEW,
})

#: Rol → capacidades. `admin` va aparte (ALL_CAPS).
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "comercial": _COMERCIAL_CAPS,
    "pedidos": _PEDIDOS_CAPS,
    "sat": _SAT_CAPS,
    # Legacy — reconciliación:
    "manager": _USER_CAPS,      # como antes: ve el ERP, no edita (CRM-focused)
    "user": _USER_CAPS,         # solo lectura del ERP
    "viewer": frozenset(),      # sin ERP
}

#: Roles OPERATIVOS del ERP que se pueden asignar como rol adicional (multi-rol)
#: y que se ofrecen en /admin/users.
ASSIGNABLE_ERP_ROLES: tuple[str, ...] = ("comercial", "pedidos", "sat")


def _role_value(role: Any) -> str:
    return str(getattr(role, "value", role) or "").strip()


def parse_erp_roles(raw: Any) -> list[str]:
    """Lista de roles operativos válidos guardada en `User.erp_roles` (JSON)."""
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        val = _role_value(item)
        if val in ASSIGNABLE_ERP_ROLES and val not in out:
            out.append(val)
    return out


def effective_roles(user: Any) -> set[str]:
    """Roles efectivos del usuario: el principal (`role`) + los operativos
    adicionales (`erp_roles`)."""
    roles = {_role_value(getattr(user, "role", ""))}
    roles |= set(parse_erp_roles(getattr(user, "erp_roles", None)))
    return {r for r in roles if r}


def capabilities_for(user: Any) -> frozenset[str]:
    """Capacidades EFECTIVAS del usuario (unión de las de todos sus roles).
    `admin` tiene todas."""
    roles = effective_roles(user)
    if "admin" in roles:
        return ALL_CAPS
    caps: set[str] = set()
    for role in roles:
        caps |= ROLE_CAPABILITIES.get(role, frozenset())
    return frozenset(caps)


def has_capability(user: Any, cap: str) -> bool:
    """¿El usuario tiene la capacidad `cap`?"""
    roles = effective_roles(user)
    if "admin" in roles:
        return True
    return any(cap in ROLE_CAPABILITIES.get(role, frozenset()) for role in roles)


def can_view_web_orders(user: Any) -> bool:
    """¿Puede ver pedidos de origen web? (Los que no, no ven ni tocan web.)"""
    return has_capability(user, Cap.ORDERS_VIEW_WEB)
