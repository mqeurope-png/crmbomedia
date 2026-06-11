"use client";

import { CheckCircle2, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  completeTask,
  deleteTask,
  listContactTasks,
  type Task,
} from "../lib/tasksApi";
import { extractErrorMessage } from "../lib/errors";
import { TaskModal } from "./TaskModal";

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Tasks tied to a single contact. Replaces the static "Tareas
 * pendientes" card on the contact detail page with a real list
 * backed by the Mini-PR C API: complete, delete, create. */
export function ContactTasksSection({ contactId }: { contactId: string }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);

  const reload = useCallback(async () => {
    try {
      const items = await listContactTasks(contactId);
      setTasks(items);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las tareas."));
    }
  }, [contactId]);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  async function handleComplete(task: Task) {
    try {
      await completeTask(task.id);
      await reload();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo completar la tarea."));
    }
  }

  async function handleDelete(task: Task) {
    if (!window.confirm(`¿Borrar la tarea "${task.title}"?`)) return;
    try {
      await deleteTask(task.id);
      await reload();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la tarea."));
    }
  }

  return (
    <article className="card">
      <div className="section-title">
        <h2>Tareas pendientes</h2>
        <button
          type="button"
          className="button small"
          onClick={() => setShowModal(true)}
        >
          <Plus size={11} aria-hidden /> Crear
        </button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : tasks.length === 0 ? (
        <p className="muted">Sin tareas pendientes.</p>
      ) : (
        <ul className="tasks-list">
          {tasks.map((task) => (
            <li key={task.id} className="tasks-row">
              <button
                type="button"
                className="tasks-row-complete"
                onClick={() => handleComplete(task)}
                title="Marcar como hecha"
              >
                <CheckCircle2 size={16} aria-hidden />
              </button>
              <div className="tasks-row-main">
                <p className="tasks-row-title">{task.title}</p>
                {task.due_at ? (
                  <p className="muted small tasks-row-meta">
                    Vence: {formatDateTime(task.due_at)}
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                className="tasks-row-delete"
                onClick={() => handleDelete(task)}
                title="Borrar"
              >
                <Trash2 size={13} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      {showModal ? (
        <TaskModal
          contactId={contactId}
          onClose={() => setShowModal(false)}
          onCreated={async () => {
            setShowModal(false);
            await reload();
          }}
        />
      ) : null}
    </article>
  );
}
