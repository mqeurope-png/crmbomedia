"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Cap, can } from "../../lib/capabilities";
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

  convertFactusolQuoteToOrder,
  downloadFactusolDocumentPdf,
  FACTUSOL_SERIES,
  listFactusolQuotes,
  saveBlob,
  type FactusolPdfLang,
  type FactusolQuote,
  type PaymentIntentInput,
  type QuoteEstado,
  type QuoteQueue,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ageInDays, relativeAge } from "../../lib/relativeAge";

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
/** Serie por defecto del PDF cuando la proforma no trae `TIPPRE`. */
const PRESUPUESTO_SERIE = 1;
/** Hasta cuántas proformas pide la pantalla (el listado va DESC por CODPRE). */
const LIST_LIMIT = 500;
const MAX_DAYS_BACK = 1825;

type SortKey = "fecha" | "serie" | "codpre";
type SortDir = "asc" | "desc";
const SORT_LABEL: Record<SortKey, string> = {
  fecha: "Fecha", serie: "Serie", codpre: "Nº de proforma",
};

/** Lote 2 · PR-2: antigüedad en palabras. Verbo de la frase según el estado
 *  («Aceptada hace 3 días», «Enviada hace 41 días · sin respuesta»,
 *  «Rechazada hace 9 semanas»). La convertida no pasa por aquí: enlaza a su
 *  pedido. */
const AGE_VERB: Record<QuoteEstado, string> = {
  aceptada: "Aceptada", pendiente: "Enviada", rechazada: "Rechazada", otro: "Emitida",
};
/** Días a partir de los cuales la antigüedad es un aviso comercial y la frase
 *  se tiñe de ámbar (no es un estado del sistema, por eso no es pastilla).
 *  Solo pendientes: `fecha` es la de emisión de la proforma, no la de la
 *  aceptación, así que en aceptadas no dice cuánto lleva esperando. */
const AGE_NOTICE_DAYS: Partial<Record<QuoteEstado, number>> = { pendiente: 30 };

/** Orden por defecto de cada cola: en «pendientes» por antigüedad (las más
 *  antiguas primero, que son la acción comercial); en las demás, fecha desc.
 *  La elección explícita del usuario siempre manda. */
function defaultSortDir(queue: QuoteQueue | null, key: SortKey): SortDir {
  return queue === "pendientes" && key === "fecha" ? "asc" : "desc";
}

function serieOf(q: FactusolQuote): number {
  return q.serie ?? (Number(q.tippre) || 0);
}
/** Nº de FACTUSOL como número de verdad (9 < 40, no «9» > «40»). */
function codpreOf(q: FactusolQuote): number {
  return Number(q.codpre) || 0;
}

/** Orden de la lista: fecha (empate → nº), serie (empate → nº) o nº; siempre
 *  numérico en el nº. */
function sortQuotes(list: FactusolQuote[], key: SortKey, dir: SortDir): FactusolQuote[] {
  const sign = dir === "asc" ? 1 : -1;
  const byCodpre = (a: FactusolQuote, b: FactusolQuote) => codpreOf(a) - codpreOf(b);
  const cmp = (a: FactusolQuote, b: FactusolQuote): number => {
    if (key === "fecha") {
      const d = (a.fecha ?? "").localeCompare(b.fecha ?? "");
      return d !== 0 ? d : byCodpre(a, b);
    }
    if (key === "serie") {
      const d = serieOf(a) - serieOf(b);
      return d !== 0 ? d : byCodpre(a, b);
    }
    return byCodpre(a, b);
  };
  return [...list].sort((a, b) => sign * cmp(a, b));
}

/** Días hacia atrás que hay que pedir para cubrir «desde» (el periodo elegido
 *  como mínimo). */
function daysBackFor(daysBack: number, desde: string): number {
  if (!desde) return daysBack;
  const from = new Date(`${desde}T00:00:00Z`).getTime();
  if (Number.isNaN(from)) return daysBack;
  const days = Math.ceil((Date.now() - from) / 86_400_000) + 1;
  return Math.min(MAX_DAYS_BACK, Math.max(daysBack, days));
}

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
 *  Fase 2: paso de pago + albarán, idempotente), más Duplicar y PDF y, en
 *  «⋯», Editar y Ver empresa. «+ Nueva proforma» elige la empresa con el
 *  buscador unificado y abre el alta de siempre.
 *
 *  Lote 2 · PR-2 (revisión de diseño §5): cada fila dice su antigüedad en
 *  palabras y se tiñe de ámbar pasado el umbral; «pendientes» se ordena por
 *  antigüedad; Duplicar es un secundario fijo en todas las filas; y la
 *  convertida enlaza a su pedido desde la frase y desde «Ver pedido». */
