"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import {
  createGdprRequest,
  getCurrentUser,
  listGdprRequests,
  processGdprRequest,
  updateGdprRequest,
  type GdprProcessResult,
  type GdprRequest,
  type GdprRequestStatus,
  type GdprRequestType,
} from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

const TYPES: GdprRequestType[] = [
  "access",
  "rectification",
  "erasure",
  "portability",
  "objection",
];

const STATUSES: GdprRequestStatus[] = [
  "pending",
  "in_progress",
  "completed",
  "rejected",
];

const TYPE_LABEL: Record<GdprRequestType, string> = {
  access: "Acceso",
  rectification: "Rectificación",
  erasure: "Supresión",
  portability: "Portabilidad",
  objection: "Oposición",
};

const STATUS_LABEL: Record<GdprRequestStatus, string> = {
  pending: "Pendiente",
  in_progress: "En curso",
  completed: "Completada",
  rejected: "Rechazada",
};

export default function GdprPage() {
  const [requests, setRequests] = useState<GdprRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [subjectEmail, setSubjectEmail] = useState("");
  const [requestType, setRequestType] = useState<GdprRequestType>("access");
  const [notes, setNotes] = useState("");
  const [lastResult, setLastResult] = useState<GdprProcessResult | null>(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const rows = await listGdprRequests();
      setRequests(rows);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las solicitudes RGPD"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    async function bootstrap() {
      try {
        const me = await getCurrentUser();
        if (me.role !== "admin") {
          throw new Error("No tienes permisos de administrador");
        }
        await refresh();
      } catch (err) {
        setError(extractErrorMessage(err, "No se pudieron cargar las solicitudes RGPD"));
        setIsLoading(false);
      }
    }
    bootstrap();
  }, [refresh]);

  async function onCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    try {
      await createGdprRequest({
        subject_email: subjectEmail,
        request_type: requestType,
        notes: notes || null,
      });
      setSubjectEmail("");
      setNotes("");
      setMessage("Solicitud registrada");
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear la solicitud"));
    }
  }

  async function onProcess(request: GdprRequest) {
    if (request.status === "completed") return;
    const confirmed = window.confirm(
      `Procesar ${TYPE_LABEL[request.request_type]} para ${request.subject_email}? ` +
        (request.request_type === "erasure"
          ? "Esta acción elimina al contacto y anonimiza los registros de auditoría asociados."
          : "Esta acción es trazable en la auditoría."),
    );
    if (!confirmed) return;
    setError(null);
    setMessage(null);
    try {
      const result = await processGdprRequest(request.id);
      setLastResult(result);
      setMessage(`Solicitud ${request.id.slice(0, 8)}… procesada`);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo procesar la solicitud"));
    }
  }

  async function onStatusChange(request: GdprRequest, status: GdprRequestStatus) {
    setError(null);
    setMessage(null);
    try {
      await updateGdprRequest(request.id, { status });
      setMessage("Estado actualizado");
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo actualizar el estado"));
    }
  }

  return (
    <main className="shell">
      <PageHeader
        title="RGPD · Derechos del titular"
        eyebrow="Administración"
        description="Registro y procesamiento de solicitudes RGPD: acceso, rectificación, supresión, portabilidad y oposición. Cada acción se audita en gdpr.* dentro del registro de auditoría."
      />

      {error ? <ErrorState title="Error RGPD" message={error} /> : null}
      {message ? <div className="success-state">{message}</div> : null}

      <section className="card">
        <h2>Nueva solicitud</h2>
        <form className="form-card embedded" onSubmit={onCreate}>
          <label>
            Email del titular
            <input
              type="email"
              required
              value={subjectEmail}
              onChange={(event) => setSubjectEmail(event.target.value)}
              placeholder="titular@dominio.com"
            />
          </label>
          <label>
            Tipo
            <select
              value={requestType}
              onChange={(event) => setRequestType(event.target.value as GdprRequestType)}
            >
              {TYPES.map((type) => (
                <option key={type} value={type}>
                  {TYPE_LABEL[type]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Notas internas (opcional)
            <textarea
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="Canal de recepción, identificación verificada, etc."
            />
          </label>
          <button className="button" type="submit">Registrar solicitud</button>
        </form>
      </section>

      <section className="card">
        <div className="section-title">
          <h2>Solicitudes</h2>
          {isLoading ? <span className="muted">Cargando…</span> : null}
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Fecha</th>
                <th>Titular</th>
                <th>Tipo</th>
                <th>Estado</th>
                <th>Evidencia</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {requests.map((request) => (
                <tr key={request.id}>
                  <td>{new Date(request.requested_at).toLocaleString()}</td>
                  <td>{request.subject_email}</td>
                  <td>{TYPE_LABEL[request.request_type]}</td>
                  <td>
                    <select
                      value={request.status}
                      onChange={(event) =>
                        onStatusChange(request, event.target.value as GdprRequestStatus)
                      }
                    >
                      {STATUSES.map((status) => (
                        <option key={status} value={status}>
                          {STATUS_LABEL[status]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    {request.evidence_path ? (
                      <code className="audit-metadata">{request.evidence_path}</code>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    <button
                      className="button secondary small"
                      type="button"
                      disabled={request.status === "completed"}
                      onClick={() => onProcess(request)}
                    >
                      Procesar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {requests.length === 0 && !isLoading ? (
          <p className="muted">Sin solicitudes registradas todavía.</p>
        ) : null}
      </section>

      {lastResult ? (
        <section className="card">
          <h2>Último resultado</h2>
          <p className="muted">
            {TYPE_LABEL[lastResult.request_type]} · {STATUS_LABEL[lastResult.status]}
          </p>
          {lastResult.evidence_path ? (
            <p>
              Fichero generado:{" "}
              <code className="audit-metadata">{lastResult.evidence_path}</code>
            </p>
          ) : null}
          <pre className="audit-metadata">
            {JSON.stringify(lastResult.payload, null, 2)}
          </pre>
        </section>
      ) : null}
    </main>
  );
}
