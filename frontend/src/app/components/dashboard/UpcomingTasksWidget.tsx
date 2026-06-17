"use client";

/**
 * "📅 Próximas tareas" — PR-E2 reemplaza al widget "Próximos eventos"
 * (Google Calendar). Tira de `/api/dashboard/upcoming-tasks` que ya
 * filtra status open + due_at >= NOW().
 */
import { CalendarClock } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getDashboardUpcomingTasks } from "../../lib/dashboardApi";
import type { Task } from "../../lib/tasksApi";

function formatWhen(due: string): string {
  const d = new Date(due);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function UpcomingTasksWidget() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDashboardUpcomingTasks(8)
      .then((rows) => {
        if (!cancelled) setTasks(rows);
      })
      .catch(() => {
        if (!cancelled) setError("No se pudieron cargar las próximas tareas.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <article className="card widget widget-upcoming-tasks">
      <header className="section-title">
        <h2>
          <CalendarClock size={14} aria-hidden /> Próximas tareas
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
          <p className="muted small">Sin tareas próximas.</p>
        </div>
      ) : (
        <ul className="widget-list">
          {tasks.map((t) => (
            <li key={t.id} className="widget-row">
              <div className="widget-row-main">
                <p className="widget-row-title">
                  {t.contact_id ? (
                    <Link href={`/contacts/${t.contact_id}`}>{t.title}</Link>
                  ) : (
                    t.title
                  )}
                </p>
                <p className="widget-row-meta">
                  <span className="muted small">
                    {t.due_at ? formatWhen(t.due_at) : "Sin fecha"}
                  </span>
                  {t.contact ? (
                    <span className="muted small">
                      ·{" "}
                      {[t.contact.first_name, t.contact.last_name]
                        .filter(Boolean)
                        .join(" ")}
                    </span>
                  ) : null}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
