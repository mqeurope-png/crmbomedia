"use client";

import { AlertTriangle, ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import {
  disconnectGoogle,
  getGoogleStatus,
  listGoogleCalendars,
  selectGoogleCalendar,
  type GoogleCalendarItem,
  type GoogleStatus,
} from "../../lib/googleApi";
import { extractErrorMessage } from "../../lib/errors";

/** Post-OAuth setup screen.
 *
 * Reached automatically after the Google consent screen (the backend
 * redirects here on /callback success). Lets the user pick which
 * calendar to sync with — and, before they confirm a new one, warns
 * about events already in the previously selected calendar. */
export default function GoogleSetupPage() {
  const router = useRouter();
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [calendars, setCalendars] = useState<GoogleCalendarItem[]>([]);
  const [picked, setPicked] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([
        getGoogleStatus(),
        listGoogleCalendars(),
      ]);
      setStatus(s);
      setCalendars(list);
      const initial =
        s.selected_calendar?.id ??
        list.find((c) => c.primary)?.id ??
        list[0]?.id ??
        "";
      setPicked(initial);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el setup."));
    }
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  async function handleConfirm(event: React.FormEvent) {
    event.preventDefault();
    if (!picked || submitting) return;
    const currentId = status?.selected_calendar?.id;
    if (
      currentId &&
      currentId !== picked &&
      !window.confirm(
        "Los eventos ya sincronizados seguirán en el calendario anterior. Solo las tareas nuevas se crearán en el calendario seleccionado. ¿Confirmar el cambio?",
      )
    ) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await selectGoogleCalendar(picked);
      router.push("/account");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la selección."));
      setSubmitting(false);
    }
  }

  async function handleCancel() {
    if (
      !window.confirm(
        "¿Cancelar y desconectar la cuenta de Google? Tendrás que volver a autorizar para sincronizar.",
      )
    ) {
      return;
    }
    setSubmitting(true);
    try {
      await disconnectGoogle();
      router.push("/account");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo desconectar."));
      setSubmitting(false);
    }
  }

  return (
    <main className="shell narrow">
      <PageHeader
        title="Sincronización con Google Calendar"
        eyebrow="Cuenta · Google"
        actions={
          <Link className="button small secondary" href="/account">
            <ArrowLeft size={11} aria-hidden /> Volver
          </Link>
        }
      />

      {loading ? (
        <p className="muted">Cargando calendarios…</p>
      ) : error && !status ? (
        <div className="error-state">{error}</div>
      ) : status && !status.connected ? (
        <div className="error-state">
          La cuenta no está conectada. Vuelve a <Link href="/account">/account</Link>{" "}
          e inicia la conexión.
        </div>
      ) : (
        <article className="form-card">
          {status?.google_email ? (
            <p className="muted small">
              Conectaste tu cuenta <strong>{status.google_email}</strong>.
            </p>
          ) : null}
          <p>
            Selecciona el calendario donde se sincronizarán tus tareas.
          </p>
          {error ? <p className="form-error">{error}</p> : null}
          <form onSubmit={handleConfirm}>
            <ul className="calendar-picker">
              {calendars.map((cal) => (
                <li key={cal.id}>
                  <label>
                    <input
                      type="radio"
                      name="calendar"
                      value={cal.id}
                      checked={picked === cal.id}
                      onChange={() => setPicked(cal.id)}
                    />
                    <span>
                      {cal.summary}
                      {cal.primary ? (
                        <span className="muted small"> · primario</span>
                      ) : null}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
            {status?.selected_calendar?.id && status.selected_calendar.id !== picked ? (
              <p className="form-warning">
                <AlertTriangle size={11} aria-hidden /> Los eventos ya
                sincronizados seguirán en el calendario anterior. Solo las
                tareas nuevas se crearán en el calendario seleccionado.
              </p>
            ) : null}
            <div className="actions">
              <button
                type="submit"
                className="button"
                disabled={!picked || submitting}
              >
                {submitting ? "Guardando…" : "Confirmar y empezar a sincronizar"}
              </button>
              <button
                type="button"
                className="button secondary"
                onClick={handleCancel}
                disabled={submitting}
              >
                Cancelar y desconectar
              </button>
            </div>
          </form>
        </article>
      )}
    </main>
  );
}
