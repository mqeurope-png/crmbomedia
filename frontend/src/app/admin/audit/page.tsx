"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { exportAuditLogs, getAuditLogs, getCurrentUser, type AuditLog } from "../../lib/api";

export default function AuditLogsPage() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      const currentUser = await getCurrentUser();
      if (currentUser.role !== "admin") throw new Error("No tienes permisos de administrador");
      setLogs(await getAuditLogs());
    }
    load()
      .catch((err) => setError(err instanceof Error ? err.message : "No se pudo cargar auditoría"))
      .finally(() => setIsLoading(false));
  }, []);

  async function onExport(format: "csv" | "json") {
    try {
      const blob = await exportAuditLogs(format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `audit_logs.${format}`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo exportar auditoría");
    }
  }

  return (
    <main className="shell">
      <Link href="/" className="back-link">← Volver al dashboard</Link>
      <section className="hero compact">
        <p className="eyebrow">Administración</p>
        <h1>Auditoría</h1>
        <div className="actions">
          <button className="button secondary" type="button" onClick={() => onExport("csv")}>Exportar CSV</button>
          <button className="button secondary" type="button" onClick={() => onExport("json")}>Exportar JSON</button>
        </div>
      </section>
      {isLoading ? <p className="muted">Cargando auditoría...</p> : null}
      {error ? <ErrorState title="Error de auditoría" message={error} /> : null}
      {!error ? (
        <section className="card">
          <h2>Eventos recientes</h2>
          <div className="table-scroll">
            <table>
              <thead><tr><th>Fecha</th><th>Acción</th><th>Entidad</th><th>ID</th><th>Mensaje</th></tr></thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id}>
                    <td>{new Date(log.created_at).toLocaleString()}</td>
                    <td>{log.action}</td>
                    <td>{log.entity_type}</td>
                    <td>{log.entity_id ?? "—"}</td>
                    <td>{log.message ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </main>
  );
}
