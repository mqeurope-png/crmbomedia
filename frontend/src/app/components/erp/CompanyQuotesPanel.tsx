"use client";

import { useCallback, useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  convertFactusolQuoteToOrder,
  listFactusolQuotes,
  type FactusolQuote,
  type PaymentIntentInput,
} from "../../lib/erpApi";
import { ConvertQuoteDialog } from "./ConvertQuoteDialog";
import { CreateQuoteModal } from "./CreateQuoteModal";
import { albaranSummary, conversionNotice, pollQuoteJob } from "./quoteJobs";
import { QuotesTable } from "./QuotesTable";

export { albaranSummary };

/** Pestaña «Proformas FACTUSOL» de la ficha de empresa (C-4).
 *
 *  Las escrituras van por la cola serializada, así que aquí se encola y se
 *  hace polling del job hasta que termina — el mismo contrato que la emisión
 *  de facturas. Fase 2: «Convertir en pedido» pasa por el paso de pago
 *  (opción B) y crea el albarán en FACTUSOL en el mismo job. Fase 4: el
 *  diálogo de conversión y el polling son los mismos que en la pantalla
 *  Proformas. */
export function CompanyQuotesPanel({
  companyId,
  companyName,
  factusolCodcli,
  onOrderCreated,
  createSignal = 0,
}: {
  companyId: string;
  companyName: string;
  factusolCodcli: string | null;
  onOrderCreated?: (orderId: string) => void;
  /** Fase 3: «Nueva proforma» desde la cabecera de la ficha de empresa. */
  createSignal?: number;
}) {
  const [quotes, setQuotes] = useState<FactusolQuote[]>([]);
  const [unlinked, setUnlinked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // CODPRE de la proforma que se está editando (C-4-fix6).
  const [editing, setEditing] = useState<string | null>(null);
  const [busyJob, setBusyJob] = useState(false);
  // Fase 2: proforma pendiente de confirmar el pago antes de convertir.
  const [converting, setConverting] = useState<FactusolQuote | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listFactusolQuotes({ company_id: companyId, days_back: 365 })
      .then((r) => {
        setQuotes(r.items);
        setUnlinked(r.unlinked);
      })
      .catch((e) => setError(extractErrorMessage(e, "No se pudieron cargar las proformas.")))
      .finally(() => setLoading(false));
  }, [companyId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (createSignal > 0 && factusolCodcli) setCreating(true);
  }, [createSignal, factusolCodcli]);

  /** Espera a que el job termine. Devuelve su resultado, o null si falló. */
  const waitForJob = useCallback(async (jobId: string) => {
    const outcome = await pollQuoteJob(jobId);
    if (outcome.status === "finished") return outcome.result;
    if (outcome.status === "failed") {
      setError(outcome.error);
      return null;
    }
    setNotice("Sigue en curso; actualiza en unos segundos.");
    return null;
  }, []);

  async function onCreated(jobId: string) {
    setCreating(false);
    setEditing(null);
    setBusyJob(true);
    setNotice("Creando la proforma en FACTUSOL…");
    setError(null);
    const result = await waitForJob(jobId);
    setBusyJob(false);
    if (result) setNotice(`Proforma nº ${result.codpre} creada.`);
    load();
  }

  async function convert(codpre: string, payment: PaymentIntentInput) {
    setConverting(null);
    setBusyJob(true);
    setError(null);
    setNotice("Creando el pedido y el albarán en FACTUSOL…");
    try {
      const r = await convertFactusolQuoteToOrder(codpre, { payment, create_albaran: true });
      const result = await waitForJob(r.job_id);
      if (result) {
        setNotice(conversionNotice(codpre, result, payment.paid));
        if (typeof result.order_id === "string") onOrderCreated?.(result.order_id);
      }
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo convertir la proforma."));
    } finally {
      setBusyJob(false);
    }
  }

  if (unlinked || !factusolCodcli) {
    return (
      <section className="erp-card" aria-label="Proformas FACTUSOL">
        <h3>Proformas FACTUSOL</h3>
        <p className="muted small">
          Esta empresa no está vinculada a un cliente de FACTUSOL. Vincúlala en
          la sección FACTUSOL para poder crear proformas.
        </p>
      </section>
    );
  }

  return (
    <section className="erp-card" aria-label="Proformas FACTUSOL">
      <div className="sat-queue-head">
        <h3>Proformas FACTUSOL</h3>
        <button type="button" className="button small" disabled={busyJob}
                onClick={() => setCreating(true)}>
          Nueva proforma
        </button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-info" role="status">{notice}</p> : null}

      {loading ? (
        <p className="muted">Cargando…</p>
      ) : (
        /* La misma tabla que usa el buscador de proformas del alta de pedido. */
        <QuotesTable
          quotes={quotes}
          emptyText="Sin proformas en el último año."
          actions={(q) => (
            <>
              <button type="button" className="button small secondary"
                      disabled={busyJob}
                      onClick={() => setEditing(q.codpre ?? "")}>
                Editar
              </button>
              <button type="button" className="button small secondary"
                      disabled={busyJob}
                      onClick={() => setConverting(q)}>
                Convertir en pedido
              </button>
            </>
          )}
        />
      )}

      {creating || editing ? (
        <CreateQuoteModal
          companyId={companyId}
          companyName={companyName}
          factusolCodcli={factusolCodcli}
          editCodpre={editing}
          onCreated={onCreated}
          onCancel={() => { setCreating(false); setEditing(null); }}
        />
      ) : null}

      {converting ? (
        <ConvertQuoteDialog
          quote={converting}
          onCancel={() => setConverting(null)}
          onConfirm={(payment) => void convert(converting.codpre ?? "", payment)}
        />
      ) : null}
    </section>
  );
}
