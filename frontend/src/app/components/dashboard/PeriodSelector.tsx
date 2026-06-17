"use client";

/**
 * Segmented temporal compartido — PR-E3. [3 días][1 semana][15 días]
 * [30 días][Custom ▾]. El custom despliega dos `<input type=date>`
 * (desde/hasta). Presentacional: el parent decide persistencia.
 *
 * Reutilizado por los widgets del dashboard (Leads prioritarios,
 * Últimas interacciones, Stats campañas) y por el editor del field
 * `brevo_campaign_interaction` en /contacts.
 */
import { Calendar } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { DashboardPeriod, DashboardWindow } from "../../lib/dashboardApi";

type Props = {
  value: DashboardWindow;
  onChange: (next: DashboardWindow) => void;
  /** Incluir la opción "Todo" (sin cota temporal). Para el field de
      campañas Brevo Bart pide default "Todo"; en widgets no aplica. */
  includeAll?: boolean;
  size?: "sm" | "md";
};

const PRESETS: ReadonlyArray<[DashboardPeriod, string]> = [
  ["3d", "3 días"],
  ["7d", "1 semana"],
  ["15d", "15 días"],
  ["30d", "30 días"],
];

function toDateInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString().slice(0, 10);
}

export function PeriodSelector({
  value,
  onChange,
  includeAll = false,
  size = "sm",
}: Props) {
  const [customOpen, setCustomOpen] = useState(value.period === "custom");
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (value.period !== "custom") setCustomOpen(false);
  }, [value.period]);

  useEffect(() => {
    if (!customOpen) return;
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setCustomOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [customOpen]);

  return (
    <div
      ref={wrapRef}
      className={`period-selector period-selector-${size}`}
    >
      <div className="widget-segment" role="radiogroup" aria-label="Período">
        {includeAll ? (
          <button
            type="button"
            role="radio"
            aria-checked={value.period === ("all" as DashboardPeriod)}
            className={`widget-segment-item${
              value.period === ("all" as DashboardPeriod) ? " is-active" : ""
            }`}
            onClick={() =>
              onChange({ period: "all" as DashboardPeriod })
            }
          >
            Todo
          </button>
        ) : null}
        {PRESETS.map(([p, label]) => (
          <button
            key={p}
            type="button"
            role="radio"
            aria-checked={value.period === p}
            className={`widget-segment-item${
              value.period === p ? " is-active" : ""
            }`}
            onClick={() => onChange({ period: p })}
          >
            {label}
          </button>
        ))}
        <button
          type="button"
          className={`widget-segment-item${
            value.period === "custom" ? " is-active" : ""
          }`}
          aria-pressed={value.period === "custom"}
          onClick={() => setCustomOpen((o) => !o)}
          title="Rango personalizado"
        >
          <Calendar size={12} aria-hidden /> Custom
        </button>
      </div>
      {customOpen ? (
        <div className="period-selector-custom">
          <label>
            Desde
            <input
              type="date"
              value={toDateInput(value.start)}
              onChange={(e) => {
                const start = e.target.value
                  ? new Date(`${e.target.value}T00:00:00`).toISOString()
                  : null;
                onChange({ period: "custom", start, end: value.end ?? null });
              }}
            />
          </label>
          <label>
            Hasta
            <input
              type="date"
              value={toDateInput(value.end)}
              onChange={(e) => {
                const end = e.target.value
                  ? new Date(`${e.target.value}T23:59:59`).toISOString()
                  : null;
                onChange({ period: "custom", start: value.start ?? null, end });
              }}
            />
          </label>
        </div>
      ) : null}
    </div>
  );
}
