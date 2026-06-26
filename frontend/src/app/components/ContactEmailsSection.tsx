"use client";

import { History, Mail, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { formatBackendDateTime } from "../lib/dates";
import {
  listEmailThreads,
  type EmailThread,
} from "../lib/emailsApi";
import { queuePerContactBackfill } from "../lib/gmailBackfillApi";
import { extractErrorMessage } from "../lib/errors";

function formatDateTime(value: string): string {
  return formatBackendDateTime(value, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "Emails" tab inside the contact detail. Lists threads where the
 *  contact participates and offers a CTA to open the composer.
 *
 *  PR-Ficha-Cleanup: nuevo prop `refreshKey`. El bug reportado por
 *  Bart: si el operador estaba ya en la pestaña Emails y enviaba
 *  un correo desde el header, la tab no refetcheaba porque el dep
 *  `[contactId]` no cambiaba (el id sigue siendo el mismo). El
 *  parent ahora bumpea `refreshKey` tras `onSent` para forzar el
 *  refetch. */
export function ContactEmailsSection({
  contactId,
  contactEmail,
  onCompose,
  refreshKey = 0,
}: {
  contactId: string;
  contactEmail?: string | null;
  onCompose: () => void;
  refreshKey?: number;
}) {
  const [threads, setThreads] = useState<EmailThread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // PR-Auto-Backfill-Gmail-Por-Contacto. Estado del botón "Importar
  // histórico de Gmail".
  const [importing, setImporting] = useState(false);
  const [importNotice, setImportNotice] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    listEmailThreads(contactId)
      .then((page) => setThreads(page.items))
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudieron cargar los hilos.")),
      )
      .finally(() => setLoading(false));
  }, [contactId, refreshKey]);

  async function handleImportHistory() {
    if (importing) return;
    setImporting(true);
    setImportNotice(null);
    try {
      await queuePerContactBackfill(contactId, 12);
      setImportNotice(
        "Importando histórico de Gmail. Refresca la ficha en 1-2 min.",
      );
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudo importar el histórico."),
      );
    } finally {
      setImporting(false);
    }
  }

  return (
    <div>
      <div className="section-title">
        <h3>
          <Mail size={12} aria-hidden /> Emails
        </h3>
        <div style={{ display: "flex", gap: 6 }}>
          {contactEmail ? (
            <button
              type="button"
              className="button secondary small"
              onClick={handleImportHistory}
              disabled={importing}
              title="Buscar e importar conversaciones históricas de Gmail con este contacto (últimos 12 meses)"
            >
              <History size={11} aria-hidden />{" "}
              {importing ? "Importando…" : "Importar histórico Gmail"}
            </button>
          ) : null}
          <button type="button" className="button small" onClick={onCompose}>
            <Plus size={11} aria-hidden /> Nuevo
          </button>
        </div>
      </div>
      {importNotice ? (
        <p className="muted small" style={{ color: "#15803d" }}>
          {importNotice}
        </p>
      ) : null}
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : threads.length === 0 ? (
        <p className="muted small">Aún no has enviado emails a este contacto.</p>
      ) : (
        <ul className="widget-list">
          {threads.map((t) => (
            <li key={t.id} className="widget-row">
              <div className="widget-row-main">
                <p className="widget-row-title">
                  <Link href={`/emails/${t.id}`}>
                    {t.subject || "(sin asunto)"}
                  </Link>
                  {t.has_unread_replies ? (
                    <span className="badge ok"> Nuevo</span>
                  ) : null}
                </p>
                <p className="widget-row-meta muted small">
                  {formatDateTime(t.last_message_at)} · {t.message_count} mensaje
                  {t.message_count === 1 ? "" : "s"}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
