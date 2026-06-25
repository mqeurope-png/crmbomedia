"use client";

import { useEffect, useState } from "react";
import {
  createTask,
  updateTask,
  type Task,
  type TaskCreatePayload,
  type TaskUpdatePayload,
} from "../lib/tasksApi";
import { extractErrorMessage } from "../lib/errors";
import { getGoogleStatus, type GoogleStatus } from "../lib/googleApi";

type Props = {
  /** Pre-fill contact link — used by the contact detail "Tareas" tab. */
  contactId?: string | null;
  /** Pass a Task to edit in place; omit (or null) for creation. */
  task?: Task | null;
  onClose: () => void;
  onCreated?: (task: Task) => void;
  onUpdated?: (task: Task) => void;
};

const PRIORITIES: Array<[Task["priority"], string]> = [
  ["low", "Baja"],
  ["medium", "Media"],
  ["high", "Alta"],
  ["urgent", "Urgente"],
];

const REMINDERS: Array<[number | null, string]> = [
  [null, "Sin recordatorio"],
  [0, "Justo a la hora"],
  [5, "5 minutos antes"],
  [15, "15 minutos antes"],
  [30, "30 minutos antes"],
  [60, "1 hora antes"],
  [1440, "1 día antes"],
];

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function isoToLocalInputValue(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function tomorrowAtNine(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(9, 0, 0, 0);
  return isoToLocalInputValue(d.toISOString());
}

/** Modal to create OR edit a task.
 *
 * - When `task` is null/undefined the form is in "create" mode and
 *   posts to `POST /api/tasks`.
 * - When `task` is set the form opens prefilled and submits to
 *   `PATCH /api/tasks/{id}` with only the changed fields. The Google
 *   sync checkbox tracks whether the task is currently synced so
 *   toggling it triggers create/delete on the backend.
 */
export function TaskModal({
  contactId,
  task,
  onClose,
  onCreated,
  onUpdated,
}: Props) {
  const isEdit = !!task;
  const [title, setTitle] = useState(task?.title ?? "");
  const [description, setDescription] = useState(task?.description ?? "");
  const [dueAt, setDueAt] = useState(
    task?.due_at ? isoToLocalInputValue(task.due_at) : tomorrowAtNine(),
  );
  const [priority, setPriority] = useState<Task["priority"]>(
    task?.priority ?? "medium",
  );
  const [reminder, setReminder] = useState<number | null>(
    task?.reminder_minutes_before ?? null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [googleStatus, setGoogleStatus] = useState<GoogleStatus | null>(null);
  // For new tasks default to ON when GCal is available; for editing
  // start from the task's current sync state.
  const [syncWithGoogle, setSyncWithGoogle] = useState<boolean>(
    isEdit ? !!task?.google_event_id : true,
  );

  useEffect(() => {
    getGoogleStatus()
      .then(setGoogleStatus)
      .catch(() => setGoogleStatus(null));
  }, []);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!title.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const canSync =
        !!googleStatus?.connected &&
        !!googleStatus?.selected_calendar &&
        syncWithGoogle;
      if (isEdit && task) {
        const payload: TaskUpdatePayload = {
          title: title.trim(),
          description: description.trim() || null,
          due_at: dueAt ? new Date(dueAt).toISOString() : null,
          priority,
          reminder_minutes_before: reminder,
          sync_with_google_calendar: canSync,
        };
        const updated = await updateTask(task.id, payload);
        onUpdated?.(updated);
      } else {
        const payload: TaskCreatePayload = {
          title: title.trim(),
          description: description.trim() || null,
          due_at: dueAt ? new Date(dueAt).toISOString() : null,
          priority,
          reminder_minutes_before: reminder,
          contact_id: contactId ?? null,
          sync_with_google_calendar: canSync,
        };
        const created = await createTask(payload);
        onCreated?.(created);
      }
    } catch (err) {
      setError(
        extractErrorMessage(
          err,
          isEdit ? "No se pudo actualizar la tarea." : "No se pudo crear la tarea.",
        ),
      );
      setSubmitting(false);
    }
  }

  return (
    // Bug 1 fix: wrap en `modal-overlay` + `modal-dialog` (mismo
    // patrón que ContactEditForm) en lugar del antiguo `modal-backdrop`
    // + `modal` sin CSS — el inner `.modal` no tenía estilo y el
    // contenido se mezclaba con la página de fondo.
    <div className="modal-overlay" role="dialog" aria-modal="true">
      <div className="modal-dialog">
        <header>
          <h2>{isEdit ? "Editar tarea" : "Nueva tarea"}</h2>
          {contactId && !isEdit ? (
            <p className="muted small">Vinculada al contacto actual.</p>
          ) : null}
        </header>
        {error ? <p className="form-error">{error}</p> : null}
        <form onSubmit={handleSubmit}>
          <label className="field">
            Título
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              autoFocus
              maxLength={200}
            />
          </label>
          <label className="field">
            Descripción
            <textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={4000}
            />
          </label>
          <label className="field">
            Vencimiento
            <input
              type="datetime-local"
              value={dueAt}
              onChange={(e) => setDueAt(e.target.value)}
            />
          </label>
          <label className="field">
            Prioridad
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as Task["priority"])}
            >
              {PRIORITIES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Recordatorio
            <select
              value={reminder === null ? "" : String(reminder)}
              onChange={(e) =>
                setReminder(e.target.value === "" ? null : Number(e.target.value))
              }
            >
              {REMINDERS.map(([value, label]) => (
                <option key={String(value)} value={value === null ? "" : value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {googleStatus?.connected && googleStatus.selected_calendar ? (
            <div className="task-modal-gcal">
              <label>
                <input
                  type="checkbox"
                  checked={syncWithGoogle}
                  onChange={(e) => setSyncWithGoogle(e.target.checked)}
                />
                Sincronizar con Google Calendar
                {googleStatus.selected_calendar.summary ? (
                  <span className="muted small">
                    {" "}
                    — &quot;{googleStatus.selected_calendar.summary}&quot;
                  </span>
                ) : null}
              </label>
            </div>
          ) : googleStatus && !googleStatus.connected ? (
            <p className="task-modal-gcal-hint muted small">
              💡 Conecta Google Calendar en{" "}
              <a href="/account">/account</a> para sincronizar tus tareas
              con tu agenda.
            </p>
          ) : null}
          <div className="actions">
            <button
              type="button"
              className="button secondary"
              onClick={onClose}
              disabled={submitting}
            >
              Cancelar
            </button>
            <button
              type="submit"
              className="button"
              disabled={submitting || !title.trim()}
            >
              {submitting
                ? isEdit
                  ? "Guardando…"
                  : "Creando…"
                : isEdit
                  ? "Guardar cambios"
                  : "Crear tarea"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
