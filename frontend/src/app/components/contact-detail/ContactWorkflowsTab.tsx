"use client";

import { CircleX, Workflow as WorkflowIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  cancelWorkflowRun,
  listContactWorkflowRuns,
  type WorkflowRunRead,
} from "../../lib/workflowsApi";
import { formatBackendDateTime } from "../../lib/dates";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  contactId: string;
  canManage: boolean;
};

/** Pestaña "Workflows" dentro de la ficha del contacto.
 *
 *  Lista los workflow runs en los que el contacto ha estado o está. Si
 *  el user puede gestionar (admin/manager), añade botón de cancelar
 *  manual. */
export function ContactWorkflowsTab({ contactId, canManage }: Props) {
  const [runs, setRuns] = useState<WorkflowRunRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRuns(await listContactWorkflowRuns(contactId));
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudieron cargar los workflows."),
      );
    } finally {
      setLoading(false);
    }
  }, [contactId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <p className="muted">Cargando…</p>;
  if (error) return <p className="form-error">{error}</p>;
  if (runs.length === 0) {
    return (
      <p className="muted">
        <WorkflowIcon size={14} aria-hidden /> Este contacto no ha estado en
        ningún workflow.
      </p>
    );
  }

  return (
    <table className="workflow-runs-table">
      <thead>
        <tr>
          <th>Workflow</th>
          <th>Estado</th>
          <th>Iniciado</th>
          <th>Terminado</th>
          {canManage ? <th /> : null}
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <tr key={r.id}>
            <td>
              <Link href={`/admin/workflows/${r.workflow_id}`}>
                {r.workflow_name ?? r.workflow_id}
              </Link>
            </td>
            <td>
              <span className={`badge ${badgeClass(r.state)}`}>{r.state}</span>
              {r.exit_kind ? (
                <span className="muted small"> · {r.exit_kind}</span>
              ) : null}
            </td>
            <td>{formatBackendDateTime(r.started_at)}</td>
            <td>
              {r.completed_at ? formatBackendDateTime(r.completed_at) : "—"}
            </td>
            {canManage ? (
              <td>
                {r.state === "running" ||
                r.state === "waiting" ||
                r.state === "waiting_for_event" ? (
                  <button
                    type="button"
                    className="button secondary small"
                    onClick={async () => {
                      if (!confirm("¿Cancelar este run?")) return;
                      try {
                        await cancelWorkflowRun(r.id);
                        await load();
                      } catch (err) {
                        setError(
                          extractErrorMessage(err, "No se pudo cancelar."),
                        );
                      }
                    }}
                  >
                    <CircleX size={11} aria-hidden /> Cancelar
                  </button>
                ) : null}
              </td>
            ) : null}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function badgeClass(state: string): string {
  if (state === "running" || state === "waiting" || state === "waiting_for_event") {
    return "active";
  }
  if (state === "completed") return "ok";
  if (state === "cancelled") return "muted";
  if (state === "failed") return "warn";
  return "muted";
}
