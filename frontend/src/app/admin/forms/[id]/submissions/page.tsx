"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { PageHeader } from "../../../../components/PageHeader";
import { extractErrorMessage } from "../../../../lib/errors";
import { getSubmissions, type FormSubmissionRow } from "../../../../lib/formsApi";

export default function FormSubmissionsPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [rows, setRows] = useState<FormSubmissionRow[]>([]);
  const [spamFilter, setSpamFilter] = useState<"all" | "clean" | "spam">("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    const is_spam = spamFilter === "all" ? undefined : spamFilter === "spam";
    getSubmissions(id, { is_spam, limit: 200 })
      .then((r) => setRows(r.items))
      .catch((err) => setError(extractErrorMessage(err, "No se pudieron cargar los submits.")))
      .finally(() => setLoading(false));
  }, [id, spamFilter]);

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Submits recibidos"
        eyebrow="Formularios"
        crumbs={[
          { label: "Formularios", href: "/admin/forms" },
          { label: "Submits" },
        ]}
      />
      <div className="wf-list-filters" style={{ marginBottom: 12 }}>
        <select value={spamFilter} onChange={(e) => setSpamFilter(e.target.value as "all" | "clean" | "spam")} aria-label="Filtro spam">
          <option value="all">Todos</option>
          <option value="clean">Solo válidos</option>
          <option value="spam">Solo spam</option>
        </select>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="muted">No hay submits con este filtro.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Contacto</th>
              <th>Estado</th>
              <th>UTM source</th>
              <th>IP</th>
              <th>Payload</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id}>
                <td className="muted small">{new Date(s.created_at).toLocaleString("es-ES")}</td>
                <td>
                  {s.contact_id ? (
                    <Link href={`/contacts/${s.contact_id}`}>Ver contacto</Link>
                  ) : (
                    <span className="muted small">—</span>
                  )}
                </td>
                <td>
                  {s.is_spam ? (
                    <span className="badge badge-danger">spam: {s.spam_reason}</span>
                  ) : (
                    <span className="badge">ok</span>
                  )}
                </td>
                <td className="muted small">{s.utm_source ?? "—"}</td>
                <td className="muted small">{s.ip_address ?? "—"}</td>
                <td className="muted small">
                  <code>{JSON.stringify(s.payload).slice(0, 80)}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
