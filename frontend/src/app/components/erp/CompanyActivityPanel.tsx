"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  fichaDocumentoHref,
  getEjercicios,
  listFactusolDocuments,
  listFactusolQuotes,
  listOrders,
  type FactusolDocument,
  type FactusolQuote,
  type OrderSummary,
  type QuoteQueue,
  type WorkflowQueue,
} from "../../lib/erpApi";
import { CreateQuoteModal } from "./CreateQuoteModal";
import { QuoteEmailModal, emailedMark } from "./QuoteEmailModal";
import { ActionsMenu } from "./flow/ActionsMenu";
import { pollQuoteJob } from "./quoteJobs";

/** Documentos que entran por cada origen (antes 5; ahora que es una tabla
 *  cabe más historia sin ocupar más). */
const MAX = 20;

export type ActivityKind = "pedido" | "factura" | "proforma";
export type ActivityFilter = "todo" | ActivityKind;

const FILTERS: { key: ActivityFilter; label: string }[] = [
  { key: "todo", label: "Todo" },
  { key: "pedido", label: "Pedidos" },
  { key: "factura", label: "Facturas" },
  { key: "proforma", label: "Proformas" },
];

const KIND_LABEL: Record<ActivityKind, string> = {
  pedido: "Pedido", factura: "Factura", proforma: "Proforma",
};

/** Tono de la pastilla por cola de trabajo, con el significado del sistema
 *  de diseño: verde = hecho, ámbar = pendiente, azul = informativo (por
 *  facturar), rojo = incidencia. */
const QUEUE_TONE: Record<WorkflowQueue, string> = {
  por_revisar: "a", por_facturar: "b", factura_por_enviar: "a", por_cobrar: "a",
  incidencias: "r", listo: "g",
};

/** Tono de la proforma por su cola (pantalla Proformas, Fase 4). */
const QUOTE_TONE: Record<QuoteQueue, string> = {
  aceptadas: "g", pendientes: "a", rechazadas: "r", convertidas: "n",
};

/** Una fila de la tabla de actividad, venga de donde venga. */
export type ActivityRow = {
  key: string;
  kind: ActivityKind;
  /** Nº visible del documento (pedido, factura o proforma). */
  numero: string;
  /** Página del documento; null = solo texto (las facturas FACTUSOL no
   *  tienen página propia y el explorador no admite abrir una por URL). */
  href: string | null;
  /** Referencia de la proforma, si la tiene. */
  detalle: string | null;
  fecha: string | null;
  importe: number | null;
  moneda: string;
  estado: string;
  tone: string;
  /** Punto B: la proforma entera, para «⋯ → Duplicar» (solo en proformas). */
  quote?: FactusolQuote;
};

function fecha(iso: string | null | undefined): string {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return d && m ? `${Number(d)}/${Number(m)}/${y.slice(2)}` : iso;
}

const NF = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Importe con dos decimales al estilo español y su moneda (EUR → €). */
export function importe(n: number | null | undefined, moneda = "EUR"): string {
  if (n == null) return "—";
  return `${NF.format(n)} ${!moneda || moneda === "EUR" ? "€" : moneda}`;
}

function orderRow(o: OrderSummary): ActivityRow {
  const wf = o.workflow;
  const estado = o.cancelled
    ? { estado: o.refunded ? "Reembolsado" : "Anulado", tone: "n" }
    : wf
      ? { estado: wf.queue_label, tone: QUEUE_TONE[wf.queue] ?? "n" }
      : { estado: "—", tone: "n" };
  return {
    key: `o-${o.id}`, kind: "pedido", numero: o.order_number,
    href: `/erp/orders/${o.id}`, detalle: null,
    fecha: o.placed_at ?? o.created_at ?? null,
    importe: o.total_amount, moneda: o.currency, ...estado,
  };
}

/** Ejercicio de un documento leído en vivo: el en curso, que es el que leen
 *  las dos listas (facturas y proformas). Sin saberlo, no hay enlace. */
function ejercicioDe(ejercicio: string | null | undefined): string | null {
  return ejercicio || null;
}

