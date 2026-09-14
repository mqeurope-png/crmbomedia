"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { CompanyPickerModal } from "../../components/CompanyPickerModal";
import { ConvertQuoteDialog } from "../../components/erp/ConvertQuoteDialog";
import { CreateQuoteModal } from "../../components/erp/CreateQuoteModal";
import { ActionsMenu } from "../../components/erp/flow/ActionsMenu";
import { RegimePill } from "../../components/erp/flow/RegimePill";
import { QueueCards } from "../../components/erp/flow/WorkflowQueueCards";
import { conversionNotice, pollQuoteJob } from "../../components/erp/quoteJobs";
import { getCurrentUser } from "../../lib/api";
import { getCompany } from "../../lib/companiesApi";
import {
  ERP_EDIT_ROLES,
  convertFactusolQuoteToOrder,
  downloadFactusolDocumentPdf,
  duplicateFactusolQuote,
  listFactusolQuotes,
  saveBlob,
  type FactusolPdfLang,
  type FactusolQuote,
  type PaymentIntentInput,
  type QuoteQueue,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** Colas de la pantalla Proformas (maqueta «proformas»): las tres de la
 *  maqueta + «Convertidas» (ya son pedido de BoHub; el equivalente a «Listo»
 *  en la bandeja, para no perder de vista lo ya convertido). El criterio lo
 *  decide el backend (`workflow.quote_queue`), aquí solo se pinta. */
const QUEUES: readonly QuoteQueue[] = ["aceptadas", "pendientes", "rechazadas", "convertidas"];
const QUEUE_LABEL: Record<QuoteQueue, string> = {
  aceptadas: "Aceptadas · por convertir",
  pendientes: "Pendientes de respuesta",
  rechazadas: "Rechazadas",
  convertidas: "Convertidas",
};
const QUEUE_COLOR: Record<QuoteQueue, string> = {
  aceptadas: "#1F9D74",
  pendientes: "#E0A92A",
  rechazadas: "#9AA2AD",
  convertidas: "#2F6BFF",
};
const QUEUE_HINT: Record<QuoteQueue, string> = {
  aceptadas: "listas para pasar a pedido",
  pendientes: "enviadas, sin respuesta del cliente",
  rechazadas: "el cliente no las aceptó",
  convertidas: "ya son pedidos de BoHub",
};
const ESTADO_TONE: Record<string, string> = {
  aceptada: "ok", pendiente: "warn", rechazada: "muted", otro: "muted",
};
const DAYS_OPTIONS = [
  { value: 90, label: "3 meses" }, { value: 180, label: "6 meses" },
  { value: 365, label: "1 año" }, { value: 730, label: "2 años" },
];
/** Serie de los presupuestos (TIPPRE es siempre '1'). */
const PRESUPUESTO_SERIE = 1;

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return y && m && d ? `${d}/${m}/${y}` : iso;
}

/** Idioma del PDF por el país del cliente (E4-fix2: el documento sale en el
 *  idioma del cliente). Sin país, español. */
function pdfLangFor(country: string | null | undefined): FactusolPdfLang {
  switch ((country || "").toUpperCase()) {
    case "FR": case "BE": case "LU": case "MC": return "fr";
    case "DE": case "AT": case "CH": return "de";
    case "NL": return "nl";
    case "": case "ES": return "es";
    default: return "en";
  }
}

/** Lo que hay que saber del IVA sin abrir nada: importe + régimen. */
function amountHint(q: FactusolQuote): string {
  if (q.regime === "intracomunitario") return "exento · intracomunitario";
  if (q.regime === "exportacion") return "exento · exportación";
  if (q.regime === "nacional") return "IVA 21 % · nacional";
  return q.exento ? "exento" : "IVA incl.";
}

type Company = { id: string; name: string; codcli: string };

/** Pantalla Proformas (rediseño de flujo, Fase 4): colas arriba con contador,
 *  lista de la cola elegida con nº, cliente (país · régimen), importe, estado
 *  y su acción principal («Convertir en pedido», la misma conversión de la
 *  Fase 2: paso de pago + albarán, idempotente), más PDF y, en «⋯», Duplicar,
 *  Editar y Ver empresa. «+ Nueva proforma» elige la empresa con el buscador
 *  unificado y abre el alta de siempre. */
