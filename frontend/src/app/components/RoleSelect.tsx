"use client";

import type { Role } from "../lib/api";
import {
  CRM_ROLES,
  ERP_ROLES,
  ROLE_LABELS,
  roleScopeNote,
  scopeChangeWarning,
} from "../lib/roles";

/** ERP-F2-fix1 — selector de rol AGRUPADO por ámbito (CRM / ERP), con la nota
 *  de lo que ese rol ve y, al editar, un aviso si el cambio salta de ámbito.
 *  Controlado: el padre lleva el valor (para leer el aviso antes de guardar).
 */
export function RoleSelect({
  name,
  value,
  onChange,
  originalRole,
  id,
}: {
  name: string;
  value: Role;
  onChange: (role: Role) => void;
  /** Rol actual del usuario (solo al editar): dispara el aviso de ámbito. */
  originalRole?: Role;
  id?: string;
}) {
  const warning =
    originalRole !== undefined ? scopeChangeWarning(originalRole, value) : null;
  return (
    <div className="role-select">
      <select
        id={id}
        name={name}
        value={value}
        aria-label="Rol"
        onChange={(event) => onChange(event.target.value as Role)}
      >
        <optgroup label="CRM (comercial)">
          {CRM_ROLES.map((role) => (
            <option key={role} value={role}>{ROLE_LABELS[role]}</option>
          ))}
        </optgroup>
        <optgroup label="ERP (operativo)">
          {ERP_ROLES.map((role) => (
            <option key={role} value={role}>{ROLE_LABELS[role]}</option>
          ))}
        </optgroup>
      </select>
      <p className="role-scope-note muted small">{roleScopeNote(value)}</p>
      {warning ? (
        <p className="role-scope-warn" role="alert">
          <span aria-hidden="true">⚠ </span>{warning}
        </p>
      ) : null}
    </div>
  );
}
