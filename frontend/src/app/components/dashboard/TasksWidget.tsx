"use client";

/**
 * "📋 Mis tareas y agenda" — PR-E2. Reusa `getMyBuckets({scope:
 * "mine"})` que ya devuelve `{overdue, today, tomorrow, …}`.
 * Cada item lleva checkbox para marcar completada (POST a
 * `/api/tasks/{id}/complete`).
 */
import { CheckCircle2, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  completeTask,
  getMyBuckets,
  type Task,
  type TaskBuckets,
} from "../../lib/tasksApi";
import { TaskModal } from "../TaskModal";

function fmtTime(due: string | null): string {
  if (!due) return "Sin hora";
  const d = new Date(due);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("es-ES", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtAyer(due: string | null): string {
  if (!due) return "—";
  const d = new Date(due);
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const isYesterday = d.toDateString() === yesterday.toDateString();
  return `${isYesterday ? "Ayer" : d.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
  })} · ${fmtTime(due)}`;
}

function Bucket({
  title,
  rows,
  tone,
  formatRow,
  onComplete,
  pendingId,
}: {
  title: string;
  rows: Task[];
  tone: "danger" | "primary" | "info";
  formatRow: (t: Task) => string;
  onComplete: (id: string) => Promise<void>;
  pendingId: string | null;
}) {
  if (rows.length === 0) return null;
  return (
    <div className="contact-tasks-pending-bucket">
      <h4 className={`contact-tasks-pending-bucket-title is-${tone}`}>
        {title} ({rows.length})
      </h4>
      <ul className="contact-tasks-pending-list">
        {rows.slice(0, 5).map((t) => (
          <li key={t.id} className="contact-tasks-pending-row">
            <input
              type="checkbox"
              aria-label={`Completar "${t.title}"`}
              checked={false}
              disabled={pendingId === t.id}
              onChange={() => onComplete(t.id)}
            />
            <span className="contact-tasks-pending-row-title">
              {t.contact_id ? (
                <Link href={`/contacts/${t.contact_id}`}>{t.title}</Link>
              ) : (
                t.title
              )}
            </span>
            <span className="muted small">{formatRow(t)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function TasksWidget() {
  const [buckets, setBuckets] = useState<TaskBuckets | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);

  async function load() {
    try {
      const res = await getMyBuckets({ scope: "mine" });
      setBuckets(res);
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

  async function handleComplete(id: string) {
    setPendingId(id);
    try {
      await completeTask(id);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo completar la tarea."));
    } finally {
      setPendingId(null);
    }
  }

  const empty =
    buckets &&
    buckets.overdue.length === 0 &&
    buckets.today.length === 0 &&
    buckets.tomorrow.length === 0;

  return (
    <article className="card widget widget-tasks">
      <header className="section-title">
        <h2>
          <CheckCircle2 size={14} aria-hidden /> Mis tareas y agenda
        </h2>
        <Link href="/tasks" className="small muted">
          Ver todas
        </Link>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : empty ? (
        <div className="widget-empty">
          <p className="muted small">
            No tienes tareas pendientes. ¡Buen trabajo!
          </p>
          <button
            type="button"
            className="button small"
            onClick={() => setShowModal(true)}
          >
            <Plus size={11} aria-hidden /> Crear tarea
          </button>
        </div>
      ) : (
        <div className="contact-tasks-pending">
          <Bucket
            title="Vencidas"
            tone="danger"
            rows={buckets?.overdue ?? []}
            formatRow={(t) => fmtAyer(t.due_at)}
            onComplete={handleComplete}
            pendingId={pendingId}
          />
          <Bucket
            title="Hoy"
            tone="primary"
            rows={buckets?.today ?? []}
            formatRow={(t) => fmtTime(t.due_at)}
            onComplete={handleComplete}
            pendingId={pendingId}
          />
          <Bucket
            title="Mañana"
            tone="info"
            rows={buckets?.tomorrow ?? []}
            formatRow={(t) => fmtTime(t.due_at)}
            onComplete={handleComplete}
            pendingId={pendingId}
          />
        </div>
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
