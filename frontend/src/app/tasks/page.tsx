"use client";

import {
  AlertCircle,
  Calendar,
  CheckCircle2,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { TaskModal } from "../components/TaskModal";
import { extractErrorMessage } from "../lib/errors";
import {
  completeTask,
  deleteTask,
  getMyBuckets,
  listTasks,
  type Task,
  type TaskBuckets,
} from "../lib/tasksApi";
import { getCurrentUser } from "../lib/api";

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

const BUCKET_LABELS: Array<[keyof Omit<TaskBuckets, "total_open">, string]> = [
  ["overdue", "Vencidas"],
  ["today", "Hoy"],
  ["tomorrow", "Mañana"],
  ["later", "Más adelante"],
  ["no_date", "Sin fecha"],
];

const PRIORITY_LABEL: Record<Task["priority"], string> = {
  low: "Baja",
  medium: "Media",
  high: "Alta",
  urgent: "Urgente",
};

export default function TasksPage() {
  const [buckets, setBuckets] = useState<TaskBuckets | null>(null);
  const [completedTasks, setCompletedTasks] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  // "Mostrar completadas" toggle. Off by default so done tasks don't
  // clutter the urgency buckets. When on, an extra "Completadas"
  // section appears at the bottom with the last 50 done tasks.
  const [showCompleted, setShowCompleted] = useState(false);

  const reload = useCallback(async () => {
    try {
      const data = await getMyBuckets();
      setBuckets(data);
      if (showCompleted) {
        const me = await getCurrentUser();
        const page = await listTasks({
          assignedUserId: me.id,
          status: "done",
          limit: 50,
        });
        setCompletedTasks(page.items);
      } else {
        setCompletedTasks([]);
      }
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las tareas."));
    }
  }, [showCompleted]);

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
    <main className="shell shell-wide">
      <PageHeader
        title="Tareas"
        eyebrow="Productividad"
        description="Tus tareas pendientes agrupadas por urgencia."
        actions={
          <>
            <label className="task-show-completed">
              <input
                type="checkbox"
                checked={showCompleted}
                onChange={(e) => setShowCompleted(e.target.checked)}
              />
              Mostrar completadas
            </label>
            <button
              type="button"
              className="button"
              onClick={() => setShowModal(true)}
            >
              <Plus size={13} aria-hidden /> Nueva tarea
            </button>
          </>
        }
      />

      {error ? <p className="form-error">{error}</p> : null}

      {loading ? (
        <p className="muted">Cargando…</p>
      ) : !buckets ? null : (
        <section className="tasks-grid">
          {BUCKET_LABELS.map(([key, label]) => {
            const items = buckets[key];
            return (
              <article key={key} className={`tasks-bucket tasks-bucket--${key}`}>
                <header>
                  <h2>{label}</h2>
                  <span className="muted small">
                    {items.length} pendiente{items.length === 1 ? "" : "s"}
                  </span>
                </header>
                {items.length === 0 ? (
                  <p className="muted small">Sin tareas en esta franja.</p>
                ) : (
                  <ul className="tasks-list">
                    {items.map((task) => (
                      <TaskRow
                        key={task.id}
                        task={task}
                        onComplete={() => handleComplete(task)}
                        onDelete={() => handleDelete(task)}
                        onEdit={() => setEditingTask(task)}
                      />
                    ))}
                  </ul>
                )}
              </article>
            );
          })}
        </section>
      )}

      {showCompleted && completedTasks.length > 0 ? (
        <section className="tasks-completed">
          <header className="section-title">
            <h2>Completadas</h2>
            <span className="muted small">
              {completedTasks.length} última{completedTasks.length === 1 ? "" : "s"}
            </span>
          </header>
          <ul className="tasks-list">
            {completedTasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                onComplete={() => {}}
                onDelete={() => handleDelete(task)}
                onEdit={() => setEditingTask(task)}
              />
            ))}
          </ul>
        </section>
      ) : null}

      {showModal ? (
        <TaskModal
          onClose={() => setShowModal(false)}
          onCreated={async () => {
            setShowModal(false);
            await reload();
          }}
        />
      ) : null}
      {editingTask ? (
        <TaskModal
          task={editingTask}
          onClose={() => setEditingTask(null)}
          onUpdated={async () => {
            setEditingTask(null);
            await reload();
          }}
        />
      ) : null}
    </main>
  );
}

function TaskRow({
  task,
  onComplete,
  onDelete,
  onEdit,
}: {
  task: Task;
  onComplete: () => void;
  onDelete: () => void;
  onEdit: () => void;
}) {
  return (
    <li className={`tasks-row tasks-row--priority-${task.priority}`}>
      <button
        type="button"
        className="tasks-row-complete"
        onClick={onComplete}
        title="Marcar como hecha"
      >
        <CheckCircle2 size={16} aria-hidden />
      </button>
      <div className="tasks-row-main">
        <p className="tasks-row-title">{task.title}</p>
        <p className="muted small tasks-row-meta">
          {task.due_at ? (
            <>
              <Calendar size={11} aria-hidden /> {formatDateTime(task.due_at)}
            </>
          ) : null}
          {task.priority !== "medium" ? (
            <span className={`tasks-row-priority tasks-row-priority--${task.priority}`}>
              <AlertCircle size={11} aria-hidden /> {PRIORITY_LABEL[task.priority]}
            </span>
          ) : null}
          {task.contact ? (
            <Link
              href={`/contacts/${task.contact.id}`}
              className="tasks-row-contact"
            >
              · {[task.contact.first_name, task.contact.last_name]
                .filter(Boolean)
                .join(" ") || task.contact.email}
            </Link>
          ) : null}
        </p>
      </div>
      <button
        type="button"
        className="tasks-row-edit"
        onClick={onEdit}
        title="Editar"
      >
        <Pencil size={13} aria-hidden />
      </button>
      <button
        type="button"
        className="tasks-row-delete"
        onClick={onDelete}
        title="Borrar"
      >
        <Trash2 size={13} aria-hidden />
      </button>
    </li>
  );
}
