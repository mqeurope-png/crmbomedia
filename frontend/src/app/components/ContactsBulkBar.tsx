"use client";

import { Tag as TagIcon, UserCheck, X, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { getUsers, type User } from "../lib/api";
import { bulkContactAction, type BulkAction } from "../lib/bulkApi";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  selectedIds: string[];
  currentUser: User | null;
  /** Called after a successful action so the list reloads + selection
   *  clears. */
  onAfterAction: (action: BulkAction, affected: number) => void;
  onClear: () => void;
};

const STATUS_OPTIONS = [
  ["new", "Nuevo"],
  ["qualified", "Calificado"],
  ["working", "Trabajando"],
  ["won", "Ganado"],
  ["lost", "Perdido"],
] as const;

/** Floating action bar that pops up when 1+ contacts are selected.
 *  Visibility of each action follows the role table:
 *  - assign_owner → admin / manager
 *  - change_status / add_tag / remove_tag → any signed-in user
 *  - deactivate → admin only
 */
export function ContactsBulkBar({
  selectedIds,
  currentUser,
  onAfterAction,
  onClear,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<BulkAction | null>(null);
  const [users, setUsers] = useState<User[]>([]);

  const role = currentUser?.role;
  const canAssign = role === "admin" || role === "manager";
  const canDeactivate = role === "admin";

  useEffect(() => {
    if (open !== "assign_owner" || users.length > 0) return;
    getUsers()
      .then(setUsers)
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudo cargar la lista de usuarios.")),
      );
  }, [open, users.length]);

  if (selectedIds.length === 0) return null;

  async function run(action: BulkAction, payload: Record<string, unknown> = {}) {
    setBusy(true);
    setError(null);
    try {
      const result = await bulkContactAction(selectedIds, action, payload);
      onAfterAction(action, result.affected_count);
      setOpen(null);
    } catch (err) {
      setError(extractErrorMessage(err, "La acción bulk falló."));
    } finally {
      setBusy(false);
    }
  }

  async function handleAssign(userId: string) {
    await run("assign_owner", { owner_user_id: userId });
  }

  async function handleStatus(newStatus: string) {
    await run("change_status", { new_status: newStatus });
  }

  async function handleDeactivate() {
    if (
      !window.confirm(
        `¿Desactivar ${selectedIds.length} contacto${selectedIds.length === 1 ? "" : "s"}? Esto los oculta del listado.`,
      )
    ) {
      return;
    }
    await run("deactivate");
  }

  return (
    <div className="bulk-bar" role="region" aria-label="Acciones masivas">
      <strong>
        {selectedIds.length} contacto{selectedIds.length === 1 ? "" : "s"} seleccionado
        {selectedIds.length === 1 ? "" : "s"}
      </strong>
      <div className="bulk-bar-actions">
        {canAssign ? (
          <button
            type="button"
            className="button small"
            disabled={busy}
            onClick={() => setOpen(open === "assign_owner" ? null : "assign_owner")}
          >
            <UserCheck size={11} aria-hidden /> Asignar a…
          </button>
        ) : null}
        <button
          type="button"
          className="button small secondary"
          disabled={busy}
          onClick={() => setOpen(open === "change_status" ? null : "change_status")}
        >
          <TagIcon size={11} aria-hidden /> Cambiar estado
        </button>
        {canDeactivate ? (
          <button
            type="button"
            className="button small danger"
            disabled={busy}
            onClick={handleDeactivate}
          >
            <XCircle size={11} aria-hidden /> Desactivar
          </button>
        ) : null}
        <button
          type="button"
          className="bulk-bar-close"
          onClick={onClear}
          title="Limpiar selección"
        >
          <X size={14} aria-hidden />
        </button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {open === "assign_owner" ? (
        <div className="bulk-bar-panel">
          {users.length === 0 ? (
            <p className="muted small">Cargando usuarios…</p>
          ) : (
            <ul className="bulk-bar-options">
              {users
                .filter((u) => u.is_active)
                .map((u) => (
                  <li key={u.id}>
                    <button
                      type="button"
                      className="button small secondary"
                      disabled={busy}
                      onClick={() => handleAssign(u.id)}
                    >
                      {u.full_name || u.email}
                    </button>
                  </li>
                ))}
            </ul>
          )}
        </div>
      ) : null}
      {open === "change_status" ? (
        <div className="bulk-bar-panel">
          <ul className="bulk-bar-options">
            {STATUS_OPTIONS.map(([value, label]) => (
              <li key={value}>
                <button
                  type="button"
                  className="button small secondary"
                  disabled={busy}
                  onClick={() => handleStatus(value)}
                >
                  {label}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
