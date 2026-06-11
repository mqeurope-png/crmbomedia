"use client";

import { AlertCircle, CheckCircle2, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import { getDashboardTasksPending } from "../../lib/dashboardApi";
import type { Task } from "../../lib/tasksApi";
import { TaskModal } from "../TaskModal";

function dueLabel(dueAt: string | null): {
  label: string;
  cls: string;
} {
  if (!dueAt) return { label: "Sin fecha", cls: "muted" };
  const d = new Date(dueAt);
  const now = new Date();
  const startOfDay = (x: Date) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const diffDays = Math.round(
    (startOfDay(d).getTime() - startOfDay(now).getTime()) / (24 * 3600 * 1000),
  );
  const time = d.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
  if (d < now) return { label: `Vencida · ${time}`, cls: "due-overdue" };
  if (diffDays === 0) return { label: `Hoy · ${time}`, cls: "due-today" };
  if (diffDays === 1) return { label: `Mañana · ${time}`, cls: "due-soon" };
  return {
    label: d.toLocaleDateString("es-ES", { day: "2-digit", month: "short" }),
    cls: "muted",
  };
}

export function TasksWidget() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);

  async function load() {
    try {
      const items = await getDashboardTasksPending();
      setTasks(items);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las tareas."));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <article className="card widget widget-tasks">
      <header className="section-title">
        <h2>
          <CheckCircle2 size={14} aria-hidden /> Mis tareas pendientes
        </h2>
        <Link href="/tasks" className="small muted">
          Ver todas
        </Link>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : tasks.length === 0 ? (
        <div className="widget-empty">
          <p className="muted small">No tienes tareas pendientes. ¡Buen trabajo!</p>
          <button
            type="button"
            className="button small"
            onClick={() => setShowModal(true)}
          >
            <Plus size={11} aria-hidden /> Crear tarea
          </button>
        </div>
      ) : (
        <ul className="widget-list">
          {tasks.map((task) => {
            const due = dueLabel(task.due_at);
            return (
              <li key={task.id} className="widget-row">
                <div className="widget-row-main">
                  <p className="widget-row-title">
                    {task.title}
                    {task.priority === "urgent" || task.priority === "high" ? (
                      <AlertCircle
                        size={11}
                        className={`task-priority-${task.priority}`}
                        aria-hidden
                      />
                    ) : null}
                  </p>
                  <p className="widget-row-meta">
                    <span className={due.cls}>{due.label}</span>
                    {task.contact ? (
                      <>
                        {" · "}
                        <Link href={`/contacts/${task.contact.id}`}>
                          {[task.contact.first_name, task.contact.last_name]
                            .filter(Boolean)
                            .join(" ") || task.contact.email}
                        </Link>
                      </>
                    ) : null}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {showModal ? (
        <TaskModal
          onClose={() => setShowModal(false)}
          onCreated={async () => {
            setShowModal(false);
            await load();
          }}
        />
      ) : null}
    </article>
  );
}
