"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  listFactusolDocuments,
  listFactusolQuotes,
  listOrders,
  type FactusolDocument,
  type FactusolQuote,
  type OrderSummary,
  type WorkflowQueue,
} from "../../lib/erpApi";

const MAX = 5;

/** Tono de la pastilla por cola de trabajo (los colores de la bandeja). */
const QUEUE_TONE: Record<WorkflowQueue, string> = {
  por_revisar: "a", por_facturar: "b", por_cobrar: "g", por_enviar: "p",
  incidencias: "r", listo: "n",
};

function fecha(iso: string | null | undefined): string {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return d && m ? `${Number(d)}/${Number(m)}/${y.slice(2)}` : iso;
}

/** «Actividad reciente» de la ficha de empresa (rediseño de flujo, Fase 3):
 *  los últimos pedidos (con su cola del `workflow`), facturas (con su estado
 *  de cobro) y proformas de la empresa, cada uno con su estado. Tres lecturas
 *  best-effort: si FACTUSOL no responde, se dice y los pedidos (que están en
 *  BoHub) salen igual. */
export function CompanyActivityPanel({
  companyId,
  factusolCodcli,
  contactsCount,
}: {
  companyId: string;
  factusolCodcli: string | null;
  contactsCount: number;
}) {
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);
  const [invoices, setInvoices] = useState<FactusolDocument[] | null>(null);
  const [quotes, setQuotes] = useState<FactusolQuote[] | null>(null);
  const [factusolError, setFactusolError] = useState(false);

  useEffect(() => {
    let alive = true;
    // Pedidos de BoHub (con su cola): siempre.
    Promise.resolve()
      .then(() => listOrders({ company_id: companyId, limit: MAX, show_external: true }))
      .then((r) => { if (alive) setOrders(r.items); })
      .catch(() => { if (alive) setOrders([]); });
    if (!factusolCodcli) {
      setInvoices([]);
      setQuotes([]);
      return () => { alive = false; };
    }
    Promise.resolve()
      .then(() => listFactusolDocuments("facturas", {
        codcli: factusolCodcli, sort: "fecha", dir: "desc", limit: MAX,
      }))
      .then((r) => { if (alive) setInvoices(r.items); })
      .catch(() => { if (alive) { setInvoices([]); setFactusolError(true); } });
    Promise.resolve()
      .then(() => listFactusolQuotes({ company_id: companyId, days_back: 365 }))
      .then((r) => {
        if (!alive) return;
        const sorted = [...r.items].sort((a, b) => (b.fecha ?? "").localeCompare(a.fecha ?? ""));
        setQuotes(sorted.slice(0, MAX));
      })
      .catch(() => { if (alive) { setQuotes([]); setFactusolError(true); } });
    return () => { alive = false; };
  }, [companyId, factusolCodcli]);

  const loading = orders === null || invoices === null || quotes === null;
  const empty = !loading && orders.length === 0 && invoices.length === 0 && quotes.length === 0;

  return (
    <section className="erp-flow-panel" aria-label="Actividad reciente">
      <h3>Actividad reciente</h3>
      {loading ? <p className="muted small">Cargando…</p> : null}
      {factusolError ? (
        <p className="muted small" role="status">
          FACTUSOL no responde: faltan las facturas y proformas.
        </p>
      ) : null}
      {orders?.map((o) => {
        const wf = o.workflow;
        return (
          <div className="erp-flow-kv" key={`o-${o.id}`}>
            <span>
              <Link href={`/erp/orders/${o.id}`}>Pedido {o.order_number}</Link>
              <span className="muted small"> · {fecha(o.placed_at)} · {o.total_amount.toFixed(2)} {o.currency}</span>
            </span>
            <span className="v">
              {wf ? (
                <span className={`erp-flow-pill is-${QUEUE_TONE[wf.queue] ?? "n"}`}>
                  {wf.queue_label.toLowerCase()}
                </span>
              ) : null}
            </span>
          </div>
        );
      })}
      {invoices?.map((f) => {
        const cobrada = f.saldo_pendiente != null && f.saldo_pendiente <= 0.005;
        const pendiente = f.saldo_pendiente != null && f.saldo_pendiente > 0.005;
        return (
          <div className="erp-flow-kv" key={`f-${f.numero}`}>
            <span>
              Factura {f.numero}
              <span className="muted small"> · {fecha(f.fecha)}{f.total != null ? ` · ${f.total.toFixed(2)} €` : ""}</span>
            </span>
            <span className="v">
              <span className={`erp-flow-pill is-${cobrada ? "g" : pendiente ? "a" : "n"}`}>
                {cobrada ? "cobrada" : pendiente ? "pend. cobro" : f.estado_label || "—"}
              </span>
            </span>
          </div>
        );
      })}
      {quotes?.map((q) => (
        <div className="erp-flow-kv" key={`q-${q.codpre}`}>
          <span>
            Proforma {q.codpre}
            <span className="muted small"> · {fecha(q.fecha)} · {q.total.toFixed(2)} €{q.referencia ? ` · ${q.referencia}` : ""}</span>
          </span>
          <span className="v"><span className="erp-flow-pill is-n">proforma</span></span>
        </div>
      ))}
      {empty ? <p className="muted small">Sin pedidos, facturas ni proformas recientes.</p> : null}
      <div className="erp-flow-kv">
        <span className="muted">Contactos</span>
        <span className="v">{contactsCount}</span>
      </div>
    </section>
  );
}
