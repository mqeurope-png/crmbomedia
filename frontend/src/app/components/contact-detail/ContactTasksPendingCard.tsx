"use client";

/**
 * Card de Resumen ficha contacto — "Tareas pendientes" agrupadas por
 * Vencidas / Hoy / Mañana. Replica el widget del dashboard pero
 * filtrado al contacto. PR-Db.
 *
 * Mantiene el fetch local del componente (no requiere que el padre
 * pase tasks); usa `/api/contacts/{id}/tasks` igual que
 * `ContactTasksSection`.
 */
import { ArrowUpRight, CheckSquare } from "lucide-react";
import { useEffect, useState } from "react";
import { listContactTasks, type Task } from "../../lib/tasksApi";

type Props = {
  contactId: string;
  onSeeAll?: () => void;
};

type Bucket = {
  key: "overdue" | "today" | "tomorrow";
  label: string;
  tone: "danger" | "primary" | "info";
  rows: Task[];
};

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function classifyBucket(task: Task, today: Date): "overdue" | "today" | "tomorrow" | null {
  if (!task.due_at) return null;
  const due = new Date(task.due_at);
  if (Number.isNaN(due.getTime())) return null;
  const dueDay = startOfDay(due);
  const todayDay = startOfDay(today);
  const tomorrowDay = new Date(todayDay);
  tomorrowDay.setDate(tomorrowDay.getDate() + 1);
  if (dueDay.getTime() < todayDay.getTime()) return "overdue";
  if (dueDay.getTime() === todayDay.getTime()) return "today";
  if (dueDay.getTime() === tomorrowDay.getTime()) return "tomorrow";
  return null;
}

function formatTime(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("es-ES", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

const STATUS_OPEN = new Set(["pending", "in_progress"]);

export function ContactTasksPendingCard({ contactId, onSeeAll }: Props) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listContactTasks(contactId)
      .then((rows) => {
        if (!cancelled) setTasks(rows);
      })
      .catch(() => {
        if (!cancelled) setError("No se pudieron cargar las tareas.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [contactId]);

  const now = new Date();
  const open = tasks.filter((t) => STATUS_OPEN.has(t.status));
  const buckets: Bucket[] = [
    {
      key: "overdue",
      label: "Vencidas",
      tone: "danger",
      rows: open.filter((t) => classifyBucket(t, now) === "overdue"),
    },
    {
      key: "today",
      label: "Hoy",
      tone: "primary",
      rows: open.filter((t) => classifyBucket(t, now) === "today"),
    },
    {
      key: "tomorrow",
      label: "Mañana",
      tone: "info",
      rows: open.filter((t) => classifyBucket(t, now) === "tomorrow"),
    },
  ];
  const totalPending = buckets.reduce((acc, b) => acc + b.rows.length, 0);

  return (
    <article className="card contact-summary-card">
      <header className="contact-summary-card-header">
        <h3>
          <CheckSquare size={14} aria-hidden /> Tareas pendientes
        </h3>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : totalPending === 0 ? (
        <p className="muted small">Sin tareas pendientes.</p>
      ) : (
        <div className="contact-tasks-pending">
          {buckets.map((b) =>
            b.rows.length === 0 ? null : (
              <div key={b.key} className="contact-tasks-pending-bucket">
                <h4 className={`contact-tasks-pending-bucket-title is-${b.tone}`}>
                  {b.label} ({b.rows.length})
                </h4>
                <ul className="contact-tasks-pending-list">
                  {b.rows.slice(0, 4).map((t) => (
                    <li key={t.id} className="contact-tasks-pending-row">
                      <input
                        type="checkbox"
                        defaultChecked={false}
                        aria-label={`Completar "${t.title}"`}
                        disabled
                        title="Marcar como completada desde la pestaña Tareas"
                      />
                      <span className="contact-tasks-pending-row-title">
                        {t.title}
                      </span>
                      <span className="muted small">
                        {t.due_at ? formatTime(t.due_at) : "Sin hora"}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ),
          )}
        </div>
      )}
      {onSeeAll && totalPending > 0 ? (
        <button
          type="button"
          className="contact-summary-link"
          onClick={onSeeAll}
        >
          Ver todas <ArrowUpRight size={12} aria-hidden />
        </button>
      ) : null}
    </article>
  );
}
