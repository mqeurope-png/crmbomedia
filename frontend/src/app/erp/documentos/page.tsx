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
 *  VIVO (sin cache) y solo lectura salvo el cobro F-4-B. Por documento se
 *  ofrece la acción que toca: PDF (todos), «Registrar cobro» (facturas
 *  pendientes, motor F-4-B), y «Crear/Abrir pedido» de BoHub (presupuestos /
 *  pedidos de cliente). Filtros y orden consistentes con Proformas. */
export default function FactusolDocumentosPage() {
  const [tab, setTab] = useState<FactusolDocType>("facturas");
  const [items, setItems] = useState<FactusolDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [series, setSeries] = useState<FactusolSerie[]>([]);
  const [detail, setDetail] = useState<FactusolDocument | null>(null);
  const [canEdit, setCanEdit] = useState(false);
  // Cobro F-4-B (solo facturas pendientes): la factura elegida para el modal.
  const [cobrando, setCobrando] = useState<FactusolDocument | null>(null);
  // Descarga de PDF (solo facturas): selección múltiple → ZIP, y por fila.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [downloading, setDownloading] = useState(false);
  const [dlError, setDlError] = useState<string | null>(null);

  // Filtros. `clienteQ` viaja tal cual: el backend lo resuelve contra F_CLI
  // por nombre, CIF o email (E3-A-fix1).
  const [serie, setSerie] = useState<string>("");
  const [clienteInput, setClienteInput] = useState("");
  const [clienteQ, setClienteQ] = useState("");
  const [fechaDesde, setFechaDesde] = useState("");
  const [fechaHasta, setFechaHasta] = useState("");
  const [q, setQ] = useState("");
  const [ciclo, setCiclo] = useState<string>("");
  // ERP-F3 — filtro por estado de COBRO (solo facturas), por `estado`.
  const [pago, setPago] = useState<string>("");
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

  const load = useCallback(async (nextOffset: number, fresh = false) => {
    setLoading(true);
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
        fresh_ciclo: fresh || undefined,
        sort,
        dir,
        limit: PAGE_SIZE,
        offset: nextOffset,
      });
      setItems(r.items);
      setTotal(r.total);
      setOffset(nextOffset);
      setSelected(new Set());  // la selección no sobrevive a un recargado
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo consultar FACTUSOL."));
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [tab, serie, clienteQ, fechaDesde, fechaHasta, q, ciclo, pago, sort, dir]);

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

  const hasFilters =
    serie !== "" || clienteQ !== "" || fechaDesde !== "" ||
    fechaHasta !== "" || q.trim() !== "" || ciclo !== "" || pago !== "";
  const cicloOptions = CICLO_OPTIONS[tab];
  const isFacturas = tab === "facturas";
  const sortKeys: FactusolDocumentSort[] = isFacturas
    ? ["numero", "cliente", "fecha", "total", "saldo"]
    : ["numero", "cliente", "fecha", "total"];

  /** Acción principal por documento (la que toca según el tipo/estado). */
  function primaryAction(d: FactusolDocument) {
    if (isFacturas) {
      if (canEdit && !facturaCobrada(d) && d.serie !== null && d.codigo !== null) {
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
    if (tab === "presupuestos" || tab === "pedidos") {
      if (d.order) {
        return (
          <Link
            href={`/erp/orders/${d.order.id}`} className="button small"
            onClick={(e) => e.stopPropagation()}
          >
            Abrir pedido {d.order.order_number}
          </Link>
        );
      }
      if (canEdit && d.serie !== null && d.codigo !== null) {
        return (
          <Link
            href={`/erp/orders/new?doc_type=${tab}&serie=${d.serie}&codigo=${d.codigo}`}
            className="button small"
            onClick={(e) => e.stopPropagation()}
          >
            Crear pedido
          </Link>
        );
      }
    }
    return null;
  }

  return (
    <main className="shell shell-wide erp-flow">
      <PageHeader
        title="Documentos FACTUSOL"
        eyebrow="ERP"
        description="Explorador de presupuestos, pedidos, albaranes y facturas — lectura en vivo."
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
          <table className="data-table erp-doc-table">
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
                <th>Total</th>
                {isFacturas ? <th>Saldo pend.</th> : null}
                <th>Estado</th>
                <th className="erp-doc-actions-col">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {items.map((d) => {
                const badge = cycleBadge(tab, d.ciclo ?? null);
                return (
                  <tr
                    key={`${d.serie}-${d.codigo}`}
                    className="erp-doc-row"
                    onClick={() => setDetail(d)}
                  >
                    {isFacturas ? (
                      <td className="erp-doc-check" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`Seleccionar factura ${d.numero}`}
                          checked={selected.has(rowKey(d))}
                          onChange={() => toggleRow(rowKey(d))}
                        />
                      </td>
                    ) : null}
                    <td><strong className="mono">{d.numero}</strong></td>
                    <td>
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
                        <span className="muted small">{d.referencia}</span>
                      ) : null}
                    </td>
                    <td>{fmtDate(d.fecha)}</td>
                    <td className="mono">{eur(d.total)}</td>
                    {isFacturas ? (
                      <td className={
                        d.saldo_pendiente && d.saldo_pendiente > 0.005
                          ? "erp-doc-saldo-due mono" : "mono"
                      }>
                        {d.saldo_pendiente !== null && d.saldo_pendiente !== undefined
                          ? eur(d.saldo_pendiente) : "—"}
                      </td>
                    ) : null}
                    <td>
                      <span className={`badge ${d.estado_tone ?? "muted"}`}>
                        {d.estado_label}
                      </span>
                      {badge && tab !== "facturas" ? (
                        <span className={`${badge.className} erp-doc-ciclo-badge`}>{badge.label}</span>
                      ) : null}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
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
    </main>
  );
}
