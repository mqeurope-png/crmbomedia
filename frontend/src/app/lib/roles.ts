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
  comercial: "ERP · Comercial",
};

/** Roles agrupados por ÁMBITO para el desplegable (CRM arriba, ERP abajo). */
export const CRM_ROLES: ReadonlyArray<Role> = ["admin", "manager", "user", "viewer"];
export const ERP_ROLES: ReadonlyArray<Role> = ["comercial", "pedidos", "sat"];

export type RoleScope = "crm" | "erp";

export function roleScope(role: Role): RoleScope {
  return isErpOnlyRole(role) ? "erp" : "crm";
}

export function roleLabel(role: Role): string {
  return ROLE_LABELS[role] ?? role;
}

/** Lo que hace de verdad el taller (14/10/2026): lo mismo que dicen
 *  `ROLE_CAPABILITIES` (backend) y `SAT_CAPS` (aquí). Si cambia el rol, cambia
 *  esta frase. */
export const SAT_ROLE_NOTE =
  "Taller: la Cola SAT completa (preparar, embalar, enviar, etiqueta y "
  + "seguimiento) y los pedidos de muestra de punta a punta (darlos de alta, "
  + "prepararlos, enviarlos, completarlos o anularlos). Ve la bandeja y la "
  + "ficha de cualquier pedido, pero en un pedido normal solo mira. No cobra, "
  + "no factura ni manda correos —salvo «Enviar a SAT», solo a la dirección "
  + "del taller y sin factura— y no ve contactos, documentos, proformas ni "
  + "ofertas.";

/** Nota breve del ámbito del rol elegido, para el selector. */
export function roleScopeNote(role: Role): string {
  if (role === "sat") return SAT_ROLE_NOTE;
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
