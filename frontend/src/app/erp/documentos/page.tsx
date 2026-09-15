"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import {
  cycleBadge,
  defaultPdfLang,
  FactusolDocumentDetailModal,
} from "../../components/erp/FactusolDocumentDetailModal";
import { ActionsMenu } from "../../components/erp/flow/ActionsMenu";
import { RegimePill } from "../../components/erp/flow/RegimePill";
import {
  LinkDocumentOrderModal,
  type LinkedOrder,
} from "../../components/erp/LinkDocumentOrderModal";
import { RegistrarCobroModal } from "../../components/erp/RegistrarCobroModal";
import { getCurrentUser } from "../../lib/api";
import {
  downloadFacturasPdfZip,
  downloadFactusolDocumentPdf,
  ERP_EDIT_ROLES,
  getFactusolSeries,
  listFactusolDocuments,
  saveBlob,
  type FactusolDocType,
  type FactusolDocument,
  type FactusolDocumentFilters,
  type FactusolDocumentSort,
  type FactusolSerie,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const PAGE_SIZE = 100;

/** Pestañas por tipo, en el orden de la maqueta («docs»): presupuestos,
 *  pedidos de cliente, albaranes, facturas. La clave es el `FactusolDocType`
 *  del backend; la etiqueta, la de la maqueta. */
const TABS: { key: FactusolDocType; label: string }[] = [
  { key: "presupuestos", label: "Presupuestos" },
  { key: "pedidos", label: "Pedidos cliente" },
  { key: "albaranes", label: "Albaranes" },
  { key: "facturas", label: "Facturas" },
];

/** E3-B — estados del ciclo por los que se puede filtrar en cada pestaña, con
 *  la semántica de CADA tipo: un albarán «pendiente» está «Sin facturar». */
const CICLO_OPTIONS: Partial<Record<
  FactusolDocType,
  { value: NonNullable<FactusolDocumentFilters["ciclo"]>; label: string }[]
>> = {
  presupuestos: [
    { value: "pendiente", label: "Sin albarán ni factura" },
    { value: "con_albaran", label: "Con albarán" },
    { value: "facturado", label: "Facturado" },
  ],
  pedidos: [
    { value: "pendiente", label: "Sin albarán ni factura" },
    { value: "con_albaran", label: "Con albarán" },
    { value: "facturado", label: "Facturado" },
  ],
  albaranes: [
    { value: "pendiente", label: "Sin facturar" },
    { value: "facturado", label: "Facturado" },
  ],
};

/** Orden consistente con la pantalla de Proformas: dropdown «Ordenar por» +
 *  botón de sentido. El backend ordena el conjunto completo; `numero` es
 *  numérico de verdad (ordena por CÓDIGO, 9 antes que 40). */
const SORT_LABEL: Record<FactusolDocumentSort, string> = {
  numero: "Nº", cliente: "Cliente", fecha: "Fecha", total: "Total",
  saldo: "Saldo pend.",
};

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return y && m && d ? `${d}/${m}/${y}` : iso;
}

function eur(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : `${n.toFixed(2)} €`;
}

/** Lote 2 · PR-2 — rango de mes por defecto: el mes en curso (para cuadrar
 *  cierres), en la zona horaria del navegador. */
function currentMonthRange(now = new Date()): { desde: string; hasta: string } {
  const y = now.getFullYear();
  const m = now.getMonth();
  const pad = (n: number) => String(n).padStart(2, "0");
  const last = new Date(y, m + 1, 0).getDate();
  return { desde: `${y}-${pad(m + 1)}-01`, hasta: `${y}-${pad(m + 1)}-${pad(last)}` };
}

/** «hace X» desde una marca ISO del servidor (la de la lectura en vivo). */
function sinceLabel(iso: string, now: number): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "hace unos segundos";
  const min = Math.round(s / 60);
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `hace ${h} h`;
  return `hace ${Math.round(h / 24)} días`;
}

/** Idioma del PDF por el país del cliente (E4-fix2): mismo criterio que el
 *  resto del ERP. Prefiere el ISO2 del CRM; cae al país del documento. */
function pdfLangFor(d: FactusolDocument) {
  return defaultPdfLang(d.country_iso2 ?? d.cliente_pais);
}

/** Una factura está cobrada cuando ESTFAC=2 (mismo criterio que el workflow):
 *  entonces no se ofrece «Registrar cobro». */
function facturaCobrada(d: FactusolDocument): boolean {
  return String(d.estado ?? "").replace(/\.0$/, "") === "2";
}

