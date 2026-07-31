"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { ExceptionsTable } from "../../components/erp/ExceptionsTable";
import { getCurrentUser, type User } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import {
  assignException,
  listExceptions,
  resolveException,
  setExceptionStatus,
  type ErpExceptionRow,
} from "../../lib/erpApi";

export default function ErpExceptionsPage() {
  const [rows, setRows] = useState<ErpExceptionRow[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState("");
  const [assigned, setAssigned] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    listExceptions({ status: status || undefined, assigned: assigned || undefined })
      .then(setRows)
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la bandeja.")))
      .finally(() => setLoading(false));
  }, [status, assigned]);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
  }, []);
  useEffect(() => { load(); }, [load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo completar la acción."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Excepciones"
        eyebrow="ERP"
        description="Todo lo que bloquea el flujo, con acción de resolución."
        crumbs={[{ label: "ERP" }, { label: "Excepciones" }]}
      />
      <div className="wf-list-filters" style={{ marginBottom: 12, display: "flex", gap: 8 }}>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filtro estado">
          <option value="">Estado: todos</option>
          <option value="open">Abiertas</option>
          <option value="in_progress">En curso</option>
          <option value="resolved">Resueltas</option>
          <option value="dismissed">Descartadas</option>
        </select>
        <select value={assigned} onChange={(e) => setAssigned(e.target.value)} aria-label="Filtro asignación">
          <option value="">Asignación: todas</option>
          <option value="me">Asignadas a mí</option>
        </select>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="muted">No hay excepciones con este filtro. 🎉</p>
      ) : (
        <ExceptionsTable
          rows={rows}
          busy={busy}
          onAssignMe={(id) => user && act(() => assignException(id, user.id))}
          onMarkSeen={(id) => act(() => setExceptionStatus(id, "in_progress"))}
          onResolve={(id) => {
            const note = window.prompt("Nota de resolución:");
            if (note && note.trim()) act(() => resolveException(id, note.trim()));
          }}
        />
      )}
    </main>
  );
}
