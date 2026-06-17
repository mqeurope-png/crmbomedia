"use client";

import { Calendar, Mail, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { DashboardKpis, type DashboardRange } from "./components/dashboard/DashboardKpis";
import { EmailActivityWidget } from "./components/dashboard/EmailActivityWidget";
import { EmailTrackingStatsWidget } from "./components/dashboard/EmailTrackingStatsWidget";
import { PipelineSummaryWidget } from "./components/dashboard/PipelineSummaryWidget";
import { PriorityLeadsWidget } from "./components/dashboard/PriorityLeadsWidget";
import { RecentInteractionsWidget } from "./components/dashboard/RecentInteractionsWidget";
import { TasksWidget } from "./components/dashboard/TasksWidget";
import { UpcomingTasksWidget } from "./components/dashboard/UpcomingTasksWidget";
import { UserCampaignStatsWidget } from "./components/dashboard/UserCampaignStatsWidget";
import { ErrorState } from "./components/ErrorState";
import { getCurrentUser, getStoredToken, type User } from "./lib/api";
import { extractErrorMessage } from "./lib/errors";

export default function Home() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);
  // PR-F fix: empezamos en "auth-checking" en vez de "loading". Si no
  // hay cookie/token, redirigimos a /welcome ANTES de renderizar
  // cualquier shell, sin tocar la API. Sólo cuando hay token se hace
  // el fetch del usuario.
  const [isLoading, setIsLoading] = useState(true);
  const [range, setRange] = useState<DashboardRange>("today");

  useEffect(() => {
    // 1) Sin token en cliente → redirect inmediato, no API call.
    const token = getStoredToken();
    if (!token) {
      router.replace("/welcome");
      return;
    }
    // 2) Hay token (o cookie). Validamos contra el backend; CUALQUIER
    //    fallo (incluido 401 con cookie caducada / inválida) redirige
    //    a /welcome. Antes el regex sobre el mensaje no matchaba
    //    "Invalid authentication credentials" y el operador veía la
    //    shell del CRM con un banner de error en lugar del splash.
    getCurrentUser()
      .then((u) => {
        if (u) {
          setUser(u);
          setIsLoading(false);
          return;
        }
        // Defensa extra — no debería pasar, pero por si acaso.
        router.replace("/welcome");
      })
      .catch((err) => {
        const message = extractErrorMessage(
          err,
          "No se pudo validar la sesión.",
        );
        // Mensajes de transporte (CORS, DNS, network error sin status)
        // se quedan visibles para el operador; cualquier respuesta del
        // backend (incluida 401) tira a /welcome.
        if (/network|fetch failed|aborted/i.test(message)) {
          setError(message);
          setIsLoading(false);
          return;
        }
        router.replace("/welcome");
      });
  }, [router]);

  if (isLoading) {
    // Pantalla vacía mientras decidimos. Sin shell del CRM ni topbar
    // para no leak'ear UI a un visitante no autenticado.
    return null;
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
        <PriorityLeadsWidget />
        <EmailActivityWidget />
        <EmailTrackingStatsWidget />
        <UpcomingTasksWidget />
        <UserCampaignStatsWidget />
        <RecentInteractionsWidget />
      </section>
    </main>
  );
}