/** ERP-E3 / Fase 5 — explorador de documentos FACTUSOL con los componentes
 *  reales del ERP (pastilla país·régimen, badge de estado, «⋯»), lectura EN
 *  VIVO (sin cache) y solo lectura salvo el cobro F-4-B y el vínculo a
 *  pedido. Por documento se ofrece la acción que toca: PDF (todos),
 *  «Registrar cobro» (facturas pendientes, motor F-4-B), y en la columna
 *  «Pedido» el enlace al pedido de BoHub o cómo conseguirlo: «Crear pedido»
 *  (presupuestos / pedidos de cliente) o «Vincular» (albaranes / facturas
 *  creados en FACTUSOL; Lote 2 · PR-2). Filtros y orden consistentes con
 *  Proformas; «Solo sin vincular · N» como chip destacado, rango de mes por
 *  defecto y «Solo lectura · sincronizado hace X» bajo el título. */
export default function FactusolDocumentosPage() {
  const [tab, setTab] = useState<FactusolDocType>("facturas");
  const [items, setItems] = useState<FactusolDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [unlinkedTotal, setUnlinkedTotal] = useState<number | null>(null);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [cycleAge, setCycleAge] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [series, setSeries] = useState<FactusolSerie[]>([]);
  const [detail, setDetail] = useState<FactusolDocument | null>(null);
  const [canEdit, setCanEdit] = useState(false);
  // Cobro F-4-B (solo facturas pendientes): la factura elegida para el modal.
  const [cobrando, setCobrando] = useState<FactusolDocument | null>(null);
  // Lote 2 · PR-2 — «Vincular»: el albarán / la factura elegido para el modal.
  const [vinculando, setVinculando] = useState<FactusolDocument | null>(null);
  // Descarga de PDF (solo facturas): selección múltiple → ZIP, y por fila.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [downloading, setDownloading] = useState(false);
  const [dlError, setDlError] = useState<string | null>(null);

  // Filtros. `clienteQ` viaja tal cual: el backend lo resuelve contra F_CLI
  // por nombre, CIF o email (E3-A-fix1). Fechas: el mes en curso por
  // defecto (Lote 2 · PR-2), borrables.
  const [serie, setSerie] = useState<string>("");
  const [clienteInput, setClienteInput] = useState("");
  const [clienteQ, setClienteQ] = useState("");
  const [fechaDesde, setFechaDesde] = useState(() => currentMonthRange().desde);
  const [fechaHasta, setFechaHasta] = useState(() => currentMonthRange().hasta);
  const [q, setQ] = useState("");
  const [ciclo, setCiclo] = useState<string>("");
  // ERP-F3 — filtro por estado de COBRO (solo facturas), por `estado`.
  const [pago, setPago] = useState<string>("");
  // Lote 2 · PR-2 — chip «Solo sin vincular» (documentos sin pedido de BoHub).
  const [soloSinVincular, setSoloSinVincular] = useState(false);
  const [sort, setSort] = useState<FactusolDocumentSort>("numero");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    getCurrentUser()
      .then((u) => setCanEdit((ERP_EDIT_ROLES as readonly string[]).includes(u.role)))
      .catch(() => setCanEdit(false));
  }, []);

  useEffect(() => {
    getFactusolSeries()
      .then((r) => setSeries(r.items.filter((s) => s.is_known)))
      .catch(() => setSeries([]));
  }, []);

  // «sincronizado hace X» se refresca solo, sin volver a leer FACTUSOL.
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const load = useCallback(async (nextOffset: number, fresh = false) => {
    setLoading(true);
    setSyncing(fresh);
    setError(null);
    try {
      const r = await listFactusolDocuments(tab, {
        serie: serie ? Number(serie) : undefined,
        cliente_q: clienteQ.trim() || undefined,
        fecha_desde: fechaDesde || undefined,
        fecha_hasta: fechaHasta || undefined,
        q: q.trim() || undefined,
        ciclo: (ciclo || undefined) as FactusolDocumentFilters["ciclo"],
        estado: tab === "facturas" && pago ? pago : undefined,
        linked: soloSinVincular ? false : undefined,
        fresh_ciclo: fresh || undefined,
        sort,
        dir,
        limit: PAGE_SIZE,
        offset: nextOffset,
      });
      setItems(r.items);
      setTotal(r.total);
      setUnlinkedTotal(r.unlinked_total ?? null);
      setFetchedAt(r.fetched_at ?? new Date().toISOString());
      setCycleAge(r.cycle_index_age_seconds ?? null);
      setNow(Date.now());
      setOffset(nextOffset);
      setSelected(new Set());  // la selección no sobrevive a un recargado
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo consultar FACTUSOL."));
      setItems([]);
      setTotal(0);
      setUnlinkedTotal(null);
    } finally {
      setLoading(false);
      setSyncing(false);
    }
  }, [tab, serie, clienteQ, fechaDesde, fechaHasta, q, ciclo, pago, soloSinVincular, sort, dir]);

  useEffect(() => {
    void load(0);
  }, [load]);

  function limpiar() {
    setSerie("");
    setClienteInput("");
    setClienteQ("");
    setFechaDesde("");
    setFechaHasta("");
    setQ("");
    setCiclo("");
    setPago("");
    setSoloSinVincular(false);
  }

  function mesEnCurso() {
    const r = currentMonthRange();
    setFechaDesde(r.desde);
    setFechaHasta(r.hasta);
  }

  // --- Descarga de PDF ----------------------------------------------------
  const rowKey = (d: FactusolDocument) => `${d.serie}-${d.codigo}`;

  function toggleRow(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }
  function toggleAll() {
    setSelected((prev) =>
      prev.size === items.length ? new Set() : new Set(items.map(rowKey)),
    );
  }

  async function downloadOne(d: FactusolDocument) {
    if (d.serie === null || d.codigo === null) return;
    setDlError(null);
    setDownloading(true);
    try {
      const blob = await downloadFactusolDocumentPdf(
        tab, d.serie, d.codigo, pdfLangFor(d),
      );
      saveBlob(blob, `${TABS.find((t) => t.key === tab)?.label ?? "Documento"}_${d.numero}.pdf`);
    } catch (e) {
      setDlError(extractErrorMessage(e, "No se pudo descargar el PDF."));
    } finally {
      setDownloading(false);
    }
  }

  async function downloadSelectedZip() {
    const chosen = items.filter(
      (d) => selected.has(rowKey(d)) && d.serie !== null && d.codigo !== null,
    );
    if (chosen.length === 0) return;
    setDlError(null);
    setDownloading(true);
    try {
      const blob = await downloadFacturasPdfZip(
        chosen.map((d) => ({ serie: d.serie as number, codigo: Number(d.codigo) })),
      );
      saveBlob(blob, "facturas_pdf.zip");
    } catch (e) {
      setDlError(extractErrorMessage(e, "No se pudieron descargar los PDF."));
    } finally {
      setDownloading(false);
    }
  }

  // --- Vincular a pedido (Lote 2 · PR-2) ----------------------------------
  /** Tras vincular: la fila pasa a tener pedido sin releer FACTUSOL (nada
   *  cambió allí) y el contador del chip baja en uno. */
  function onLinked(d: FactusolDocument, order: LinkedOrder) {
    setVinculando(null);
    setItems((prev) => prev.map((row) => (row === d ? { ...row, order } : row)));
    setUnlinkedTotal((n) => (n === null ? null : Math.max(0, n - 1)));
    const texto = tab === "albaranes" ? "Albarán {n} vinculado" : "Factura {n} vinculada";
    setNotice(`${texto.replace("{n}", d.numero)} al pedido ${order.order_number}.`);
  }

  const month = currentMonthRange();
  const isCurrentMonth = fechaDesde === month.desde && fechaHasta === month.hasta;
  const hasFilters =
    serie !== "" || clienteQ !== "" || fechaDesde !== "" ||
    fechaHasta !== "" || q.trim() !== "" || ciclo !== "" || pago !== "" ||
    soloSinVincular;
  const cicloOptions = CICLO_OPTIONS[tab];
  const isFacturas = tab === "facturas";
  const isLinkable = tab === "albaranes" || tab === "facturas";
  const sortKeys: FactusolDocumentSort[] = isFacturas
    ? ["numero", "cliente", "fecha", "total", "saldo"]
    : ["numero", "cliente", "fecha", "total"];
  const syncLine = fetchedAt
    ? `Solo lectura · sincronizado ${sinceLabel(fetchedAt, now)}`
    : "Solo lectura · lectura en vivo de FACTUSOL";
  const syncTitle = cycleAge !== null
    ? `Datos leídos de FACTUSOL al cargar; el índice del ciclo tiene ${cycleAge} s.`
    : "Datos leídos de FACTUSOL al cargar la lista.";

  /** Columna «Pedido»: el pedido de BoHub ligado al documento o la acción
   *  para conseguirlo (crear en presupuestos / pedidos; vincular en albaranes
   *  / facturas). */
  function pedidoCell(d: FactusolDocument) {
    if (d.order) {
      return (
        <Link
          href={`/erp/orders/${d.order.id}`} className="mono erp-doc-pedido-link"
          aria-label={`Abrir pedido ${d.order.order_number}`}
          onClick={(e) => e.stopPropagation()}
        >
          {d.order.order_number}
        </Link>
      );
    }
    if (!canEdit || d.serie === null || d.codigo === null) {
      return <span className="muted">Sin vincular</span>;
    }
    if (tab === "presupuestos" || tab === "pedidos") {
      return (
        <Link
          href={`/erp/orders/new?doc_type=${tab}&serie=${d.serie}&codigo=${d.codigo}`}
          className="button small secondary erp-doc-link-btn"
          onClick={(e) => e.stopPropagation()}
        >
          Crear pedido
        </Link>
      );
    }
    return (
      <button
        type="button" className="button small secondary erp-doc-link-btn"
        aria-label={`Vincular ${d.numero} a un pedido`}
        onClick={(e) => { e.stopPropagation(); setVinculando(d); }}
      >
        Vincular
      </button>
    );
  }

  /** Acción principal por documento (la que toca según el tipo/estado). */
  function primaryAction(d: FactusolDocument) {
    if (isFacturas && canEdit && !facturaCobrada(d) && d.serie !== null && d.codigo !== null) {
      return (
        <button
          type="button" className="button small"
          onClick={(e) => { e.stopPropagation(); setCobrando(d); }}
        >
          Registrar cobro
        </button>
      );
    }
    return null;
  }

  return (
    <main className="shell shell-wide erp-flow">
      <PageHeader
        title="Documentos FACTUSOL"
        eyebrow="ERP"
        description={syncLine}
        actions={
          <button
            type="button" className="button secondary"
            disabled={loading}
            title={syncTitle}
            onClick={() => void load(offset, true)}
          >
            {syncing ? "Sincronizando…" : "Sincronizar ahora"}
          </button>
        }
      />

      <div className="erp-doc-tabs" role="tablist" aria-label="Tipo de documento">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`pill-toggle ${tab === t.key ? "is-active" : ""}`}
            onClick={() => {
              setTab(t.key); setDetail(null); setCiclo(""); setPago("");
              setSort("numero"); setDir("desc");
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {notice ? <p className="form-info" role="status">{notice}</p> : null}
      {dlError ? <p className="form-error">{dlError}</p> : null}

      <div className="erp-flow-filters" role="search" aria-label="Filtros de documentos">
        <button
          type="button"
          className={`erp-doc-chip ${soloSinVincular ? "is-active" : ""}`}
          aria-pressed={soloSinVincular}
          title={isLinkable
            ? "Documentos sin pedido de BoHub (se vinculan desde la fila)"
            : "Documentos sin pedido de BoHub (se crea desde la fila)"}
          onClick={() => setSoloSinVincular((v) => !v)}
        >
          Solo sin vincular
          {unlinkedTotal !== null ? (
            <>
              {" "}
              <span className="erp-doc-chip-n mono" aria-label={`${unlinkedTotal} sin vincular`}>
                · {unlinkedTotal}
              </span>
            </>
          ) : null}
          {soloSinVincular ? <> <span aria-hidden>✕</span></> : null}
        </button>
        <label className="field erp-flow-filter-grow">
          <span className="sr-only">Buscar documento</span>
          <input
            type="search" value={q}
            placeholder="Nº, referencia o cliente…"
            aria-label="Buscar por número, referencia o cliente"
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Cliente, CIF o email</span>
          <span className="erp-doc-cliente-buscar">
            <input
              type="text"
              placeholder="Nombre, B12345678 o email@…"
              value={clienteInput}
              aria-label="Cliente, CIF o email"
              onChange={(e) => setClienteInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") setClienteQ(clienteInput); }}
            />
            <button
              type="button" className="button small secondary"
              onClick={() => setClienteQ(clienteInput)}
            >
              Buscar
            </button>
          </span>
        </label>
        <label className="field">
          <span>Serie / empresa</span>
          <select value={serie} aria-label="Serie / empresa"
                  onChange={(e) => setSerie(e.target.value)}>
            <option value="">Todas</option>
            {series.map((s) => (
              <option key={s.serie} value={s.serie}>{s.serie} · {s.nombre}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Desde</span>
          <input type="date" value={fechaDesde} aria-label="Fecha desde"
                 onChange={(e) => setFechaDesde(e.target.value)} />
        </label>
        <label className="field">
          <span>Hasta</span>
          <input type="date" value={fechaHasta} aria-label="Fecha hasta"
                 onChange={(e) => setFechaHasta(e.target.value)} />
        </label>
        {!isCurrentMonth ? (
          <button type="button" className="button small tertiary" onClick={mesEnCurso}>
            Mes en curso
          </button>
        ) : null}
        {cicloOptions ? (
          <label className="field">
            <span>Ciclo</span>
            <select value={ciclo} aria-label="Estado del ciclo"
                    onChange={(e) => setCiclo(e.target.value)}>
              <option value="">Todos</option>
              {cicloOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
        ) : null}
        {isFacturas ? (
          <label className="field">
            <span>Cobro</span>
            <select value={pago} aria-label="Estado de cobro"
                    onChange={(e) => setPago(e.target.value)}>
              <option value="">Todas</option>
              <option value="0">Pendientes de cobro</option>
              <option value="1">Parciales</option>
              <option value="2">Cobradas</option>
            </select>
          </label>
        ) : null}
        <label className="field">
          <span>Ordenar por</span>
          <select value={sort} aria-label="Ordenar por"
                  onChange={(e) => setSort(e.target.value as FactusolDocumentSort)}>
            {sortKeys.map((k) => <option key={k} value={k}>{SORT_LABEL[k]}</option>)}
          </select>
        </label>
        <button
          type="button" className="button small secondary"
          aria-label={dir === "desc" ? "Orden descendente" : "Orden ascendente"}
          title={dir === "desc" ? "Descendente (pulsa para ascendente)" : "Ascendente (pulsa para descendente)"}
          onClick={() => setDir((d) => (d === "desc" ? "asc" : "desc"))}
        >
          {dir === "desc" ? "↓ desc" : "↑ asc"}
        </button>
        {hasFilters ? (
          <button type="button" className="button small secondary erp-doc-clear"
                  onClick={limpiar}>
            Limpiar filtros
          </button>
        ) : null}
        {isFacturas && selected.size > 0 ? (
          <button
            type="button" className="button small" disabled={downloading}
            onClick={() => void downloadSelectedZip()}
          >
            {downloading ? "Descargando…" : `Descargar PDF (ZIP) (${selected.size})`}
          </button>
        ) : null}
      </div>

      {loading ? (
        <p className="muted">Consultando FACTUSOL…</p>
      ) : items.length === 0 ? (
        <p className="muted">Sin documentos que casen los filtros.</p>
      ) : (
        <>
          <table className="data-table data-table--responsive erp-doc-table">
            <thead>
              <tr>
                {isFacturas ? (
                  <th className="erp-doc-check">
                    <input
                      type="checkbox"
                      aria-label="Seleccionar todas las facturas"
                      checked={items.length > 0 && selected.size === items.length}
                      onChange={toggleAll}
                    />
                  </th>
                ) : null}
                <th>Nº</th>
                <th>Cliente</th>
                <th>Fecha</th>
                <th className="num erp-doc-col-amount">Total</th>
                {isFacturas ? <th className="num erp-doc-col-amount">Saldo pend.</th> : null}
                <th>Estado</th>
                <th className="erp-doc-col-pedido">Pedido</th>
                <th className="erp-doc-actions-col">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {items.map((d) => {
                const badge = cycleBadge(tab, d.ciclo ?? null);
                return (
                  <tr
                    key={`${d.serie}-${d.codigo}`}
                    className={`erp-doc-row ${d.order ? "" : "is-unlinked"}`}
                    onClick={() => setDetail(d)}
                  >
                    {isFacturas ? (
                      <td className="erp-doc-check" data-label="Seleccionar"
                          onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`Seleccionar factura ${d.numero}`}
                          checked={selected.has(rowKey(d))}
                          onChange={() => toggleRow(rowKey(d))}
                        />
                      </td>
                    ) : null}
                    <td data-label="Nº"><strong className="mono">{d.numero}</strong></td>
                    <td data-label="Cliente">
                      <div className="erp-doc-cliente">
                        {d.company ? (
                          <Link href={`/companies/${d.company.id}`}
                                onClick={(e) => e.stopPropagation()}>
                            {d.company.name}
                          </Link>
                        ) : (d.cliente_nombre ?? d.cliente_codigo ?? "—")}
                        {d.regime ? (
                          <RegimePill regime={d.regime} country={d.country_iso2} />
                        ) : d.exento ? (
                          <span className="erp-flow-pill is-p"
                                title="El documento lleva un 0 % explícito de IVA">
                            exento · según el documento
                          </span>
                        ) : null}
                      </div>
                      {d.referencia ? (
                        <span className="muted small mono">{d.referencia}</span>
                      ) : null}
                    </td>
                    <td data-label="Fecha" className="mono">{fmtDate(d.fecha)}</td>
                    <td data-label="Total" className="num erp-doc-col-amount">{eur(d.total)}</td>
                    {isFacturas ? (
                      <td data-label="Saldo pend." className={
                        d.saldo_pendiente && d.saldo_pendiente > 0.005
                          ? "erp-doc-saldo-due num erp-doc-col-amount" : "num erp-doc-col-amount"
                      }>
                        {d.saldo_pendiente !== null && d.saldo_pendiente !== undefined
                          ? eur(d.saldo_pendiente) : "—"}
                      </td>
                    ) : null}
                    <td data-label="Estado">
                      <span className={`badge ${d.estado_tone ?? "muted"}`}>
                        {d.estado_label}
                      </span>
                      {badge && tab !== "facturas" ? (
                        <span className={`${badge.className} erp-doc-ciclo-badge`}>{badge.label}</span>
                      ) : null}
                    </td>
                    <td data-label="Pedido" className="erp-doc-col-pedido"
                        onClick={(e) => e.stopPropagation()}>
                      {pedidoCell(d)}
                    </td>
                    <td data-label="Acciones" onClick={(e) => e.stopPropagation()}>
                      <div className="erp-doc-row-actions">
                        {primaryAction(d)}
                        <button
                          type="button" className="button small secondary"
                          disabled={downloading || d.serie === null || d.codigo === null}
                          onClick={() => void downloadOne(d)}
                        >
                          PDF
                        </button>
                        <ActionsMenu label={`Más acciones ${d.numero}`}>
                          <button type="button" onClick={() => setDetail(d)}>
                            Ver detalle
                          </button>
                          {d.company ? (
                            <Link href={`/companies/${d.company.id}`}>Ver empresa</Link>
                          ) : null}
                          {d.order ? (
                            <Link href={`/erp/orders/${d.order.id}`}>
                              Abrir pedido {d.order.order_number}
                            </Link>
                          ) : null}
                        </ActionsMenu>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="erp-doc-pager">
            <span className="muted small">
              {offset + 1}–{Math.min(offset + items.length, total)} de {total}
            </span>
            <button
              type="button" className="button small secondary"
              disabled={offset === 0 || loading}
              onClick={() => void load(Math.max(0, offset - PAGE_SIZE))}
            >
              ← Anteriores
            </button>
            <button
              type="button" className="button small secondary"
              disabled={offset + items.length >= total || loading}
              onClick={() => void load(offset + PAGE_SIZE)}
            >
              Siguientes →
            </button>
          </div>
        </>
      )}

      {detail && detail.serie !== null && detail.codigo !== null ? (
        <FactusolDocumentDetailModal
          docType={tab}
          serie={detail.serie}
          codigo={detail.codigo}
          onClose={() => setDetail(null)}
          onChanged={() => void load(offset, true)}
        />
      ) : null}

      {cobrando && cobrando.serie !== null && cobrando.codigo !== null ? (
        <RegistrarCobroModal
          factura={{
            serie: cobrando.serie,
            codigo: cobrando.codigo,
            numero: cobrando.numero,
          }}
          onClose={() => setCobrando(null)}
          onDone={(fresh) => {
            setCobrando(null);
            if (fresh.status === "cobrada") {
              setNotice(`Factura ${fresh.numero ?? ""} cobrada en FACTUSOL.`);
            }
            void load(offset, true);
          }}
        />
      ) : null}

      {vinculando && vinculando.serie !== null && vinculando.codigo !== null ? (
        <LinkDocumentOrderModal
          docType={tab}
          serie={vinculando.serie}
          codigo={vinculando.codigo}
          numero={vinculando.numero}
          onClose={() => setVinculando(null)}
          onLinked={(order) => onLinked(vinculando, order)}
        />
      ) : null}
    </main>
  );
}