export default function ProformasPage() {
  const [quotes, setQuotes] = useState<FactusolQuote[]>([]);
  const [counts, setCounts] = useState<Partial<Record<QuoteQueue, number>>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [canEdit, setCanEdit] = useState(false);
  const [queue, setQueue] = useState<QuoteQueue | null>("aceptadas");
  const [daysBack, setDaysBack] = useState(365);
  const [text, setText] = useState("");
  const [converting, setConverting] = useState<FactusolQuote | null>(null);
  const [editing, setEditing] = useState<{ quote: FactusolQuote; company: Company } | null>(null);
  const [picking, setPicking] = useState(false);
  const [creatingFor, setCreatingFor] = useState<Company | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then((u) => setCanEdit((ERP_EDIT_ROLES as readonly string[]).includes(u.role)))
      .catch(() => setCanEdit(false));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await listFactusolQuotes({ days_back: daysBack });
      setQuotes(r.items);
      setCounts(r.queue_counts ?? {});
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar las proformas."));
    } finally {
      setLoading(false);
    }
  }, [daysBack]);

  useEffect(() => { void load(); }, [load]);

  const rows = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return quotes.filter((q) => {
      if (queue && q.queue !== queue) return false;
      if (!needle) return true;
      const hay = [q.codpre, q.cliente_nombre, q.company?.name, q.referencia]
        .map((s) => (s || "").toLowerCase()).join(" ");
      return hay.includes(needle);
    });
  }, [quotes, queue, text]);

  /** Espera al job y devuelve su resultado, o null (con el error ya puesto). */
  async function waitFor(jobId: string): Promise<Record<string, unknown> | null> {
    const outcome = await pollQuoteJob(jobId);
    if (outcome.status === "finished") return outcome.result;
    if (outcome.status === "failed") setError(outcome.error);
    else setNotice("Sigue en curso; actualiza en unos segundos.");
    return null;
  }

  async function convert(q: FactusolQuote, payment: PaymentIntentInput) {
    const codpre = q.codpre ?? "";
    setConverting(null);
    setBusy(true);
    setError(null);
    setNotice(`Creando el pedido y el albarán en FACTUSOL para la proforma ${codpre}…`);
    try {
      const r = await convertFactusolQuoteToOrder(codpre, { payment, create_albaran: true });
      const result = await waitFor(r.job_id);
      if (result) setNotice(conversionNotice(codpre, result, payment.paid));
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo convertir la proforma."));
    } finally {
      setBusy(false);
    }
  }

  async function duplicate(q: FactusolQuote) {
    const codpre = q.codpre ?? "";
    setBusy(true);
    setError(null);
    setNotice(`Duplicando la proforma ${codpre} en FACTUSOL…`);
    try {
      const r = await duplicateFactusolQuote(codpre);
      const result = await waitFor(r.job_id);
      if (result) setNotice(`Proforma nº ${result.codpre} creada (duplicado de ${codpre}).`);
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo duplicar la proforma."));
    } finally {
      setBusy(false);
    }
  }

  async function pdf(q: FactusolQuote) {
    const codpre = q.codpre ?? "";
    setError(null);
    try {
      const blob = await downloadFactusolDocumentPdf(
        "presupuestos", PRESUPUESTO_SERIE, codpre,
        pdfLangFor(q.country_iso2 ?? q.company?.country), {},
      );
      saveBlob(blob, `Proforma_${codpre}.pdf`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo generar el PDF."));
    }
  }

  async function onPickCompany(companyId: string | null) {
    setPicking(false);
    if (!companyId) return;
    setError(null);
    try {
      const c = await getCompany(companyId);
      if (!c.factusol_company_id) {
        setNotice(`«${c.name}» no está vinculada a un cliente de FACTUSOL: vincúlala en su ficha para poder crear proformas.`);
        return;
      }
      setCreatingFor({ id: c.id, name: c.name, codcli: c.factusol_company_id });
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cargar la empresa."));
    }
  }

  async function onQuoteJob(jobId: string, verb: string) {
    setCreatingFor(null);
    setEditing(null);
    setBusy(true);
    setError(null);
    setNotice(`${verb} la proforma en FACTUSOL…`);
    const result = await waitFor(jobId);
    setBusy(false);
    if (result) setNotice(`Proforma nº ${result.codpre} ${verb === "Creando" ? "creada" : "actualizada"}.`);
    await load();
  }

  function companyOf(q: FactusolQuote): Company | null {
    if (!q.company) return null;
    return { id: q.company.id, name: q.company.name, codcli: q.company.factusol_id ?? q.clipre ?? "" };
  }

  function primaryAction(q: FactusolQuote) {
    if (q.order) {
      return (
        <Link href={`/erp/orders/${q.order.id}`} className="button small">
          Abrir pedido {q.order.order_number}
        </Link>
      );
    }
    if (canEdit && (q.queue === "aceptadas" || q.queue === "pendientes")) {
      return (
        <button type="button" className="button small" disabled={busy}
                onClick={() => setConverting(q)}>
          Convertir en pedido
        </button>
      );
    }
    return null;
  }

  const subtitle = queue ? QUEUE_HINT[queue] : "todas las proformas del periodo";
  const title = queue ? QUEUE_LABEL[queue] : "Todas";

  return (
    <main className="shell shell-wide erp-flow">
      <PageHeader
        title="Proformas"
        eyebrow="ERP"
        description="Presupuestos enviados y su estado."
        actions={canEdit ? (
          <button type="button" className="button small" disabled={busy}
                  onClick={() => setPicking(true)}>
            + Nueva proforma
          </button>
        ) : null}
      />

      <QueueCards
        counts={counts}
        active={queue}
        onSelect={setQueue}
        queues={QUEUES}
        labels={QUEUE_LABEL}
        colors={QUEUE_COLOR}
        hints={QUEUE_HINT}
        ariaLabel="Colas de proformas"
      />

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {notice ? <p className="form-info" role="status">{notice}</p> : null}

      <div className="erp-flow-qtitle">
        <span>{title}</span>
        <span className="cnt">· {rows.length} · {subtitle}</span>
        {queue ? (
          <button type="button" className="button small secondary" onClick={() => setQueue(null)}>
            Ver todas las colas
          </button>
        ) : null}
        <label className="field" style={{ marginLeft: "auto", minWidth: 180 }}>
          <span className="sr-only">Buscar proforma</span>
          <input
            type="search" value={text} placeholder="Nº, cliente o referencia…"
            aria-label="Buscar proforma"
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="sr-only">Periodo</span>
          <select value={daysBack} aria-label="Periodo"
                  onChange={(e) => setDaysBack(Number(e.target.value))}>
            {DAYS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      </div>

      {loading ? (
        <p className="muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="muted">
          {queue ? `Nada en «${QUEUE_LABEL[queue]}».` : "Sin proformas en el periodo."}
        </p>
      ) : (
        <div className="erp-flow-list" role="list" aria-label="Proformas">
          {rows.map((q) => {
            const codpre = q.codpre ?? "";
            const company = companyOf(q);
            return (
              <article key={codpre} role="listitem" className="erp-flow-item is-plain"
                       data-quote-row={codpre} aria-label={`Proforma ${codpre}`}>
                <div className="erp-flow-item-main">
                  <div className="erp-flow-item-r1">
                    <strong className="mono">{codpre}</strong>
                    <span className={`badge ${ESTADO_TONE[q.estado ?? "otro"] ?? "muted"}`}>
                      {q.estado_label ?? "—"}
                    </span>
                    {q.order ? (
                      <span className="badge info">pedido {q.order.order_number}</span>
                    ) : null}
                  </div>
                  <p className="erp-flow-item-r2">
                    <span>
                      {company ? (
                        <Link href={`/companies/${company.id}`}>{company.name}</Link>
                      ) : (q.cliente_nombre ?? q.clipre ?? "—")}
                    </span>
                    {q.regime ? (
                      <RegimePill regime={q.regime} country={q.country_iso2} />
                    ) : q.exento ? (
                      <span className="erp-flow-pill is-p" title="La proforma lleva un 0 % explícito de IVA">
                        exento · según la proforma
                      </span>
                    ) : null}
                    <span>{fmtDate(q.fecha)}</span>
                    {q.referencia ? <span className="muted">{q.referencia}</span> : null}
                  </p>
                </div>
                <div className="erp-flow-item-side">
                  <p className="erp-flow-amount">
                    {q.total.toFixed(2)} €<small>{amountHint(q)}</small>
                  </p>
                  <div className="erp-flow-item-actions">
                    {primaryAction(q)}
                    <button type="button" className="button small secondary" disabled={busy}
                            onClick={() => void pdf(q)}>
                      PDF
                    </button>
                    <ActionsMenu label={`Más acciones ${codpre}`}>
                      {canEdit ? (
                        <button type="button" disabled={busy} onClick={() => void duplicate(q)}>
                          Duplicar
                        </button>
                      ) : null}
                      {canEdit && company ? (
                        <button type="button" disabled={busy}
                                onClick={() => setEditing({ quote: q, company })}>
                          Editar
                        </button>
                      ) : null}
                      {canEdit && !q.order && q.queue !== "aceptadas" && q.queue !== "pendientes" ? (
                        <button type="button" disabled={busy} onClick={() => setConverting(q)}>
                          Convertir de todas formas
                        </button>
                      ) : null}
                      {company ? (
                        <Link href={`/companies/${company.id}`}>Ver empresa</Link>
                      ) : null}
                      {q.order ? (
                        <Link href={`/erp/orders/${q.order.id}`}>Abrir pedido</Link>
                      ) : null}
                    </ActionsMenu>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {converting ? (
        <ConvertQuoteDialog
          quote={converting}
          onCancel={() => setConverting(null)}
          onConfirm={(payment) => void convert(converting, payment)}
        />
      ) : null}

      <CompanyPickerModal
        open={picking}
        onClose={() => setPicking(false)}
        onPick={(id) => void onPickCompany(id)}
      />

      {creatingFor ? (
        <CreateQuoteModal
          companyId={creatingFor.id}
          companyName={creatingFor.name}
          factusolCodcli={creatingFor.codcli}
          onCreated={(jobId) => void onQuoteJob(jobId, "Creando")}
          onCancel={() => setCreatingFor(null)}
        />
      ) : null}

      {editing ? (
        <CreateQuoteModal
          companyId={editing.company.id}
          companyName={editing.company.name}
          factusolCodcli={editing.company.codcli}
          editCodpre={editing.quote.codpre}
          onCreated={(jobId) => void onQuoteJob(jobId, "Actualizando")}
          onCancel={() => setEditing(null)}
        />
      ) : null}
    </main>
  );
}