function invoiceRow(f: FactusolDocument, ejercicio?: string | null): ActivityRow {
  const cobrada = f.saldo_pendiente != null && f.saldo_pendiente <= 0.005;
  const pendiente = f.saldo_pendiente != null && f.saldo_pendiente > 0.005;
  // La factura abre su ficha (antes no abría nada).
  const ej = ejercicioDe(ejercicio);
  const href = ej && f.serie != null && f.codigo != null && f.codigo !== ""
    ? fichaDocumentoHref("factura", ej, f.serie, f.codigo)
    : null;
  return {
    key: `f-${f.numero}`, kind: "factura", numero: f.numero, href, detalle: null,
    fecha: f.fecha, importe: f.total, moneda: "EUR",
    estado: cobrada ? "Cobrada" : pendiente ? "Por cobrar" : f.estado_label || "—",
    tone: cobrada ? "g" : pendiente ? "a" : "n",
  };
}

function quoteRow(q: FactusolQuote, ejercicio?: string | null): ActivityRow {
  const numero = q.numero ?? q.codpre ?? "—";
  const serie = q.serie ?? q.tippre;
  // La proforma abre su ficha; sin serie o código, el listado como antes.
  const ej = ejercicioDe(ejercicio);
  const href = ej && serie != null && serie !== "" && q.codpre
    ? fichaDocumentoHref("proforma", ej, serie, q.codpre)
    : "/erp/proformas";
  return {
    key: `q-${q.codpre ?? numero}`, kind: "proforma", numero, href,
    detalle: q.referencia || null, fecha: q.fecha, importe: q.total, moneda: "EUR",
    estado: q.estado_label || "Proforma",
    tone: q.queue ? QUOTE_TONE[q.queue] ?? "n" : "n",
    quote: q,
  };
}

/** Une los tres orígenes en una sola lista, la más reciente arriba (sin
 *  fecha, al final). No se pierde nada de lo que devuelven las tres lecturas. */
export function buildActivityRows(
  orders: OrderSummary[], invoices: FactusolDocument[], quotes: FactusolQuote[],
  ejercicio?: string | null,
): ActivityRow[] {
  const rows = [
    ...orders.map(orderRow),
    ...invoices.map((f) => invoiceRow(f, ejercicio)),
    ...quotes.map((q) => quoteRow(q, ejercicio)),
  ];
  return rows.sort((a, b) => {
    if (!a.fecha && !b.fecha) return 0;
    if (!a.fecha) return 1;
    if (!b.fecha) return -1;
    return b.fecha.localeCompare(a.fecha);
  });
}

/** «Actividad reciente» de la ficha de empresa (Lote 2 · PR-2): UNA tabla de
 *  consulta (Documento · Tipo · Fecha · Importe · Estado) con filtro por
 *  tipo, en vez de tres listas. Se construye en cliente uniendo las mismas
 *  tres lecturas de siempre: pedidos de BoHub (con su cola del `workflow`),
 *  facturas FACTUSOL (con su cobro) y proformas. Cada lectura es best-effort
 *  y su fallo se dice en la propia tabla sin ocultar las demás. */
