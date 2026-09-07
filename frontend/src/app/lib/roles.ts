import type { Role } from "./api";
import { isErpOnlyRole } from "./appMode";

/** ERP-F2-fix1 — etiquetas legibles de cada rol (el valor interno no cambia).
 *  Los roles de ERP se marcan como tales para que el admin no los confunda con
 *  un escalón más de la escalera del CRM. */
export const ROLE_LABELS: Record<Role, string> = {
  admin: "Administrador",
  manager: "Responsable",
  user: "Usuario",
  viewer: "Solo lectura",
  pedidos: "ERP · Pedidos",
  sat: "ERP · Taller (SAT)",
};

/** Roles agrupados por ÁMBITO para el desplegable (CRM arriba, ERP abajo). */
export const CRM_ROLES: ReadonlyArray<Role> = ["admin", "manager", "user", "viewer"];
export const ERP_ROLES: ReadonlyArray<Role> = ["pedidos", "sat"];

export type RoleScope = "crm" | "erp";

export function roleScope(role: Role): RoleScope {
  return isErpOnlyRole(role) ? "erp" : "crm";
}

export function roleLabel(role: Role): string {
  return ROLE_LABELS[role] ?? role;
}

/** Nota breve del ámbito del rol elegido, para el selector. */
export function roleScopeNote(role: Role): string {
  return roleScope(role) === "erp"
    ? "Solo verá el ERP (pedidos, documentos, taller). No tendrá acceso a "
      + "contactos, emails ni marketing."
    : "Verá el CRM (contactos, emails, marketing…) y las secciones de ERP que "
      + "su rol permita.";
}

/** Aviso cuando el rol pasa de un ámbito a otro (cambio con consecuencias). */
export function scopeChangeWarning(from: Role, to: Role): string | null {
  if (roleScope(from) === roleScope(to)) return null;
  return roleScope(to) === "erp"
    ? "Cambio de ámbito CRM → ERP: este usuario dejará de ver el CRM y solo "
      + "verá el ERP."
    : "Cambio de ámbito ERP → CRM: este usuario dejará de estar limitado al "
      + "ERP y pasará a ver el CRM.";
}
