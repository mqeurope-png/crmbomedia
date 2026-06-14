"use client";

import { CalendarClock, Send, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  type EmailMessage,
  cancelScheduledMessage,
  listScheduledMessages,
  updateScheduledMessage,
} from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Convert an ISO datetime string into a value the browser's
 *  `<input type="datetime-local">` understands (no timezone, no
 *  seconds). The picker rounds to the minute so we drop the rest. */
function toLocalInputValue(iso: string): string {
  const d = new Date(iso);
  const off = d.getTimezoneOffset();
  const local = new Date(d.getTime() - off * 60 * 1000);
  return local.toISOString().slice(0, 16);
}

/** Right-pane view for `/emails/programados`. Lists every pending
 *  scheduled message owned by the current operator with inline
 *  edit (only the time, to keep the UI tight) + cancel +
 *  send-now controls. */
export default function ScheduledMessagesPage() {
  const [items, setItems] = useState<EmailMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{
    id: string;
    value: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await listScheduledMessages());
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudieron cargar los programados."),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onCancel = async (id: string) => {
    if (!confirm("¿Cancelar este envío programado?")) return;
    setBusy(true);
    try {
      await cancelScheduledMessage(id);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cancelar."));
    } finally {
      setBusy(false);
    }
  };

  const onSendNow = async (id: string) => {
    setBusy(true);
    try {
      // Setting scheduled_for to "now-ish" makes the sweep pick it
      // up on its next tick; the backend's >now validation needs
      // a microscopic future, so we use +5s.
      const target = new Date(Date.now() + 5000).toISOString();
      await updateScheduledMessage(id, { scheduled_for: target });
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo forzar el envío."));
    } finally {
      setBusy(false);
    }
  };

  const onSaveEdit = async () => {
    if (!editing) return;
    setBusy(true);
    try {
      const iso = new Date(editing.value).toISOString();
      await updateScheduledMessage(editing.id, { scheduled_for: iso });
      setEditing(null);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar el cambio."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="email-thread-view">
      <header className="email-thread-actions">
        <div className="email-thread-actions-title">
          <h2>
            <CalendarClock size={18} aria-hidden /> Programados
          </h2>
          <p className="muted small">
            Emails que has programado para enviarse más tarde. Aún no han
            salido a Gmail.
          </p>
        </div>
      </header>

      {error ? <p className="form-error">{error}</p> : null}

      {loading ? (
        <p className="muted">Cargando…</p>
      ) : items.length === 0 ? (
        <p className="muted">No tienes ningún envío programado.</p>
      ) : (
        <ul className="email-scheduled-list">
          {items.map((m) => {
            const isEditing = editing?.id === m.id;
            return (
              <li key={m.id} className="email-scheduled-item">
                <div className="email-scheduled-meta">
                  <span className="email-scheduled-badge">
                    <CalendarClock size={11} aria-hidden /> Programado para{" "}
                    {formatDateTime(m.scheduled_for ?? null)}
                  </span>
                  <p className="email-scheduled-subject">
                    {m.subject || "(sin asunto)"}
                  </p>
                  <p className="muted small">
                    Para: {m.to_emails.join(", ")}
                  </p>
                  {m.snippet ? (
                    <p className="email-snippet">{m.snippet}</p>
                  ) : null}
                </div>
                <div className="email-scheduled-actions">
                  {isEditing ? (
                    <>
                      <input
                        type="datetime-local"
                        value={editing.value}
                        onChange={(e) =>
                          setEditing({ id: m.id, value: e.target.value })
                        }
                      />
                      <button
                        type="button"
                        className="btn btn-primary small"
                        onClick={onSaveEdit}
                        disabled={busy}
                      >
                        Guardar
                      </button>
                      <button
                        type="button"
                        className="btn small"
                        onClick={() => setEditing(null)}
                        disabled={busy}
                      >
                        Cancelar
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="btn small"
                        onClick={() =>
                          setEditing({
                            id: m.id,
                            value: m.scheduled_for
                              ? toLocalInputValue(m.scheduled_for)
                              : "",
                          })
                        }
                        disabled={busy}
                      >
                        Editar hora
                      </button>
                      <button
                        type="button"
                        className="btn small"
                        onClick={() => onSendNow(m.id)}
                        disabled={busy}
                        title="Enviar ahora"
                      >
                        <Send size={11} aria-hidden /> Enviar ahora
                      </button>
                      <button
                        type="button"
                        className="btn small"
                        onClick={() => onCancel(m.id)}
                        disabled={busy}
                        title="Cancelar"
                      >
                        <Trash2 size={11} aria-hidden /> Cancelar
                      </button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
