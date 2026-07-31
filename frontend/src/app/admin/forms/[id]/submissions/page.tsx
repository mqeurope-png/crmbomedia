"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { PageHeader } from "../../../../components/PageHeader";
import { SubmissionsList } from "../../../../components/web-forms/SubmissionsList";
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
        <SubmissionsList rows={rows} />
      )}
    </main>
  );
}
