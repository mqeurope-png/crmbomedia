"use client";

import { Calendar, Mail, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { DashboardKpis, type DashboardRange } from "./components/dashboard/DashboardKpis";
import { EmailActivityWidget } from "./components/dashboard/EmailActivityWidget";
import { EmailTrackingStatsWidget } from "./components/dashboard/EmailTrackingStatsWidget";
import { GoogleEventsWidget } from "./components/dashboard/GoogleEventsWidget";
import { HotOpportunitiesWidget } from "./components/dashboard/HotOpportunitiesWidget";
import { PipelineSummaryWidget } from "./components/dashboard/PipelineSummaryWidget";
import { RecentInteractionsWidget } from "./components/dashboard/RecentInteractionsWidget";
import { TasksWidget } from "./components/dashboard/TasksWidget";
import { UnattendedLeadsWidget } from "./components/dashboard/UnattendedLeadsWidget";
import { ErrorState } from "./components/ErrorState";
import { getCurrentUser, type User } from "./lib/api";
import { extractErrorMessage } from "./lib/errors";

/**
 * Dashboard BoHub (PR-C). Layout:
 *   - Header con saludo, selector temporal y dos acciones rápidas.
 *   - Tira de 6 KPIs (`DashboardKpis`).
 *   - Grid de 4 columnas × 2 filas con widgets reutilizados de Fase 3
 *     + 2 placeholders nuevos (Oportunidades calientes + Últimas
 *     interacciones) hasta que aterricen sus endpoints dedicados.
 *
 * El selector temporal pasa por props a `DashboardKpis` para que los
 * tiles de Leads / Emails se re-fetchen con el rango elegido. El
 * resto de widgets mantiene su propio control de rango (legacy) para
 * no romper la lógica que ya funciona.
 */
export default function Home() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [range, setRange] = useState<DashboardRange>("today");

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch((err) => {
        const message = extractErrorMessage(
          err,
          "Arranca la API o inicia sesión de nuevo.",
        );
        if (/401|no autenticado|unauthor/i.test(message)) {
          router.replace("/welcome");
          return;
        }
        setError(message);
      })
      .finally(() => setIsLoading(false));
  }, [router]);

  if (isLoading) {
    return (
      <main className="shell shell-wide dashboard-page">
        <p className="muted">Cargando CRM…</p>
      </main>
    );
  }

  if (error) {
    return (
      <main className="shell shell-wide dashboard-page">
        <ErrorState title="No se pudo cargar el CRM" message={error} />
      </main>
    );
  }

  const canCreate = user?.role !== "viewer";
  const firstName = user?.full_name?.split(" ")[0] || user?.full_name || "operador";

  return (
    <main className="shell shell-wide dashboard-page">
      <header className="dashboard-header">
        <div className="dashboard-header-greeting">
          <h1>
            Hola, {firstName} <span aria-hidden>👋</span>
          </h1>
          <p className="muted">Resumen comercial de hoy</p>
        </div>
        <div className="dashboard-header-controls">
          <div
            className="range-segment"
            role="radiogroup"
            aria-label="Rango temporal del resumen"
          >
            {(
              [
                { value: "today" as const, label: "Hoy" },
                { value: "7d" as const, label: "7 días" },
                { value: "30d" as const, label: "30 días" },
              ]
            ).map((opt) => (
              <button
                key={opt.value}
                type="button"
                role="radio"
                aria-checked={range === opt.value}
                className={`range-segment-item${
                  range === opt.value ? " is-active" : ""
                }`}
                onClick={() => setRange(opt.value)}
              >
                {opt.label}
              </button>
            ))}
            {/* Picker de fecha custom — placeholder. Bart lo pidió en el
                mockup pero sin endpoint todavía; el botón abre nada por
                ahora para no introducir un componente vacío. */}
            <button
              type="button"
              className="range-segment-item is-icon"
              aria-label="Personalizar rango"
              title="Próximamente"
              disabled
            >
              <Calendar size={14} aria-hidden />
            </button>
          </div>
          {canCreate ? (
            <Link href="/contacts/new" className="button">
              <Plus size={16} aria-hidden /> Nuevo contacto
            </Link>
          ) : null}
          <Link href="/emails" className="button secondary">
            <Mail size={16} aria-hidden /> Nuevo email
          </Link>
        </div>
      </header>

      <DashboardKpis range={range} />

      <section className="dashboard-widgets-grid">
        <TasksWidget />
        <PipelineSummaryWidget />
        <UnattendedLeadsWidget currentUserId={user?.id ?? null} />
        <EmailActivityWidget />
        <EmailTrackingStatsWidget />
        <GoogleEventsWidget />
        <HotOpportunitiesWidget />
        <RecentInteractionsWidget />
      </section>
    </main>
  );
}
