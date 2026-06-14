"use client";

import { Calendar, Eye, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

/** Quick-filter row above the thread list. Date ranges are stored
 *  as ISO strings in the URL so refresh keeps the filter; the
 *  "Última semana" / "Último mes" presets compute fresh boundaries
 *  on every click so they don't drift relative to today. */
export function EmailFiltersBar() {
  const router = useRouter();
  const params = useSearchParams();
  const hasUnread = params.get("has_unread") === "true";
  const since = params.get("since");
  const [customOpen, setCustomOpen] = useState(false);
  const [customSince, setCustomSince] = useState("");
  const [customUntil, setCustomUntil] = useState("");

  const activeRange = (() => {
    if (!since) return "all";
    const diff = Date.now() - new Date(since).getTime();
    if (diff < 36 * 3600 * 1000) return "today";
    if (diff < 8 * 24 * 3600 * 1000) return "week";
    if (diff < 32 * 24 * 3600 * 1000) return "month";
    return "custom";
  })();

  const setParam = (overrides: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(overrides)) {
      if (v === null) next.delete(k);
      else next.set(k, v);
    }
    const qs = next.toString();
    router.push(qs ? `/emails?${qs}` : "/emails");
  };

  const applyRange = (range: "today" | "week" | "month" | "all") => {
    if (range === "all") {
      setParam({ since: null, until: null });
      return;
    }
    const now = new Date();
    let sinceDate: Date;
    if (range === "today") {
      sinceDate = new Date(
        now.getFullYear(),
        now.getMonth(),
        now.getDate(),
        0,
        0,
        0,
      );
    } else if (range === "week") {
      sinceDate = new Date(now.getTime() - 7 * 24 * 3600 * 1000);
    } else {
      sinceDate = new Date(now.getTime() - 30 * 24 * 3600 * 1000);
    }
    setParam({ since: sinceDate.toISOString(), until: null });
  };

  const applyCustomRange = () => {
    const overrides: Record<string, string | null> = {};
    overrides.since = customSince
      ? new Date(customSince).toISOString()
      : null;
    overrides.until = customUntil
      ? new Date(customUntil).toISOString()
      : null;
    setParam(overrides);
    setCustomOpen(false);
  };

  return (
    <div className="email-filters-bar" role="region" aria-label="Filtros">
      <button
        type="button"
        className={`email-filter-chip${hasUnread ? " is-active" : ""}`}
        onClick={() =>
          setParam({ has_unread: hasUnread ? null : "true" })
        }
      >
        <Eye size={12} aria-hidden />
        No leídos
      </button>

      <div className="email-filter-group" aria-label="Rango de fechas">
        <Calendar size={12} aria-hidden className="email-filter-icon" />
        {(
          [
            ["today", "Hoy"],
            ["week", "Última semana"],
            ["month", "Último mes"],
            ["all", "Todas"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`email-filter-chip${activeRange === key ? " is-active" : ""}`}
            onClick={() => applyRange(key)}
          >
            {label}
          </button>
        ))}
        <button
          type="button"
          className={`email-filter-chip${activeRange === "custom" ? " is-active" : ""}`}
          onClick={() => setCustomOpen((v) => !v)}
        >
          Personalizado…
        </button>
        {customOpen ? (
          <div className="email-filter-custom-range">
            <label>
              Desde
              <input
                type="date"
                value={customSince}
                onChange={(e) => setCustomSince(e.target.value)}
              />
            </label>
            <label>
              Hasta
              <input
                type="date"
                value={customUntil}
                onChange={(e) => setCustomUntil(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="btn btn-primary small"
              onClick={applyCustomRange}
            >
              Aplicar
            </button>
            <button
              type="button"
              className="btn small"
              onClick={() => setCustomOpen(false)}
            >
              Cancelar
            </button>
          </div>
        ) : null}
      </div>

      {(params.get("folder_id") ||
        params.get("label_id") ||
        params.get("starred") ||
        params.get("has_unread") ||
        params.get("since") ||
        params.get("until")) ? (
        <button
          type="button"
          className="email-filter-clear"
          onClick={() =>
            setParam({
              folder_id: null,
              label_id: null,
              starred: null,
              has_unread: null,
              since: null,
              until: null,
              state: null,
            })
          }
        >
          <X size={12} aria-hidden /> Limpiar filtros
        </button>
      ) : null}
    </div>
  );
}
