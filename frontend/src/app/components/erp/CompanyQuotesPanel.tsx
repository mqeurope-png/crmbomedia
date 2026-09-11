"use client";

import { useCallback, useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  convertFactusolQuoteToOrder,
  getQuoteJobStatus,
  listFactusolQuotes,
  type FactusolQuote,
  type PaymentIntentInput,
} from "../../lib/erpApi";
import { CreateQuoteModal } from "./CreateQuoteModal";
import { initialPayment, paymentReady, PaymentStep } from "./PaymentStep";
import { QuotesTable } from "./QuotesTable";

const POLL_MS = 2000;
const POLL_MAX_TRIES = 30;  // ~60 s: el worker es serie, puede haber cola

/** Texto del albarán tal como lo devuelve el job de conversión (Fase 2). */
export function albaranSummary(result: Record<string, unknown>): string {
  const alb = result.albaran as { numero?: string; status?: string } | null | undefined;
  if (alb?.numero) {
    return alb.status === "already" || alb.status === "linked"
      ? `Albarán FACTUSOL ${alb.numero} (ya existía).`
      : `Albarán FACTUSOL ${alb.numero} creado.`;
  }
  if (typeof result.albaran_error === "string" && result.albaran_error) {
    return `El albarán NO se creó: ${result.albaran_error} Reintenta desde la ficha del pedido.`;
  }
  if (typeof result.albaran_skipped === "string" && result.albaran_skipped) {
    return `Sin albarán: ${result.albaran_skipped}`;
  }
  return "";
}

/** Pestaña «Proformas FACTUSOL» de la ficha de empresa (C-4).
 *
 *  Las escrituras van por la cola serializada, así que aquí se encola y se
 *  hace polling del job hasta que termina — el mismo contrato que la emisión
 *  de facturas. Fase 2: «Convertir en pedido» pasa por el paso de pago
 *  (opción B) y crea el albarán en FACTUSOL en el mismo job. */
export function CompanyQuotesPanel({
  companyId,
  companyName,
  factusolCodcli,
  onOrderCreated,
}: {
  companyId: string;
  companyName: string;
  factusolCodcli: string | null;
  onOrderCreated?: (orderId: string) => void;
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
  const [payment, setPayment] = useState<PaymentIntentInput>(initialPayment());

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

  /** Espera a que el job termine. Devuelve su resultado, o null si falló. */
  const waitForJob = useCallback(async (jobId: string) => {
    for (let i = 0; i < POLL_MAX_TRIES; i++) {
      const s = await getQuoteJobStatus(jobId);
      if (s.status === "finished") return s.result;
      if (s.status === "failed") {
        setError(s.error || "La operación falló en FACTUSOL.");
        return null;
      }
      await new Promise((r) => setTimeout(r, POLL_MS));
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

  function openConvert(q: FactusolQuote) {
    setPayment(initialPayment());
    setConverting(q);
  }

  async function convert(codpre: string) {
    setConverting(null);
    setBusyJob(true);
    setError(null);
    setNotice("Creando el pedido y el albarán en FACTUSOL…");
    try {
      const r = await convertFactusolQuoteToOrder(codpre, { payment, create_albaran: true });
      const result = await waitForJob(r.job_id);
      if (result) {
        const pago = payment.paid
          ? " Pago apuntado (el cobro se registra al emitir la factura)."
          : " Sin pago: pendiente.";
        setNotice(
          `Pedido ${result.order_number} creado desde la proforma ${codpre}. `
          + albaranSummary(result) + pago,
        );
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
                      onClick={() => openConvert(q)}>
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
        <div className="modal-overlay" role="dialog" aria-modal="true"
             aria-label="Convertir proforma en pedido">
          <div className="modal-dialog">
            <h2>Convertir la proforma {converting.codpre} en pedido</h2>
            <p className="muted small">
              {converting.referencia || "Sin referencia"} · {converting.total.toFixed(2)} €.
              Se creará el pedido en BoHub y su <strong>albarán en FACTUSOL</strong>
              {" "}(sin factura).
            </p>
            <PaymentStep value={payment} onChange={setPayment} />
            <div className="modal-actions">
              <button type="button" className="button secondary"
                      onClick={() => setConverting(null)}>
                Cancelar
              </button>
              <button type="button" className="button"
                      disabled={!paymentReady(payment)}
                      onClick={() => convert(converting.codpre ?? "")}>
                Crear pedido y albarán
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
