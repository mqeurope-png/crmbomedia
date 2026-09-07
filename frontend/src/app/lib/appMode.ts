import type { Role } from "./api";

/** ERP-F2 — «modo» de la aplicación. Misma app por debajo; el modo decide qué
 *  ve el usuario: el CRM de siempre o un ERP sin rastro del CRM. */
export type AppMode = "crm" | "erp";

/** Roles OPERATIVOS de solo-ERP (pedidos/taller): entran a un ERP, no al CRM.
 *  Espejo de `ERP_ONLY_ROLES` del backend (app/core/auth.py). */
export const ERP_ONLY_ROLES: ReadonlyArray<Role> = ["pedidos", "sat"];

export function isErpOnlyRole(role: Role): boolean {
  return (ERP_ONLY_ROLES as readonly string[]).includes(role);
}

/** El modo al que un rol está ATADO, o `null` si puede elegir (solo el admin).
 *  - pedidos/sat → siempre ERP.
 *  - admin       → elige (null).
 *  - comercial (manager/user/viewer) → CRM (ven el ERP permitido, como hoy). */
export function forcedMode(role: Role): AppMode | null {
  if (isErpOnlyRole(role)) return "erp";
  if (role === "admin") return null;
  return "crm";
}

/** Solo el admin tiene conmutador de modo. */
export function canSwitchMode(role: Role): boolean {
  return forcedMode(role) === null;
}

const storageKey = (userId: string) => `crmbo:appmode:${userId}`;

/** Preferencia de modo del admin, recordada entre sesiones POR USUARIO en este
 *  navegador (es una preferencia de vista, no dato de cuenta: se guarda en
 *  localStorage para no necesitar migración ni endpoint). */
export function readStoredMode(userId: string): AppMode | null {
  try {
    const value = window.localStorage.getItem(storageKey(userId));
    return value === "erp" || value === "crm" ? value : null;
  } catch {
    return null;
  }
}

export function storeMode(userId: string, mode: AppMode): void {
  try {
    window.localStorage.setItem(storageKey(userId), mode);
  } catch {
    // localStorage no disponible (modo privado): el modo se recalcula del rol.
  }
}

/** Modo efectivo para un usuario: el forzado por su rol y, si puede elegir
 *  (admin), su preferencia guardada; por defecto CRM. */
export function resolveMode(user: { id: string; role: Role }): AppMode {
  const forced = forcedMode(user.role);
  if (forced) return forced;
  return readStoredMode(user.id) ?? "crm";
}

/** Rutas que un usuario en modo ERP SÍ puede visitar: el propio ERP, su cuenta
 *  y las rutas anónimas. Cualquier otra (el CRM) lo devuelve a `/erp`. */
const ERP_ALLOWED_PREFIXES = ["/erp", "/account", "/login", "/welcome"];

export function pathAllowedInErpMode(pathname: string): boolean {
  return ERP_ALLOWED_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
}

/** A dónde aterriza cada modo al entrar / al cambiar. */
export function homeForMode(mode: AppMode): string {
  return mode === "erp" ? "/erp" : "/";
}