export function CompanyActivityPanel({
  companyId,
  contactId,
  companyName = "",
  factusolCodcli,
  contactsCount,
  canDuplicate = true,
  canEmail = true,
}: {
  companyId?: string;
  /** Ficha de contacto (30/10/2026): solo SUS pedidos, con el mismo criterio
   *  y la misma tabla. Sin pedidos, el bloque no se enseña. */
  contactId?: string;
  /** Punto B: nombre de la empresa, para el modal de duplicar proforma. */
  companyName?: string | null;
  factusolCodcli: string | null;
  contactsCount?: number;
  /** Remates · punto 6: sin el permiso, el «⋯» no ofrece la acción. */
  canDuplicate?: boolean;
  canEmail?: boolean;
}) {
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);
  const [invoices, setInvoices] = useState<FactusolDocument[] | null>(null);
  // Ejercicio en curso: el de las fichas de facturas y proformas.
  const [ejercicio, setEjercicio] = useState<string | null>(null);
  const [quotes, setQuotes] = useState<FactusolQuote[] | null>(null);
  // Error por origen: cada uno se explica en la tabla por separado.
  const [ordersError, setOrdersError] = useState(false);
  const [invoicesError, setInvoicesError] = useState(false);
  const [quotesError, setQuotesError] = useState(false);
  const [filter, setFilter] = useState<ActivityFilter>("todo");
  // Punto B: proforma que se duplica («⋯ → Duplicar» en su fila) y el aviso
  // del resultado; `reloadKey` relee las tres fuentes cuando la copia existe.
  const [duplicating, setDuplicating] = useState<FactusolQuote | null>(null);
  // Punto A: proforma que se envía por email desde «⋯».
  const [emailing, setEmailing] = useState<FactusolQuote | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  async function onDuplicated(jobId: string) {
    setDuplicating(null);
    setNotice("Creando la proforma en FACTUSOL…");
    const outcome = await pollQuoteJob(jobId);
    if (outcome.status === "finished") {
      setNotice(`Proforma nº ${String(outcome.result.codpre ?? "")} creada.`);
      setReloadKey((k) => k + 1);
    } else if (outcome.status === "failed") {
      setNotice(outcome.error ?? "La operación falló en FACTUSOL.");
    } else {
      setNotice("Sigue en curso; actualiza en unos segundos.");
    }
  }

  useEffect(() => {
    let alive = true;
    // El ejercicio en curso (el que leen facturas y proformas): el de sus fichas.
    Promise.resolve()
      .then(() => getEjercicios())
      .then((e) => { if (alive) setEjercicio(e.en_curso); })
      .catch(() => undefined);
    // Pedidos de BoHub (con su cola): siempre.
    Promise.resolve()
      .then(() => listOrders({
        ...(contactId ? { contact_id: contactId } : { company_id: companyId }),
        limit: MAX, show_external: true,
      }))
      .then((r) => { if (alive) setOrders(r.items); })
      .catch(() => { if (alive) { setOrders([]); setOrdersError(true); } });
    if (contactId || !factusolCodcli) {
      setInvoices([]);
      setQuotes([]);
      return () => { alive = false; };
    }
    Promise.resolve()
      .then(() => listFactusolDocuments("facturas", {
        codcli: factusolCodcli, sort: "fecha", dir: "desc", limit: MAX,
      }))
      .then((r) => { if (alive) setInvoices(r.items); })
      .catch(() => { if (alive) { setInvoices([]); setInvoicesError(true); } });
    Promise.resolve()
      .then(() => listFactusolQuotes({ company_id: companyId, days_back: 365 }))
      .then((r) => {
        if (!alive) return;
        const sorted = [...r.items].sort((a, b) => (b.fecha ?? "").localeCompare(a.fecha ?? ""));
        setQuotes(sorted.slice(0, MAX));
      })
      .catch(() => { if (alive) { setQuotes([]); setQuotesError(true); } });
    return () => { alive = false; };
  }, [companyId, contactId, factusolCodcli, reloadKey]);

  const loading = orders === null || invoices === null || quotes === null;
  const rows = useMemo(
    () => buildActivityRows(orders ?? [], invoices ?? [], quotes ?? [], ejercicio),
    [orders, invoices, quotes, ejercicio],
  );
  const counts = useMemo(() => {
    const c: Record<ActivityKind, number> = { pedido: 0, factura: 0, proforma: 0 };
    for (const r of rows) c[r.kind] += 1;
    return c;
  }, [rows]);
  const visible = filter === "todo" ? rows : rows.filter((r) => r.kind === filter);

  // Avisos por origen: solo los que afectan a lo que se está mirando.
  const verPedidos = filter === "todo" || filter === "pedido";
  const verFacturas = filter === "todo" || filter === "factura";
  const verProformas = filter === "todo" || filter === "proforma";
  const notes: string[] = [];
  if (ordersError && verPedidos) notes.push("No se pudieron leer los pedidos de BoHub.");
  if (invoicesError && quotesError && filter === "todo") {
    notes.push("FACTUSOL no responde: faltan las facturas y proformas.");
  } else {
    if (invoicesError && verFacturas) notes.push("FACTUSOL no responde: faltan las facturas.");
    if (quotesError && verProformas) notes.push("FACTUSOL no responde: faltan las proformas.");
  }
  if (!factusolCodcli && !verPedidos && !contactId) {
    notes.push("Sin cliente FACTUSOL vinculado: no hay facturas ni proformas que consultar.");
  }
  const emptyText = filter === "todo"
    ? "Sin pedidos, facturas ni proformas recientes."
    : filter === "pedido"
      ? "Sin pedidos recientes."
      : filter === "factura" ? "Sin facturas recientes." : "Sin proformas recientes.";

  // Un contacto sin pedidos: nada (ni un bloque vacío).
  if (contactId && !loading && !ordersError && rows.length === 0) return null;

  return (
    <section className="erp-flow-panel company-activity"
             aria-label={contactId ? "Pedidos del contacto" : "Actividad reciente"}>
      <div className="company-activity-head">
        <h3>{contactId ? "Pedidos" : "Actividad reciente"}</h3>
        {contactId ? null : <div className="company-activity-filter" role="group" aria-label="Filtrar por tipo">
          {FILTERS.map((f) => {
            const n = f.key === "todo" ? rows.length : counts[f.key];
            const active = filter === f.key;
            return (
              <button
                key={f.key}
                type="button"
                className={`pill-toggle${active ? " is-active" : ""}`}
                aria-pressed={active}
                onClick={() => setFilter(f.key)}
              >
                {f.label}{loading ? "" : ` (${n})`}
              </button>
            );
          })}
        </div>}
      </div>
      {notice ? <p className="form-info" role="status">{notice}</p> : null}
      <div className="erp-flow-table">
        <table className="data-table data-table--responsive company-activity-table">
          <thead>
            <tr>
              <th scope="col">Documento</th>
              <th scope="col">Tipo</th>
              <th scope="col">Fecha</th>
              <th scope="col" className="num">Importe</th>
              <th scope="col">Estado</th>
              <th scope="col"><span className="sr-only">Acciones</span></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="company-activity-msg">Cargando…</td></tr>
            ) : null}
            {notes.map((t) => (
              <tr key={t}>
                <td colSpan={6} className="company-activity-msg is-warn" role="status">{t}</td>
              </tr>
            ))}
            {visible.map((r) => (
              <tr key={r.key}>
                <td data-label="Documento">
                  {r.href ? (
                    <Link href={r.href} className="mono">{r.numero}</Link>
                  ) : (
                    <span className="mono">{r.numero}</span>
                  )}
                  {r.detalle ? <span className="company-activity-detail"> · {r.detalle}</span> : null}
                  {(() => {
                    const mark = r.quote ? emailedMark(r.quote.emailed_at, r.quote.emailed_to) : null;
                    return mark ? (
                      <>{" "}<span className="badge ok" title={mark.title}>{mark.label}</span></>
                    ) : null;
                  })()}
                </td>
                <td data-label="Tipo">{KIND_LABEL[r.kind]}</td>
                <td data-label="Fecha" className="mono">{fecha(r.fecha) || "—"}</td>
                <td data-label="Importe" className="num">{importe(r.importe, r.moneda)}</td>
                <td data-label="Estado">
                  <span className={`erp-flow-pill is-${r.tone}`}>{r.estado}</span>
                </td>
                <td data-label="Acciones" className="erp-quote-row-actions">
                  {/* Punto B: «⋯ → Duplicar» en las proformas (copia directa,
                      punto C). La tabla tiene scroll propio: menú flotante. */}
                  {r.kind === "proforma" && r.quote && factusolCodcli
                    && (canDuplicate || canEmail) ? (
                    <ActionsMenu label={`Más acciones ${r.numero}`} floating>
                      {canDuplicate ? (
                        <button type="button" onClick={() => setDuplicating(r.quote ?? null)}>
                          Duplicar
                        </button>
                      ) : null}
                      {canEmail ? (
                        <button type="button" onClick={() => setEmailing(r.quote ?? null)}>
                          {r.quote.emailed_at ? "Reenviar por email" : "Enviar por email"}
                        </button>
                      ) : null}
                    </ActionsMenu>
                  ) : null}
                </td>
              </tr>
            ))}
            {!loading && visible.length === 0 ? (
              <tr><td colSpan={6} className="company-activity-msg">{emptyText}</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {contactId ? null : (
        <div className="erp-flow-kv">
          <span className="k">Contactos</span>
          <span className="v">{contactsCount}</span>
        </div>
      )}

      {duplicating && companyId ? (
        <CreateQuoteModal
          companyId={companyId}
          companyName={companyName ?? ""}
          factusolCodcli={factusolCodcli}
          duplicateDirect={duplicating}
          onCreated={(jobId) => void onDuplicated(jobId)}
          onCancel={() => setDuplicating(null)}
        />
      ) : null}

      {emailing ? (
        <QuoteEmailModal
          codpre={emailing.codpre ?? ""}
          serie={emailing.serie ?? (Number(emailing.tippre) || 1)}
          numero={emailing.numero}
          onClose={() => setEmailing(null)}
          onSent={() => setReloadKey((k) => k + 1)}
        />
      ) : null}
    </section>
  );
}
