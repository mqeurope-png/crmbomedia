"use client";

import { useEffect, useState } from "react";
import { getUsers, type User } from "../../lib/api";

type Props = {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  includeInactive?: boolean;
};

/**
 * PR-Backlog-Consolidado A2/A3.
 *
 * Selector de user del CRM para los step configs (asignar
 * propietario, notificar a, etc.). Antes los paneles pedían el
 * `user_id` como input texto con placeholder "UUID del user activo"
 * — flujo imposible en la práctica porque `/admin/users` no enseña
 * los UUIDs. Aquí cargamos `GET /api/users`, mostramos
 * `nombre · email · rol` y persistimos el `id` igual que hoy.
 */
export function WorkflowUserPicker({
  value,
  onChange,
  placeholder = "— Selecciona un usuario —",
  includeInactive = false,
}: Props) {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getUsers()
      .then((rows) => {
        if (!cancelled) {
          const filtered = includeInactive
            ? rows
            : rows.filter((u) => u.is_active);
          setUsers(filtered);
        }
      })
      .catch(() => {
        if (!cancelled) setUsers([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [includeInactive]);

  if (loading) {
    return <p className="muted small">Cargando usuarios…</p>;
  }
  if (users.length === 0) {
    return (
      <p className="form-error small">
        No hay usuarios activos en el CRM. Crea uno desde{" "}
        <code>/admin/users</code>.
      </p>
    );
  }

  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{placeholder}</option>
      {users.map((u) => (
        <option key={u.id} value={u.id}>
          {u.full_name || u.email} · {u.email} · {u.role}
        </option>
      ))}
    </select>
  );
}
