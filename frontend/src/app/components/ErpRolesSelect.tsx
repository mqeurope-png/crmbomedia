"use client";

import type { Role } from "../lib/api";
import { ASSIGNABLE_ERP_ROLES } from "../lib/capabilities";
import { ROLE_LABELS } from "../lib/roles";

/** Roles y permisos — selector MULTI-ROL de los roles operativos ADICIONALES del
 *  ERP (`erp_roles`). Un usuario tiene un rol principal (`RoleSelect`) y, además,
 *  puede acumular estos roles operativos: el permiso efectivo es la UNIÓN de las
 *  capacidades de todos. Solo el admin edita esto (la pantalla es admin-only).
 *
 *  Se ocultan los roles que ya son el rol PRINCIPAL (no aporta marcarlos otra
 *  vez) — así el admin ve solo lo que suma de verdad. */
export function ErpRolesSelect({
  primaryRole,
  value,
  onChange,
}: {
  primaryRole: Role;
  value: ReadonlyArray<string>;
  onChange: (roles: string[]) => void;
}) {
  const options = ASSIGNABLE_ERP_ROLES.filter((role) => role !== primaryRole);
  if (options.length === 0) return null;

  function toggle(role: Role, checked: boolean) {
    const next = new Set(value);
    if (checked) next.add(role);
    else next.delete(role);
    onChange([...next]);
  }

  return (
    <fieldset className="erp-roles-select">
      <legend className="muted small">Roles ERP adicionales</legend>
      {options.map((role) => (
        <label key={role} className="erp-roles-option">
          <input
            type="checkbox"
            name="erp_roles"
            value={role}
            checked={value.includes(role)}
            onChange={(event) => toggle(role, event.target.checked)}
          />
          <span>{ROLE_LABELS[role]}</span>
        </label>
      ))}
    </fieldset>
  );
}
