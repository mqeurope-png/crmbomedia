"use client";

import Link from "next/link";
import type { FormSubmissionRow } from "../../lib/formsApi";

/** Tabla de submits recibidos de un formulario. Presentacional (sin fetch)
 *  para poder testearla directa. La columna «Contacto» muestra, con badge,
 *  qué le pasó al contacto en cada submit (v3 Bug 4): nuevo / actualizado /
 *  spam / — (submits pre-migración, sin dato). */
export function SubmissionsList({ rows }: { rows: FormSubmissionRow[] }) {
  return (
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
              <ContactActionBadge action={s.contact_action} />
              {s.contact_id ? (
                <Link href={`/contacts/${s.contact_id}`} className="wf-sub-contact-link">
                  Ver contacto
                </Link>
              ) : null}
            </td>
            <td>
              {s.is_spam ? (
                <span className="badge bad">spam: {s.spam_reason}</span>
              ) : (
                <span className="badge ok">ok</span>
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
  );
}

function ContactActionBadge({
  action,
}: {
  action: FormSubmissionRow["contact_action"];
}) {
  if (action === "created") {
    return <span className="badge ok wf-sub-action">🟢 Nuevo</span>;
  }
  if (action === "updated") {
    return <span className="badge warn wf-sub-action">🟡 Actualizado</span>;
  }
  if (action === "spam") {
    return <span className="badge bad wf-sub-action">🔴 Spam</span>;
  }
  return <span className="badge muted wf-sub-action" aria-label="Sin dato">—</span>;
}
