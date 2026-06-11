"use client";

import { useEffect, useState } from "react";
import { createTask, type Task, type TaskCreatePayload } from "../lib/tasksApi";
import { extractErrorMessage } from "../lib/errors";
import { getGoogleStatus, type GoogleStatus } from "../lib/googleApi";

type Props = {
  /** Pre-fill contact link — used by the contact detail "Tareas" tab. */
  contactId?: string | null;
  onClose: () => void;
  onCreated: (task: Task) => void;
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

function tomorrowAtNine(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(9, 0, 0, 0);
  // datetime-local input wants `YYYY-MM-DDTHH:mm` in local time.
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Modal to create a task. Minimal MVP — title + due + priority +
 * reminder + (optional) contact. The dashboard widget, the
 * contact-tab "Tareas" and the standalone /tasks page all open this
 * with different defaults. */
export function TaskModal({ contactId, onClose, onCreated }: Props) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [dueAt, setDueAt] = useState(tomorrowAtNine());
  const [priority, setPriority] = useState<Task["priority"]>("medium");
  const [reminder, setReminder] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Google Calendar sync — the checkbox only renders once we know
  // the user has a connected + configured calendar. Default ON when
  // it shows; default OFF (and tip shown) when they aren't connected.
  const [googleStatus, setGoogleStatus] = useState<GoogleStatus | null>(null);
  const [syncWithGoogle, setSyncWithGoogle] = useState(true);

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
      const payload: TaskCreatePayload = {
        title: title.trim(),
        description: description.trim() || null,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
        priority,
        reminder_minutes_before: reminder,
        contact_id: contactId ?? null,
        sync_with_google_calendar: canSync,
      };
      const task = await createTask(payload);
      onCreated(task);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear la tarea."));
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <header>
          <h2>Nueva tarea</h2>
          {contactId ? (
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
              {submitting ? "Creando…" : "Crear tarea"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