export default function ProformasPage() {
  const [quotes, setQuotes] = useState<FactusolQuote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [canEdit, setCanEdit] = useState(false);
  const [queue, setQueue] = useState<QuoteQueue | null>("aceptadas");
  const [daysBack, setDaysBack] = useState(365);
  // Serie = empresa emisora. 0 / vacío = TODAS (por defecto), igual que en
  // Documentos: antes la pantalla solo llegaba a ver las de la serie 1 porque
  // el listado se recortaba por CODPRE y los contadores son POR serie.
  const [serie, setSerie] = useState<number>(0);
  // Filtros combinables (sobre las colas): texto, rango de fechas; y orden.
  const [text, setText] = useState("");
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  // Orden: null = el propio de la cola (`defaultSortDir`); un valor = lo que
  // eligió el usuario, que se mantiene al cambiar de cola.
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir | null>(null);
  const [converting, setConverting] = useState<FactusolQuote | null>(null);
  const [editing, setEditing] = useState<{ quote: FactusolQuote; company: Company } | null>(null);
  // Duplicar con previsualización (Lote 3): la proforma de origen que se abre en
  // el modal en modo «Duplicar». La copia se crea desde el propio modal tras la
  // vista previa; ya no hay duplicado directo desde la fila.
  const [duplicating, setDuplicating] = useState<FactusolQuote | null>(null);
  const [picking, setPicking] = useState(false);
  const [creatingFor, setCreatingFor] = useState<Company | null>(null);
  // «Ahora» para la antigüedad en palabras: el momento de la última carga (no
  // se llama a Date.now() al pintar).
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    getCurrentUser()
      .then((u) => setCanEdit(can(u, Cap.PROFORMAS)))
      .catch(() => setCanEdit(false));
  }, []);

  // «Desde» más antiguo que el periodo amplía lo que se pide al backend.
  const effectiveDays = daysBackFor(daysBack, desde);
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await listFactusolQuotes({
        days_back: effectiveDays, limit: LIST_LIMIT,
        // Sin serie = TODAS las empresas emisoras (igual que Documentos).
        ...(serie ? { serie } : {}),
      });
      setQuotes(r.items);
      setNow(Date.now());
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar las proformas."));
    } finally {
      setLoading(false);
    }
  }, [effectiveDays, serie]);

  useEffect(() => { void load(); }, [load]);

  // Filtros (texto + fechas) ANTES de la cola: los contadores de las colas
  // siguen al filtro, como en la bandeja.
  const filtered = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return quotes.filter((q) => {
      if (desde && (!q.fecha || q.fecha < desde)) return false;
      if (hasta && (!q.fecha || q.fecha > hasta)) return false;
      if (!needle) return true;
      const hay = [q.numero, q.codpre, q.cliente_nombre, q.company?.name, q.referencia]
        .map((s) => (s || "").toLowerCase()).join(" ");
      return hay.includes(needle);
    });
  }, [quotes, text, desde, hasta]);

  const counts = useMemo(() => {
    const c: Partial<Record<QuoteQueue, number>> = {};
    for (const q of filtered) {
      if (q.queue) c[q.queue] = (c[q.queue] ?? 0) + 1;
    }
    return c;
  }, [filtered]);

  const effSortKey: SortKey = sortKey ?? "fecha";
  const effSortDir: SortDir = sortDir ?? defaultSortDir(queue, effSortKey);
  const rows = useMemo(
    () => sortQuotes(filtered.filter((q) => !queue || q.queue === queue), effSortKey, effSortDir),
    [filtered, queue, effSortKey, effSortDir],
  );
  const hasFilters = Boolean(text.trim() || desde || hasta);
  /** «Más antiguas primero» solo cuando lo decide la cola, no el usuario. */
  const byAge = queue === "pendientes" && sortKey === null && sortDir === null;

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
      const r = await convertFactusolQuoteToOrder(
        codpre, { payment, create_albaran: true }, serieOf(q) || undefined,
      );
      const result = await waitFor(r.job_id);
      if (result) setNotice(conversionNotice(codpre, result, payment.paid));
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo convertir la proforma."));
    } finally {
      setBusy(false);
    }
  }

  async function pdf(q: FactusolQuote) {
    const codpre = q.codpre ?? "";
    setError(null);
    try {
      const blob = await downloadFactusolDocumentPdf(
        "presupuestos", serieOf(q) || PRESUPUESTO_SERIE, codpre,
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
    setDuplicating(null);
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

  /** Acción principal: la siguiente del sistema. La convertida no tiene (ya es
   *  pedido): «Ver pedido» va como secundario. */
  function primaryAction(q: FactusolQuote) {
    if (!q.order && canEdit && (q.queue === "aceptadas" || q.queue === "pendientes")) {
      return (
        <button type="button" className="button small" disabled={busy}
                onClick={() => setConverting(q)}>
          Convertir en pedido
        </button>
      );
    }
    return null;
  }

  /** Antigüedad en palabras (Lote 2 · PR-2). La convertida enlaza a su pedido
   *  desde la propia frase; las demás dicen estado + «hace N días» y, pasado
   *  el umbral de su estado, van en ámbar como aviso comercial. */
  function agePhrase(q: FactusolQuote) {
    if (q.order) {
      return (
        <span className="erp-pf-age-t">
          Convertida en{" "}
          <Link href={`/erp/orders/${q.order.id}`} className="mono">{q.order.order_number}</Link>
        </span>
      );
    }
    const estado: QuoteEstado = q.estado ?? "otro";
    const age = relativeAge(q.fecha, now);
    const days = ageInDays(q.fecha, now);
    const limit = AGE_NOTICE_DAYS[estado];
    const late = limit !== undefined && days !== null && days >= limit;
    const text = [AGE_VERB[estado], age, estado === "pendiente" ? "· sin respuesta" : null]
      .filter(Boolean).join(" ");
    return (
      <span className={`erp-pf-age-t${late ? " is-late" : ""}`}
            title={late ? `${days} días sin respuesta del cliente: toca reclamar.` : undefined}>
        {text}
      </span>
    );
  }

  const subtitle = (queue ? QUEUE_HINT[queue] : "todas las proformas del periodo")
    + (byAge ? " · más antiguas primero" : "");
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

      <div className="erp-flow-filters" role="search" aria-label="Filtros de proformas">
        <label className="field erp-flow-filter-grow">
          <span className="sr-only">Buscar proforma</span>
          <input
            type="search" value={text} placeholder="Empresa, cliente, referencia o nº…"
            aria-label="Buscar proforma"
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Desde</span>
          <input type="date" aria-label="Fecha desde" value={desde}
                 onChange={(e) => setDesde(e.target.value)} />
        </label>
        <label className="field">
          <span>Hasta</span>
          <input type="date" aria-label="Fecha hasta" value={hasta}
                 onChange={(e) => setHasta(e.target.value)} />
        </label>
        <label className="field">
          <span>Empresa emisora</span>
          <select value={serie} aria-label="Empresa emisora (serie)"
                  onChange={(e) => setSerie(Number(e.target.value))}>
            <option value={0}>Todas</option>
            {FACTUSOL_SERIES.map((s) => (
              <option key={s.value} value={s.value}>{s.value} · {s.label}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Periodo</span>
          <select value={daysBack} aria-label="Periodo"
                  onChange={(e) => setDaysBack(Number(e.target.value))}>
            {DAYS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Ordenar por</span>
          <select value={effSortKey} aria-label="Ordenar por"
                  onChange={(e) => setSortKey(e.target.value as SortKey)}>
            {(Object.keys(SORT_LABEL) as SortKey[]).map((k) => (
              <option key={k} value={k}>{SORT_LABEL[k]}</option>
            ))}
          </select>
        </label>
        <button
          type="button" className="button small secondary"
          aria-label={effSortDir === "desc" ? "Orden descendente" : "Orden ascendente"}
          title={effSortDir === "desc" ? "Descendente (pulsa para ascendente)" : "Ascendente (pulsa para descendente)"}
          onClick={() => setSortDir(effSortDir === "desc" ? "asc" : "desc")}
        >
          {effSortDir === "desc" ? "↓ desc" : "↑ asc"}
        </button>
        {hasFilters ? (
          <button type="button" className="button small secondary"
                  onClick={() => { setText(""); setDesde(""); setHasta(""); }}>
            Limpiar filtros
          </button>
        ) : null}
      </div>

      <div className="erp-flow-qtitle">
        <span>{title}</span>
        <span className="cnt">· {rows.length} · {subtitle}</span>
        {queue ? (
          <button type="button" className="button small secondary" onClick={() => setQueue(null)}>
            Ver todas las colas
          </button>
        ) : null}
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
            // «⋯» solo con lo que no está ya como botón en la fila (nada
            // repetido): Editar, Convertir de todas formas, Ver empresa.
            const menu = [
              canEdit && company ? (
                <button key="editar" type="button" disabled={busy}
                        onClick={() => setEditing({ quote: q, company })}>
                  Editar
                </button>
              ) : null,
              canEdit && !q.order && q.queue !== "aceptadas" && q.queue !== "pendientes" ? (
                <button key="convertir" type="button" disabled={busy} onClick={() => setConverting(q)}>
                  Convertir de todas formas
                </button>
              ) : null,
              company ? (
                <Link key="empresa" href={`/companies/${company.id}`}>Ver empresa</Link>
              ) : null,
            ].filter(Boolean);
            return (
              <article key={codpre} role="listitem" className="erp-flow-item is-plain"
                       data-quote-row={codpre} aria-label={`Proforma ${codpre}`}>
                <div className="erp-flow-item-main">
                  {/* Nº (mono) con las pastillas de origen (serie) y régimen al lado,
                      y el estado como pastilla; la palabra siempre, no solo el color. */}
                  <div className="erp-flow-item-r1">
                    <strong className="mono" title={`CODPRE ${codpre}`}>{q.numero ?? codpre}</strong>
                    {q.serie_label ? (
                      <span className="erp-flow-src" title={`Serie ${serieOf(q)}`}>{q.serie_label}</span>
                    ) : null}
                    {q.regime ? (
                      <RegimePill regime={q.regime} country={q.country_iso2} />
                    ) : q.exento ? (
                      <span className="erp-flow-pill is-p" title="La proforma lleva un 0 % explícito de IVA">
                        exento · según la proforma
                      </span>
                    ) : null}
                    <span className={`badge ${ESTADO_TONE[q.estado ?? "otro"] ?? "muted"}`}>
                      {q.estado_label ?? "—"}
                    </span>
                    {q.order ? (
                      <span className="badge info">pedido {q.order.order_number}</span>
                    ) : null}
                  </div>
                  <p className="erp-pf-client">
                    {company ? (
                      <Link href={`/companies/${company.id}`}>{company.name}</Link>
                    ) : (q.cliente_nombre ?? q.clipre ?? "—")}
                  </p>
                  <p className="erp-flow-item-r2 erp-pf-age">
                    {agePhrase(q)}
                    <span className="mono">{fmtDate(q.fecha)}</span>
                    {q.referencia ? <span className="muted">{q.referencia}</span> : null}
                  </p>
                </div>
                <div className="erp-flow-item-side">
                  <p className="erp-flow-amount">
                    {q.total.toFixed(2)} €<small>{amountHint(q)}</small>
                  </p>
                  <div className="erp-flow-item-actions">
                    {primaryAction(q)}
                    {q.order ? (
                      <Link href={`/erp/orders/${q.order.id}`} className="button small secondary"
                            title={`Pedido ${q.order.order_number}`}>
                        Ver pedido
                      </Link>
                    ) : null}
                    {canEdit ? (
                      <button type="button" className="button small secondary" disabled={busy}
                              onClick={() => setDuplicating(q)}>
                        Duplicar
                      </button>
                    ) : null}
                    <button type="button" className="button small secondary" disabled={busy}
                            onClick={() => void pdf(q)}>
                      PDF
                    </button>
                    {menu.length > 0 ? (
                      <ActionsMenu label={`Más acciones ${codpre}`}>{menu}</ActionsMenu>
                    ) : null}
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
          editSerie={serieOf(editing.quote) || undefined}
          onCreated={(jobId) => void onQuoteJob(jobId, "Actualizando")}
          onCancel={() => setEditing(null)}
        />
      ) : null}

      {/* Duplicar con previsualización (Lote 3): el mismo modal que «Nueva
          proforma → Duplicar», abierto ya en modo «Duplicar» con la proforma de
          la fila cargada en la vista previa (líneas reales de F_LPS y «Ver
          PDF»). El cliente destino arranca en el de la propia proforma si está
          vinculado a una empresa del CRM; si no, se elige con «Cambiar». La
          copia se crea desde el modal, nunca directa. */}
      {duplicating ? (
        <CreateQuoteModal
          companyId={companyOf(duplicating)?.id ?? ""}
          companyName={companyOf(duplicating)?.name ?? duplicating.cliente_nombre ?? "—"}
          factusolCodcli={companyOf(duplicating)?.codcli ?? null}
          duplicateSource={duplicating}
          onCreated={(jobId) => void onQuoteJob(jobId, "Creando")}
          onCancel={() => setDuplicating(null)}
        />
      ) : null}
    </main>
  );
}
