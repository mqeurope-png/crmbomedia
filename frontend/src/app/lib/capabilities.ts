import type { Role, User } from "./api";

/** BoHub ERP · roles y permisos — modelo de CAPACIDADES (espejo del backend
 *  `app/erp/capabilities.py`). El frontend gatea qué acciones/menús muestra por
 *  CAPACIDAD, no por rol: el permiso efectivo es la UNIÓN de las capacidades de
 *  todos los roles del usuario (rol principal `role` + `erp_roles`).
 *
 *  `/api/auth/me` ya trae `capabilities` calculado por el backend; cuando está,
 *  se usa tal cual. Para objetos `User` que NO lo traen (p.ej. la lista de
 *  usuarios) se deriva de los roles con el mismo mapa que el backend. En ambos
 *  casos el backend es la fuente de verdad: esto solo esconde/deshabilita en la
 *  UI y cada endpoint vuelve a comprobar la capacidad (403 si falta). */
export const Cap = {
  ACCESS: "erp.access",
  ORDERS_VIEW_WEB: "erp.orders.view_web",
  ORDERS_CREATE: "erp.orders.create",
  ORDERS_APPROVE: "erp.orders.approve",
  ORDERS_CANCEL: "erp.orders.cancel",
  SAMPLES_CREATE: "erp.samples.create",
  ALBARAN_CREATE: "erp.albaran.create",
  INVOICE_EMIT: "erp.invoice.emit",
  COBRO_REGISTER: "erp.cobro.register",
  EMAIL_SAT: "erp.email.sat",
  EMAIL_CLIENT: "erp.email.client",
  PROFORMAS: "erp.proformas",
  DOCUMENTS: "erp.documents",
  COMPANIES: "erp.companies",
  SAT_VIEW: "erp.sat.view",
  SAT_PREPARE: "erp.sat.prepare",
  SAT_SHIPPING: "erp.sat.shipping",
  SAT_TRACKING: "erp.sat.tracking",
  SAT_NO_SHIPPING: "erp.sat.no_shipping",
  SEGUIMIENTO: "erp.seguimiento",
  CONCILIACION: "erp.conciliacion",
  CONFIG: "erp.config",
  INTEGRACIONES: "erp.integraciones",
  ROLES_ASSIGN: "admin.roles",
} as const;

export type Capability = (typeof Cap)[keyof typeof Cap];

/** Todas las capacidades (lo que tiene el admin). */
const ALL_CAPS: ReadonlyArray<string> = Object.values(Cap);

// --- conjuntos por rol (espejo de ROLE_CAPABILITIES del backend) -------------

const COMERCIAL_CAPS: ReadonlyArray<string> = [
  Cap.ACCESS, Cap.ORDERS_CREATE, Cap.ORDERS_APPROVE, Cap.ORDERS_CANCEL,
  Cap.ALBARAN_CREATE, Cap.INVOICE_EMIT, Cap.EMAIL_SAT, Cap.EMAIL_CLIENT,
  Cap.PROFORMAS, Cap.DOCUMENTS, Cap.COMPANIES, Cap.SAT_VIEW, Cap.SAT_SHIPPING,
  Cap.SAMPLES_CREATE,
];
const PEDIDOS_CAPS: ReadonlyArray<string> = [
  ...COMERCIAL_CAPS,
  Cap.ORDERS_VIEW_WEB, Cap.COBRO_REGISTER, Cap.SAT_PREPARE, Cap.SAT_TRACKING,
  Cap.SAT_NO_SHIPPING, Cap.SEGUIMIENTO,
];
const SAT_CAPS: ReadonlyArray<string> = [
  Cap.ACCESS, Cap.ORDERS_VIEW_WEB, Cap.SAT_VIEW, Cap.SAT_PREPARE,
  Cap.SAT_SHIPPING, Cap.SAT_TRACKING, Cap.SAT_NO_SHIPPING, Cap.EMAIL_SAT,
  // El taller también DA DE ALTA muestras, no solo las prepara.
  Cap.SAMPLES_CREATE,
];
/** Legacy `manager`/`user`: solo lectura del ERP (reasignar a `comercial`). */
const USER_CAPS: ReadonlyArray<string> = [
  Cap.ACCESS, Cap.ORDERS_VIEW_WEB, Cap.SAT_VIEW,
];

const ROLE_CAPABILITIES: Record<Role, ReadonlyArray<string>> = {
  admin: ALL_CAPS,
  comercial: COMERCIAL_CAPS,
  pedidos: PEDIDOS_CAPS,
  sat: SAT_CAPS,
  manager: USER_CAPS,
  user: USER_CAPS,
  viewer: [],
};

/** Roles operativos del ERP que se pueden asignar como rol adicional (multi-rol)
 *  en /admin/users. Espejo de `ASSIGNABLE_ERP_ROLES` del backend. */
export const ASSIGNABLE_ERP_ROLES: ReadonlyArray<Role> = ["comercial", "pedidos", "sat"];

type CapUser = Pick<User, "role"> & {
  capabilities?: string[] | null;
  erp_roles?: string[] | null;
};

/** Capacidades EFECTIVAS del usuario. Prefiere el array `capabilities` de
 *  `/api/auth/me`; si no viene, lo deriva de los roles (principal + `erp_roles`)
 *  con el mismo mapa que el backend. */
export function effectiveCapabilities(
  user: CapUser | null | undefined,
): ReadonlyArray<string> {
  if (!user) return [];
  // `/api/auth/me` trae el array ya calculado (incluido `[]` para viewer): se
  // usa tal cual. Un `User` sin él (lista de usuarios) se deriva de los roles.
  if (Array.isArray(user.capabilities)) return user.capabilities;
  const roles = new Set<Role>([user.role]);
  for (const extra of user.erp_roles ?? []) {
    if ((ASSIGNABLE_ERP_ROLES as readonly string[]).includes(extra)) {
      roles.add(extra as Role);
    }
  }
  if (roles.has("admin")) return ALL_CAPS;
  const caps = new Set<string>();
  for (const role of roles) {
    for (const cap of ROLE_CAPABILITIES[role] ?? []) caps.add(cap);
  }
  return [...caps];
}

/** ¿El usuario tiene la capacidad `cap`? (unión de las de todos sus roles). */
export function can(
  user: CapUser | null | undefined,
  cap: Capability | string,
): boolean {
  if (!user) return false;
  if (user.role === "admin") return true;
  return effectiveCapabilities(user).includes(cap);
}

/** ¿Puede ver/actuar sobre pedidos de origen web (WooCommerce)? El Comercial no:
 *  los pedidos web quedan fuera de sus listas y su ficha le devuelve 403. */
export function canViewWebOrders(user: CapUser | null | undefined): boolean {
  return can(user, Cap.ORDERS_VIEW_WEB);
}
