"use client";

import { History } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  getPerContactCandidates,
  queuePerContactBatch,
} from "../lib/gmailBackfillApi";
import { extractErrorMessage } from "../lib/errors";

/** PR-Auto-Backfill-Gmail-Por-Contacto. Banner admin que aparece tras
 *  un sync masivo: ofrece importar el histórico Gmail de los contactos
 *  nuevos que llegaron sin él (los bulk syncs NO lo disparan auto).
 *
 *  Se monta en /admin/integrations. Polla candidatos al montar; si hay
 *  >0 muestra el CTA. Tras encolar, cambia a estado "importando". */
export function PerContactBackfillBanner({
  onError,
}: {
  onError?: (msg: string) => void;
}) {
  const [count, setCount] = useState(0);
  const [contactIds, setContactIds] = useState<string[]>([]);
  const [queued, setQueued] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const res = await getPerContactCandidates(24);
      setCount(res.count);
      setContactIds(res.contact_ids);
    } catch (err) {
      onError?.(
        extractErrorMessage(err, "No se pudieron cargar los candidatos."),
      );
    } finally {
      setLoaded(true);
    }
  }, [onError]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleImport() {
    if (contactIds.length === 0 || submitting) return;
    setSubmitting(true);
    try {
      const res = await queuePerContactBatch(contactIds, 12);
      setQueued(res.queued);
    } catch (err) {
      onError?.(
        extractErrorMessage(err, "No se pudo encolar la importación."),
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (!loaded) return null;
  // Tras encolar mostramos el estado "importando" y ocultamos el CTA.
  if (queued !== null) {
    return (
      <div className="pcb-banner pcb-banner-progress">
        <History size={16} aria-hidden />
        <span>
          Importando histórico de Gmail de {queued} contacto
          {queued === 1 ? "" : "s"}. Puede tardar unos minutos — refresca
          las fichas en 5-10 min.
        </span>
      </div>
    );
  }
  if (count === 0) return null;

  return (
    <div className="pcb-banner">
      <History size={16} aria-hidden />
      <span>
        Se han creado <strong>{count}</strong> contacto
        {count === 1 ? "" : "s"} nuevo{count === 1 ? "" : "s"} sin histórico
        de Gmail importado.
      </span>
      <div style={{ flex: 1 }} />
      <button
        type="button"
        className="button small"
        onClick={handleImport}
        disabled={submitting}
      >
        {submitting
          ? "Encolando…"
          : "Importar histórico de últimos 12 meses"}
      </button>
    </div>
  );
}
