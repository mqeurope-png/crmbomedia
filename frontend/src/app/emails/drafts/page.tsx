"use client";

import { PenLine } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { EmailComposerModal } from "../../components/EmailComposerModal";
import {
  type EmailDraft,
  getEmailDraft,
} from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";

/** Right-pane view for `/emails/drafts`. The middle column owns the
 *  list (see `DraftListPanel`); here we just hydrate the draft
 *  selected via the `?id=` URL param and open the composer overlay.
 *  An empty / no-selection state is shown as the default placeholder
 *  so the right pane never goes blank. */
export default function DraftsPage() {
  const router = useRouter();
  const params = useSearchParams();
  const id = params.get("id");

  const [draft, setDraft] = useState<EmailDraft | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const clearSelection = useCallback(() => {
    const sp = new URLSearchParams(params.toString());
    sp.delete("id");
    const qs = sp.toString();
    router.replace(qs ? `/emails/drafts?${qs}` : "/emails/drafts");
  }, [params, router]);

  useEffect(() => {
    if (!id) {
      setDraft(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getEmailDraft(id)
      .then((d) => {
        if (!cancelled) setDraft(d);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            extractErrorMessage(err, "No se pudo cargar el borrador."),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <div className="email-thread-view">
      <header className="email-thread-actions">
        <div className="email-thread-actions-title">
          <h2>
            <PenLine size={18} aria-hidden /> Borradores
          </h2>
          <p className="muted small">
            Selecciona un borrador de la lista para continuar
            redactándolo. Cada compose se autoguarda cada 5 segundos.
          </p>
        </div>
      </header>

      {error ? <p className="form-error">{error}</p> : null}
      {loading ? <p className="muted">Cargando borrador…</p> : null}

      {draft ? (
        <div
          className="email-compose-overlay"
          role="presentation"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) clearSelection();
          }}
        >
          <EmailComposerModal
            initialDraft={draft}
            contactId={draft.contact_id}
            onClose={clearSelection}
            onSent={clearSelection}
          />
        </div>
      ) : null}
    </div>
  );
}
