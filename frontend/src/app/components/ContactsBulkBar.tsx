"use client";

import { Download, Tag as TagIcon, UserCheck, X, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { getUsers, type User } from "../lib/api";
import {
  bulkContactAction,
  bulkExportContactsCsv,
  type BulkAction,
} from "../lib/bulkApi";
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
 *  - assign_owner → user / manager / admin (PR-D Reglas-Assign)
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
  // Sprint Reglas-Assign PR-D: el botón de "Asignar comercial" pasa a
  // estar visible para cualquier comercial (user+) — decisión §1 del
  // spec, ya alineada en el endpoint backend desde PR-Ca. El viewer
  // sigue excluido porque no tiene permisos de escritura en absoluto.
  const canAssign = role === "admin" || role === "manager" || role === "user";
  const canDeactivate = role === "admin";
  // QoL sprint — manager+ pueden exportar CSV de la selección (antes
  // sólo admin via paths internos). Backend permission alineada en
  // /api/contacts/bulk-export-csv.
  const canExport = role === "admin" || role === "manager";

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

  async function handleExport() {
    setBusy(true);
    setError(null);
    try {
      const blob = await bulkExportContactsCsv(selectedIds);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `contacts-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo exportar el CSV."));
    } finally {
      setBusy(false);
    }
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
        {canExport ? (
          <button
            type="button"
            className="button small secondary"
            disabled={busy}
            onClick={handleExport}
            title="Exportar la selección a CSV"
          >
            <Download size={11} aria-hidden /> Exportar CSV
          </button>
        ) : null}
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
