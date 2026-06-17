"use client";

/**
 * "👥 Leads prioritarios" — PR-E2 reemplaza al widget legacy "Leads
 * sin atender" (UnattendedLeadsWidget). Cambios:
 *
 * - Solo lista contactos asignados al current_user (no leads sueltos).
 * - Cada lead trae un `reason` (`recent` / `assigned` / `active`) →
 *   chip de color para que el operador entienda por qué aparece.
 * - Selector temporal independiente del header [7d] [14d] [30d].
 * - Click → ficha contacto. Sin botón "Asignarme" (son del current).
 */
import { Users } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  getDashboardPriorityLeads,
  type DashboardPeriod,
  type PriorityLead,
} from "../../lib/dashboardApi";

const REASON_LABEL: Record<string, { label: string; tone: string }> = {
  recent: { label: "Recién creado", tone: "is-info" },
  assigned: { label: "Recién asignado", tone: "is-success" },
  active: { label: "Activo", tone: "is-warning" },
};

const PERIOD_OPTIONS: ReadonlyArray<[DashboardPeriod, string]> = [
  ["7d", "7 días"],
  ["14d", "14 días"],
  ["30d", "30 días"],
];

function relative(value: string): string {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Date.now() - then;
  const day = Math.floor(diff / 86_400_000);
  if (day === 0) return "hoy";
  if (day === 1) return "ayer";
  if (day < 30) return `hace ${day}d`;
  return new Date(value).toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
  });
}

export function PriorityLeadsWidget() {
  const [period, setPeriod] = useState<DashboardPeriod>("14d");
  const [leads, setLeads] = useState<PriorityLead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getDashboardPriorityLeads(period, 10)
      .then((rows) => {
        if (!cancelled) setLeads(rows);
      })
      .catch(() => {
        if (!cancelled) setError("No se pudieron cargar los leads.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [period]);

  return (
    <article className="card widget widget-priority-leads">
      <header className="section-title">
        <h2>
          <Users size={14} aria-hidden /> Leads prioritarios
        </h2>
        <div
          className="widget-segment"
          role="radiogroup"
          aria-label="Rango temporal"
        >
          {PERIOD_OPTIONS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={period === value}
              className={`widget-segment-item${
                period === value ? " is-active" : ""
              }`}
              onClick={() => setPeriod(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : leads.length === 0 ? (
        <div className="widget-empty">
          <p className="muted small">
            No tienes leads prioritarios en este período.
          </p>
        </div>
      ) : (
        <ul className="widget-list">
          {leads.map((lead) => {
            const reason = REASON_LABEL[lead.reason] ?? {
              label: lead.reason,
              tone: "is-muted",
            };
            const name =
              [lead.first_name, lead.last_name].filter(Boolean).join(" ") ||
              lead.email;
            return (
              <li key={lead.id} className="widget-row">
                <div className="widget-row-main">
                  <p className="widget-row-title">
                    <Link href={`/contacts/${lead.id}`}>{name}</Link>
                  </p>
                  <p className="widget-row-meta">
                    <span className="muted small">{lead.email}</span>
                    <span className="muted small">
                      · {relative(lead.signal_at)}
                    </span>
                  </p>
                </div>
                <span className={`chip ${reason.tone}`}>{reason.label}</span>
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}
