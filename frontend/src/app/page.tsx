"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { EmailActivityWidget } from "./components/dashboard/EmailActivityWidget";
import { EmailTrackingStatsWidget } from "./components/dashboard/EmailTrackingStatsWidget";
import { GoogleEventsWidget } from "./components/dashboard/GoogleEventsWidget";
import { LeadsStatsWidget } from "./components/dashboard/LeadsStatsWidget";
import { PipelineSummaryWidget } from "./components/dashboard/PipelineSummaryWidget";
import { TasksWidget } from "./components/dashboard/TasksWidget";
import { UnattendedLeadsWidget } from "./components/dashboard/UnattendedLeadsWidget";
import { ErrorState } from "./components/ErrorState";
import { PageHeader } from "./components/PageHeader";
import { getCurrentUser, type User } from "./lib/api";
import { extractErrorMessage } from "./lib/errors";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** Dashboard.
 *
 * Fase 3 redesign. Six widgets in a responsive grid: tasks, Google
 * Calendar events, pipeline summary, unattended leads, lead stats
 * chart, recent email activity. Each widget owns its own fetch so a
 * slow endpoint doesn't block the rest. */
export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch((err) =>
        setError(
          extractErrorMessage(err, "Arranca la API o inicia sesión de nuevo."),
        ),
      )
      .finally(() => setIsLoading(false));
  }, []);

  if (isLoading) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Dashboard" eyebrow="CRM" />
        <p className="muted">Cargando CRM…</p>
      </main>
    );
  }

  if (error) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Dashboard" eyebrow="CRM" />
        <ErrorState title="No se pudo cargar el CRM" message={error} />
      </main>
    );
  }

  const isAdmin = user?.role === "admin";
  const canCreate = user?.role !== "viewer";

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Dashboard"
        eyebrow="CRM"
        description={
          user
            ? `Hola, ${user.full_name.split(" ")[0] || user.full_name}.`
            : undefined
        }
        actions={
          <>
            {canCreate ? (
              <Link href="/contacts/new" className="button small">
                + Nuevo contacto
              </Link>
            ) : null}
            {isAdmin ? (
              <a
                href={`${apiBaseUrl}/api/docs`}
                className="button secondary small"
                target="_blank"
                rel="noreferrer"
              >
                OpenAPI
              </a>
            ) : null}
          </>
        }
      />

      <section className="dashboard-grid">
        <TasksWidget />
        <GoogleEventsWidget />
        <PipelineSummaryWidget />
        <UnattendedLeadsWidget currentUserId={user?.id ?? null} />
        <LeadsStatsWidget />
        <EmailTrackingStatsWidget />
        <EmailActivityWidget />
      </section>
    </main>
  );
}
