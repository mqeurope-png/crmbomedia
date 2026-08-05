"use client";

import { useCallback, useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  convertFactusolQuoteToOrder,
  getQuoteJobStatus,
  listFactusolQuotes,
  type FactusolQuote,
} from "../../lib/erpApi";
import { CreateQuoteModal } from "./CreateQuoteModal";

const POLL_MS = 2000;
const POLL_MAX_TRIES = 30;  // ~60 s: el worker es serie, puede haber cola

/** Pestaña «Proformas FACTUSOL» de la ficha de empresa (C-4).
 *
 *  Las escrituras van por la cola serializada, así que aquí se encola y se
 *  hace polling del job hasta que termina — el mismo contrato que la emisión
 *  de facturas. */
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

  async function convert(codpre: string) {
    setBusyJob(true);
    setError(null);
    setNotice("Creando el pedido…");
    try {
      const r = await convertFactusolQuoteToOrder(codpre);
      const result = await waitForJob(r.job_id);
      if (result) {
        setNotice(`Pedido ${result.order_number} creado desde la proforma ${codpre}.`);
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
      ) : quotes.length === 0 ? (
        <p className="muted small">Sin proformas en el último año.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Nº</th><th>Fecha</th><th>Referencia</th>
              <th>Total</th><th />
            </tr>
          </thead>
          <tbody>
            {quotes.map((q) => (
              <tr key={q.codpre ?? ""}>
                <td>{q.codpre}</td>
                <td className="muted small">{q.fecha ?? "—"}</td>
                <td>{q.referencia || "—"}</td>
                <td>{q.total.toFixed(2)} €</td>
                <td className="erp-quote-row-actions">
                  <button type="button" className="button small secondary"
                          disabled={busyJob}
                          onClick={() => setEditing(q.codpre ?? "")}>
                    Editar
                  </button>
                  <button type="button" className="button small secondary"
                          disabled={busyJob}
                          onClick={() => convert(q.codpre ?? "")}>
                    Convertir en pedido
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
    </section>
  );
}
