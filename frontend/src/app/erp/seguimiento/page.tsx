"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Cap, can } from "../../lib/capabilities";
import { PageHeader } from "../../components/PageHeader";
import {
  ColumnPicker,
  readHiddenColumns,
  storeHiddenColumns,
} from "../../components/ColumnPicker";
import { ScrollTable } from "../../components/ScrollTable";
import { ExcludeSeguimientoModal } from "../../components/erp/ExcludeSeguimientoModal";
import { getCurrentUser, type User } from "../../lib/api";
import {
  downloadFacturasPdfZip,
  downloadFactusolDocumentPdf,

  excludeSeguimiento,
  type ExclusionReasonCode,
  exportSeguimientoXlsx,
  forceSeguimiento,
  getErpSettings,
  getOrderFactusolInvoiceRef,
  includeSeguimiento,
  listSeguimiento,
  reconcileFactusolInvoices,
  reconcileWooStatuses,
  saveBlob,
  waitForFactusolReconcile,
  waitForReconcileWoo,
  type FactusolLinkSummary,
  isManagedSummary,
  syncSeguimientoDrive,
  type DriveManagedSummary,
  type SeguimientoEspejoStats,
  type DriveSyncReviewGroup,
  type DriveSyncSummary,
  type DriveSyncUnknownInvoice,
  type SeguimientoFilters,
  type SeguimientoPage,
  type SeguimientoRow,
  type WooReconcileSummary,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** Columnas de la tabla (rediseño 2026), en su orden FIJO, con su clave de
 *  orden (null = no ordenable). El estado va en la columna Situación, no en
 *  la posición; la tabla se ordena por Fecha por defecto (Situación sigue
 *  disponible pulsando su cabecera). `sticky` = fija a la izquierda al
 *  desplazar (con la casilla de selección); `locked` = no se puede ocultar
 *  con «Columnas». Ocultar columnas NO cambia el Excel ni la hoja de Drive,
 *  que siguen llevando todas. */
type ColKey =
  | "situacion" | "pedido" | "fecha" | "cliente" | "origen" | "productos"
  | "importe" | "empresa" | "factura" | "fecha_factura" | "factura_enviada"
  | "cobro" | "preparacion" | "envio" | "courier" | "recogido" | "tracking" | "serie" | "nota";
const COLUMNS: { key: ColKey; label: string; sort: string | null; sticky?: boolean; locked?: boolean }[] = [
  { key: "situacion", label: "Situación", sort: "situacion", sticky: true },
  { key: "pedido", label: "Nº pedido", sort: "albaran_pedido", sticky: true, locked: true },
  { key: "fecha", label: "Fecha", sort: "fecha" },
  { key: "cliente", label: "Cliente", sort: "cliente", sticky: true },
  { key: "origen", label: "Origen", sort: null },
  { key: "productos", label: "Productos", sort: null },
  { key: "importe", label: "Importe", sort: null },
  { key: "empresa", label: "Empresa (serie)", sort: "empresa" },
  { key: "factura", label: "Factura", sort: "factura" },
  { key: "fecha_factura", label: "Fecha factura", sort: null },
  { key: "factura_enviada", label: "Factura enviada", sort: null },
  { key: "cobro", label: "Cobro", sort: null },
  { key: "preparacion", label: "Preparación", sort: null },
  { key: "envio", label: "Envío", sort: null },
  // Con quién va el envío (la agencia de Genei o el courier de la Cola SAT);
  // «Envío» es solo el estado.
  { key: "courier", label: "Courier", sort: "courier" },
  { key: "recogido", label: "Fecha recogido", sort: null },
  { key: "tracking", label: "Tracking", sort: null },
  { key: "serie", label: "Nº serie · WhiteRIP", sort: null },
  { key: "nota", label: "Nota / Incidencia", sort: null },
];
const COLUMN_KEYS: string[] = COLUMNS.map((c) => c.key);

/** Clases extra de las celdas (tamaño / color) por columna. */
const CELL_CLASS: Partial<Record<ColKey, string>> = {
  productos: " muted small",
  importe: " erp-num",
  tracking: " muted small",
  serie: " muted small",
  nota: " small",
  preparacion: " small",
  envio: " small",
  courier: " small",
};

/** Clases de la celda: las de su columna y, en Preparación / Envío, «No
 *  aplica» en gris (el paso no aplica a ese pedido). */
function cellClass(key: ColKey, r: SeguimientoRow): string {
  const muted = (key === "preparacion" || key === "envio") && r[key] === "No aplica";
  return `${CELL_CLASS[key] ?? ""}${muted ? " muted" : ""}`;
}

/** Columnas ocultas, por usuario, en este navegador. */
function columnsStorageKey(user: User | null): string {
  return `bohub.seguimiento.columnas.${user?.id || user?.email || "anon"}`;
}

/** Texto largo en una línea, truncado: el completo al pasar el ratón y, con
 *  un clic, desplegado (se pliega con otro clic). Una sola copia del texto. */
function TruncText({ text, className }: { text: string | null | undefined; className?: string }) {
  const [open, setOpen] = useState(false);
  if (!text) return <>—</>;
  return (
    <button
      type="button"
      className={`seg-trunc${open ? " is-open" : ""}${className ? ` ${className}` : ""}`}
      title={text}
      aria-expanded={open}
      onClick={() => setOpen((v) => !v)}
    >
      {text}
    </button>
  );
}

function d(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, day] = iso.split("-");
  return `${Number(day)}/${Number(m)}/${y}`;
}

/** Fecha corta de la tabla: `30/09/26`. */
function dc(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, day] = iso.slice(0, 10).split("-");
  if (!y || !m || !day) return "—";
  return `${day.padStart(2, "0")}/${m.padStart(2, "0")}/${y.slice(-2)}`;
}

/** Importe con formato `#.##0,00 €` (es-ES). */
function eur(n: number, moneda: string): string {
  try {
    return new Intl.NumberFormat("es-ES", {
      style: "currency", currency: moneda || "EUR",
    }).format(Number(n) || 0);
  } catch {
    return `${(Number(n) || 0).toFixed(2)} €`;
  }
}

/** « (artisjet-europe 2 · boprint 1)» — métodos de pago rellenados por tienda. */
function paymentMethodByStore(r: WooReconcileSummary): string {
  const parts = Object.entries(r.payment_method_by_store ?? {})
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([store, n]) => `${store} ${n}`);
  return parts.length ? ` (${parts.join(" · ")})` : "";
}

/** Cambios que aplica la puesta al día: salidas del seguimiento, reembolsos
 *  marcados, estados recuperados y métodos de pago rellenados. */
function reconcileChanges(r: WooReconcileSummary): number {
  return r.removed_total + r.to_refunded + (r.to_filled ?? 0) + (r.to_not_found ?? 0)
    + (r.to_payment_method ?? 0);
}

/** ERP-F6 — Seguimiento de pedidos: la vista que sustituye el Excel manual de
 *  Bart. Por defecto enseña los pedidos EN CURSO (la parte de arriba del
 *  Excel); el histórico de 7.743 filas se queda en su fichero.
 *  ERP-F6-fix7: casillas para excluir del seguimiento, filtros de pendientes /
 *  excluidos, y previsualización que separa lo que hay que decidir de lo que
 *  solo informa. */
export default function SeguimientoPage() {
  const [user, setUser] = useState<User | null>(null);
  const [page, setPage] = useState<SeguimientoPage | null>(null);
  // Lote de bandeja (Parte B): por defecto, ordenado por Fecha (más reciente
  // primero) — antes era Situación, y eso enterraba abajo (por «Por enviar»)
  // pedidos recientes como una MUESTRA-xxxx recién creada. Situación sigue
  // disponible pulsando su cabecera (orden manual, como cualquier otra).
  const [filters, setFilters] = useState<SeguimientoFilters>({ sort: "fecha", dir: "desc" });
  const [q, setQ] = useState("");
  const [origins, setOrigins] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [syncSummary, setSyncSummary] = useState<DriveSyncSummary | null>(null);
  // ERP-F6-fix2 — previsualización pendiente de confirmar (dry-run).
  const [previewSummary, setPreviewSummary] = useState<DriveSyncSummary | null>(null);
  // ERP-F6-fix7 — selección de filas para excluir/reincluir en bloque.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Control manual — pedidos a QUITAR del seguimiento (abre el diálogo de
  // motivo + avisos). Una fila o la selección.
  const [excludeTarget, setExcludeTarget] = useState<SeguimientoRow[] | null>(null);
  // ERP-Woo — previsualización de la puesta al día de estados de WooCommerce.
  const [reconcile, setReconcile] = useState<WooReconcileSummary | null>(null);
  // ERP — previsualización de la vinculación de facturas de FACTUSOL.
  const [facturaLink, setFacturaLink] = useState<FactusolLinkSummary | null>(null);
  // «Columnas»: las ocultas por este usuario (todas visibles por defecto).
  const [hiddenCols, setHiddenCols] = useState<Set<string>>(new Set());

  const canEdit = can(user, Cap.SEGUIMIENTO);
  const viewExcluded = filters.ver_excluidos === true;
  const viewOcultos = filters.ver_ocultos_estado === true;

  const load = useCallback(async () => {
    try {
      setPage(await listSeguimiento({ ...filters, limit: 300 }));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cargar el seguimiento."));
    }
  }, [filters]);

  useEffect(() => {
    getCurrentUser()
      .then((u) => {
        setUser(u);
        setHiddenCols(readHiddenColumns(columnsStorageKey(u), COLUMN_KEYS));
      })
      .catch(() => undefined);
    getErpSettings()
      .then((cfg) => setOrigins(cfg.shipping_origins ?? []))
      .catch(() => setOrigins([]));
  }, []);

  useEffect(() => { void load(); }, [load]);
  // Al cambiar de vista/filtros, la selección deja de tener sentido.
  useEffect(() => { setSelected(new Set()); }, [filters]);

  // Columnas visibles (orden fijo) y columnas fijas a la izquierda al
  // desplazar: casilla, Situación, Nº pedido y Cliente (las que estén a la
  // vista). «Quitar» va fija a la derecha.
  const visibleCols = COLUMNS.filter((c) => !hiddenCols.has(c.key));
  const stickyLeft = [
    ...(canEdit ? ["sel"] : []),
    ...visibleCols.filter((c) => c.sticky).map((c) => c.key as string),
  ];
  function stickyClass(key: string): string {
    const i = stickyLeft.indexOf(key);
    if (i < 0) return "";
    return ` sticky-l sticky-l-${i}${i === stickyLeft.length - 1 ? " sticky-l-last" : ""}`;
  }

  function renderCell(key: ColKey, r: SeguimientoRow): ReactNode {
    switch (key) {
      case "situacion":
        return (
          <>
            {/* Situación = cola de la línea de vida, con color. */}
            <span
              className={`seg-situacion is-${r.situacion_tone}`}
              title={r.nota_incidencia || undefined}
            >
              {r.situacion_label}
            </span>
            {r.completado ? (
              <span className="badge ok" title={`Completado${r.completado_en ? ` el ${d(r.completado_en)}` : ""}${r.completado_por_nombre ? ` por ${r.completado_por_nombre}` : ""} (solo BoHub)`}>
                {" "}completado
              </span>
            ) : null}
            {r.reembolsado && r.situacion !== "reembolsado" ? (
              <span className="badge warn" title="Reembolsado en WooCommerce (reembolso total)">
                {" "}reembolsado
              </span>
            ) : null}
            {r.oculto_por_estado && r.estado_woo_motivo ? (
              <span className="badge bad" title={`Oculto por estado: ${r.woo_status ?? r.estado_woo_motivo}`}>
                {" "}{r.estado_woo_motivo_label || r.estado_woo_motivo}
              </span>
            ) : null}
            {r.forzado ? (
              <span className="badge muted"
                title={`Forzado a la vista pese a su estado${r.forzado_en ? ` el ${d(r.forzado_en)}` : ""}${r.forzado_por_nombre ? ` por ${r.forzado_por_nombre}` : ""}`}>
                {" "}forzado
              </span>
            ) : null}
          </>
        );
      case "pedido":
        // Cada fila enlaza a la ficha del pedido.
        return <Link href={`/erp/orders/${r.id}`}>{r.order_number}</Link>;
      case "fecha":
        return dc(r.fecha);
      case "cliente":
        return <span className="seg-ellipsis">{r.cliente ?? "—"}</span>;
      case "origen":
        return r.origen_label || "—";
      case "productos":
        return <TruncText text={r.productos} />;
      case "importe":
        return eur(r.importe, r.moneda);
      case "empresa":
        return r.empresa_serie || "—";
      case "factura":
        return r.factura ? (
          <span className="erp-factura-cell">
            {r.factura}{" "}
            <button
              type="button"
              className="button small secondary"
              disabled={busy}
              title="Descargar el PDF de la factura"
              onClick={() => void onDownloadRowPdf(r)}
            >
              PDF
            </button>
          </span>
        ) : "—";
      case "fecha_factura":
        return dc(r.fecha_factura);
      case "factura_enviada":
        return dc(r.factura_enviada);
      case "cobro":
        return <span className={`seg-cobro is-${r.cobro}`}>{r.cobro_label}</span>;
      case "preparacion":
        return r.preparacion;
      case "envio":
        return r.envio;
      case "courier":
        return <span className="seg-ellipsis">{r.courier || "—"}</span>;
      case "recogido":
        // Fecha real de recogida (Cola SAT: recogido / en tránsito).
        return dc(r.recogido);
      case "tracking":
        return <span className="seg-ellipsis">{r.tracking ?? "—"}</span>;
      case "serie":
        return <span className="seg-ellipsis">{r.serie_whiterip || "—"}</span>;
      case "nota":
        return <TruncText text={r.nota_incidencia} />;
      default:
        return null;
    }
  }

  /** Título (texto completo / fecha larga) de las celdas que se acortan. */
  function cellTitle(key: ColKey, r: SeguimientoRow): string | undefined {
    switch (key) {
      case "fecha": return r.fecha ? d(r.fecha) : undefined;
      case "fecha_factura": return r.fecha_factura ? d(r.fecha_factura) : undefined;
      case "factura_enviada": return r.factura_enviada ? d(r.factura_enviada) : undefined;
      case "recogido": return r.recogido ? d(r.recogido) : undefined;
      case "cliente": return r.cliente ?? undefined;
      case "empresa": return r.empresa ?? undefined;
      case "tracking": return r.tracking ?? undefined;
      case "courier": return r.courier && r.courier !== "—" ? r.courier : undefined;
      case "serie": return r.serie_whiterip || undefined;
      default: return undefined;
    }
  }

  function onColumnsChange(next: Set<string>) {
    setHiddenCols(next);
    storeHiddenColumns(columnsStorageKey(user), next);
  }

  function toggleSort(key: string | null) {
    if (!key) return;
    setFilters((f) => ({
      ...f,
      sort: key,
      dir: f.sort === key && f.dir !== "asc" ? "asc" : f.sort === key ? "desc" : "asc",
    }));
  }

  function toggleRow(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    const items = page?.items ?? [];
    setSelected((prev) =>
      prev.size === items.length ? new Set() : new Set(items.map((r) => r.id)),
    );
  }

  async function onExport() {
    setBusy(true);
    setError(null);
    try {
      const blob = await exportSeguimientoXlsx(filters);
      saveBlob(blob, `seguimiento_pedidos_${new Date().toISOString().slice(0, 10)}.xlsx`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo exportar el Excel."));
    } finally {
      setBusy(false);
    }
  }

  // Control manual — «Quitar del seguimiento»: abre el diálogo (motivo +
  // avisos) para una fila o para la selección (casillas de fix7). Cualquier
  // pedido, cualquier estado; con factura/cobro/albarán se AVISA, no se bloquea.
  function openExclude(rows: SeguimientoRow[]) {
    if (rows.length === 0) return;
    setError(null);
    setNotice(null);
    setExcludeTarget(rows);
  }

  function onExcludeSelected() {
    const items = page?.items ?? [];
    openExclude(items.filter((r) => selected.has(r.id)));
  }

  async function onConfirmExclude(reason: string, reasonCode?: ExclusionReasonCode) {
    if (!excludeTarget) return;
    setBusy(true);
    setError(null);
    try {
      const r = await excludeSeguimiento(
        excludeTarget.map((x) => x.id), reason || undefined, reasonCode,
      );
      setExcludeTarget(null);
      setNotice(
        `${r.excluded} pedido(s) quitado(s) del seguimiento`
        + (r.already_excluded > 0 ? ` (${r.already_excluded} ya estaban fuera)` : "")
        + ". Se deshace con «Reincluir» en «Ver excluidos».",
      );
      setSelected(new Set());
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron quitar los pedidos del seguimiento."));
    } finally {
      setBusy(false);
    }
  }

  // «Reincluir»: deshace la exclusión (una fila o la selección). Idempotente.
  async function onIncludeRows(ids: string[]) {
    if (ids.length === 0) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await includeSeguimiento(ids);
      setNotice(`${r.included} pedido(s) reincluido(s) en el seguimiento.`);
      setSelected(new Set());
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron reincluir los pedidos."));
    } finally {
      setBusy(false);
    }
  }

  function onIncludeSelected() {
    void onIncludeRows([...selected]);
  }

  // ERP-Woo — «Reincluir» en la vista de OCULTOS POR ESTADO: fuerza el pedido
  // a la vista aunque su estado lo deje fuera (y lo deshace). Es otro eje que
  // la exclusión manual: por eso no vale `includeSeguimiento`.
  async function onForceRows(ids: string[], forced: boolean) {
    if (ids.length === 0) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await forceSeguimiento(ids, forced);
      setNotice(forced
        ? `${r.changed} pedido(s) forzado(s) en el seguimiento pese a su estado.`
        : `${r.changed} pedido(s) ya no se fuerzan: vuelven a estar ocultos por estado.`);
      setSelected(new Set());
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cambiar el forzado de los pedidos."));
    } finally {
      setBusy(false);
    }
  }

  // ERP — descargar el PDF de la factura de una fila (solo lectura). El pedido
  // solo guarda el CODFAC; la serie real (empresa emisora) la resuelve
  // FACTUSOL vía la clave {serie, código}. No marca ni envía nada.
  async function onDownloadRowPdf(row: SeguimientoRow) {
    if (!row.factura) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const ref = await getOrderFactusolInvoiceRef(row.id);
      const blob = await downloadFactusolDocumentPdf("facturas", ref.serie, ref.codigo);
      saveBlob(blob, `Factura_${ref.numero}.pdf`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo descargar el PDF de la factura."));
    } finally {
      setBusy(false);
    }
  }

  // ERP — descargar en ZIP las facturas de los pedidos seleccionados que ya
  // tengan factura. Reutiliza las casillas de fix7. Solo lectura.
  async function onDownloadSelectedPdf() {
    const items = page?.items ?? [];
    const chosen = items.filter((r) => selected.has(r.id) && r.factura);
    if (chosen.length === 0) {
      setError("Ninguno de los pedidos seleccionados tiene factura.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      // Resuelve serie+código real de cada factura; los que no se puedan
      // localizar en FACTUSOL se omiten.
      const refs = await Promise.all(chosen.map(async (r) => {
        try { return await getOrderFactusolInvoiceRef(r.id); }
        catch { return null; }
      }));
      const facturas = refs
        .filter((x): x is NonNullable<typeof x> => x !== null)
        .map((ref) => ({ serie: ref.serie, codigo: ref.codigo }));
      if (facturas.length === 0) {
        setError("No se pudo localizar ninguna factura en FACTUSOL.");
        return;
      }
      const blob = await downloadFacturasPdfZip(facturas);
      saveBlob(blob, "facturas_pdf.zip");
      const faltan = chosen.length - facturas.length;
      setNotice(
        `Descargadas ${facturas.length} factura(s) en ZIP`
        + (faltan > 0 ? ` (${faltan} sin factura localizable, omitidas).` : "."),
      );
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron descargar las facturas."));
    } finally {
      setBusy(false);
    }
  }

  // ERP-F6-fix2 — paso 1: previsualizar (no escribe nada). Bart ve qué hará
  // sobre un fichero que mantiene desde hace años ANTES de confirmar.
  async function onPreview() {
    setBusy(true);
    setError(null);
    setNotice(null);
    setSyncSummary(null);
    setPreviewSummary(null);
    try {
      setPreviewSummary(await syncSeguimientoDrive({ preview: true }));
    } catch (e) {
      setError(extractErrorMessage(
        e,
        "No se pudo previsualizar la hoja de Drive. ¿Está configurada la cuenta de servicio?",
      ));
    } finally {
      setBusy(false);
    }
  }

  // Paso 2: confirmar la escritura.
  async function onConfirmSync() {
    setBusy(true);
    setError(null);
    try {
      const summary = await syncSeguimientoDrive();
      setSyncSummary(summary);
      setPreviewSummary(null);
      setNotice(
        isManagedSummary(summary)
          ? `Hoja actualizada: ${summary.rows} filas de BoHub`
            + (summary.manuales ? ` + ${summary.manuales} añadidas a mano (conservadas)` : "")
            + ` en «${summary.tab}».`
            + (summary.historic_tab ? ` La pestaña «${summary.historic_tab}» no se ha tocado.` : "")
          : `Hoja actualizada: ${summary.appended_rows} filas añadidas. `
            + `${summary.orders_to_review} pedidos a revisar.`,
      );
      await load();
    } catch (e) {
      setError(extractErrorMessage(
        e, "No se pudo actualizar la hoja de Drive.",
      ));
    } finally {
      setBusy(false);
    }
  }

  // ERP-Woo — puesta al día de estados: corre en SEGUNDO PLANO. Se encola y se
  // hace polling del estado; nada de peticiones colgadas (evita el 504).
  // Paso 1: previsualizar (no escribe).
  async function onReconcilePreview() {
    setBusy(true);
    setError(null);
    setNotice(null);
    setReconcile(null);
    try {
      const { job_id } = await reconcileWooStatuses({ preview: true });
      setNotice("Consultando WooCommerce… (en segundo plano)");
      const res = await waitForReconcileWoo(job_id);
      setNotice(null);
      if (res.status === "finished") setReconcile(res.result);
      else if (res.status === "error") {
        setError(res.error || "La puesta al día falló.");
      } else {
        setError("La puesta al día tardó demasiado; vuelve a intentarlo en un momento.");
      }
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo consultar WooCommerce."));
    } finally {
      setBusy(false);
    }
  }

  // Paso 2: aplicar los cambios de estado (también en segundo plano).
  async function onReconcileApply() {
    setBusy(true);
    setError(null);
    setNotice("Aplicando… (en segundo plano)");
    try {
      const { job_id } = await reconcileWooStatuses({ preview: false });
      const res = await waitForReconcileWoo(job_id);
      if (res.status === "finished") {
        const r = res.result;
        setReconcile(null);
        setNotice(
          `Puesta al día aplicada: ${r.removed_total} pedidos salieron del `
          + `seguimiento (${r.to_cancel} cancelados, ${r.to_fail} fallidos, `
          + `${r.to_unpaid} sin pagar / en espera, ${r.to_trash} en papelera); `
          + `${r.to_refunded} quedaron marcados «Reembolsado» (siguen a la vista)`
          + ((r.unknown_total ?? 0) > 0
            ? `; de ${r.unknown_total} sin estado, ${r.to_filled ?? 0} recuperaron el suyo `
              + `y ${r.to_not_found ?? 0} ya no existen en la tienda`
            : "")
          + ((r.to_payment_method ?? 0) > 0
            ? `; ${r.to_payment_method} pedidos web rellenaron su método de pago`
              + `${paymentMethodByStore(r)}.`
            : "."),
        );
        await load();
      } else {
        setNotice(null);
        setError(res.status === "error"
          ? (res.error || "No se pudo aplicar la puesta al día.")
          : "La puesta al día tardó demasiado; vuelve a intentarlo.");
      }
    } catch (e) {
      setNotice(null);
      setError(extractErrorMessage(e, "No se pudo aplicar la puesta al día."));
    } finally {
      setBusy(false);
    }
  }

  // ERP — vincular facturas creadas a mano en FACTUSOL (segundo plano).
  async function onFacturaLinkPreview() {
    setBusy(true); setError(null); setNotice(null); setFacturaLink(null);
    try {
      const { job_id } = await reconcileFactusolInvoices({ preview: true });
      setNotice("Buscando facturas en FACTUSOL… (en segundo plano)");
      const res = await waitForFactusolReconcile(job_id);
      setNotice(null);
      if (res.status === "finished") setFacturaLink(res.result);
      else if (res.status === "error") setError(res.error || "La vinculación falló.");
      else setError("La vinculación tardó demasiado; vuelve a intentarlo.");
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo consultar FACTUSOL."));
    } finally { setBusy(false); }
  }

  async function onFacturaLinkApply() {
    setBusy(true); setError(null); setNotice("Enlazando… (en segundo plano)");
    try {
      const { job_id } = await reconcileFactusolInvoices({ preview: false });
      const res = await waitForFactusolReconcile(job_id);
      if (res.status === "finished") {
        setFacturaLink(null);
        setNotice(
          `Facturas enlazadas: ${res.result.linked}. `
          + `${res.result.conflicts.length} conflictos sin enlazar (revísalos).`,
        );
        await load();
      } else {
        setNotice(null);
        setError(res.status === "error"
          ? (res.error || "No se pudo aplicar la vinculación.")
          : "La vinculación tardó demasiado; vuelve a intentarlo.");
      }
    } catch (e) {
      setNotice(null);
      setError(extractErrorMessage(e, "No se pudo aplicar la vinculación."));
    } finally { setBusy(false); }
  }

  const drive = page?.drive;
  const items = page?.items ?? [];

  return (
    <main className="shell shell-wide erp-seg-page">
      <PageHeader
        title="Seguimiento de pedidos"
        eyebrow="ERP"
        description="Las columnas del Excel de seguimiento, sin copiar nada a mano. Por defecto: pedidos en curso."
        crumbs={[{ label: "ERP" }, { label: "Seguimiento" }]}
      />
      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}

      <section className="erp-card">
        <div className="erp-doc-filters">
          <label className="field">
            <span>Buscar</span>
            <input
              value={q}
              placeholder="cliente, pedido, albarán, factura, tracking o nº de serie"
              aria-label="Buscar en el seguimiento"
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") setFilters({ ...filters, q: q || undefined });
              }}
            />
          </label>
          <label className="field">
            <span>Empresa (serie)</span>
            <select
              aria-label="Filtrar por empresa"
              value={filters.serie ?? ""}
              onChange={(e) => setFilters({
                ...filters, serie: e.target.value ? Number(e.target.value) : undefined,
              })}
            >
              <option value="">Todas</option>
              {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Vendedor</span>
            <input
              aria-label="Filtrar por vendedor"
              value={filters.vendedor ?? ""}
              placeholder="WEB"
              onChange={(e) => setFilters({ ...filters, vendedor: e.target.value || undefined })}
            />
          </label>
          <label className="field">
            <span>Transportista</span>
            <input
              aria-label="Filtrar por transportista"
              title="Filtra por la columna Courier (la agencia de Genei o el courier de la Cola SAT)"
              value={filters.transportista ?? ""}
              placeholder="UPS, Ctt…"
              onChange={(e) => setFilters({
                ...filters, transportista: e.target.value || undefined,
              })}
            />
          </label>
          <label className="field">
            <span>Origen del envío</span>
            <select
              aria-label="Filtrar por origen del envío"
              value={filters.origen ?? ""}
              onChange={(e) => setFilters({ ...filters, origen: e.target.value || undefined })}
            >
              <option value="">Todos</option>
              {origins.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Estado</span>
            <select
              aria-label="Filtrar por estado"
              value={filters.estado ?? ""}
              onChange={(e) => setFilters({
                ...filters,
                estado: (e.target.value || undefined) as SeguimientoFilters["estado"],
              })}
            >
              <option value="">Todos</option>
              <option value="pendiente">Pendiente</option>
              <option value="enviado">Enviado</option>
              <option value="facturado">Facturado</option>
              <option value="completado">Completado</option>
            </select>
          </label>
          <label className="field">
            <span>Desde</span>
            <input
              type="date" aria-label="Fecha desde" value={filters.desde ?? ""}
              onChange={(e) => setFilters({ ...filters, desde: e.target.value || undefined })}
            />
          </label>
          <label className="field">
            <span>Hasta</span>
            <input
              type="date" aria-label="Fecha hasta" value={filters.hasta ?? ""}
              onChange={(e) => setFilters({ ...filters, hasta: e.target.value || undefined })}
            />
          </label>
          <label className="field erp-check-field">
            <input
              type="checkbox"
              aria-label="Solo pedidos en curso"
              checked={filters.en_curso !== false}
              disabled={viewExcluded}
              onChange={(e) => setFilters({
                ...filters, en_curso: e.target.checked ? undefined : false,
              })}
            />
            <span>Solo en curso</span>
          </label>
          {/* ERP-F6-fix7 — pendientes de escribir en Drive vs excluidos. */}
          <label className="field erp-check-field">
            <input
              type="checkbox"
              aria-label="Solo pendientes de escribir en Drive"
              checked={filters.pendiente_escribir === true}
              disabled={viewExcluded}
              onChange={(e) => setFilters({
                ...filters, pendiente_escribir: e.target.checked ? true : undefined,
              })}
            />
            <span>Solo pendientes de escribir</span>
          </label>
          <label className="field erp-check-field">
            <input
              type="checkbox"
              aria-label="Ver pedidos excluidos del seguimiento"
              checked={viewExcluded}
              onChange={(e) => setFilters({
                ...filters, ver_excluidos: e.target.checked ? true : undefined,
              })}
            />
            <span>Ver excluidos</span>
          </label>
          {/* ERP-Woo — ocultados por estado: anulados y los web que la tienda
              no llegó a procesar (sin pagar, en espera, cancelado, fallido,
              borrador). Se pueden «Reincluir» desde aquí. */}
          <label className="field erp-check-field">
            <input
              type="checkbox"
              aria-label="Ver pedidos ocultados por estado de WooCommerce"
              checked={viewOcultos}
              onChange={(e) => setFilters({
                ...filters, ver_ocultos_estado: e.target.checked ? true : undefined,
              })}
            />
            <span>Ver ocultados por estado</span>
          </label>
          <button type="button" className="button small secondary" disabled={busy}
            onClick={onExport}>
            Descargar Excel
          </button>
          {/* «Columnas»: qué se ve en la tabla (orden fijo, recordado por
              usuario en este navegador). El Excel y la hoja siguen con todas. */}
          <ColumnPicker
            columns={COLUMNS.map((c) => ({ key: c.key, label: c.label, locked: c.locked }))}
            hidden={hiddenCols}
            onChange={onColumnsChange}
          />
          {canEdit && !viewExcluded && !viewOcultos ? (
            <button
              type="button" className="button small" disabled={busy}
              title={drive && !drive.configured
                ? "Falta configurar la cuenta de servicio y la hoja en Configuración ERP"
                : undefined}
              onClick={onPreview}
            >
              {busy ? "Trabajando…" : "Actualizar hoja de Drive…"}
            </button>
          ) : null}
          {canEdit ? (
            <button
              type="button" className="button small secondary" disabled={busy}
              title="Re-consulta WooCommerce: saca los cancelados / fallidos / sin pagar, marca los reembolsados y rellena el método de pago de los pedidos web que no lo tienen"
              onClick={onReconcilePreview}
            >
              {busy ? "Trabajando…" : "Poner al día estados Woo…"}
            </button>
          ) : null}
          {canEdit ? (
            <button
              type="button" className="button small secondary" disabled={busy}
              title="Enlaza a los pedidos las facturas creadas a mano en FACTUSOL (por referencia)"
              onClick={onFacturaLinkPreview}
            >
              {busy ? "Trabajando…" : "Vincular facturas de FACTUSOL…"}
            </button>
          ) : null}
          {canEdit && selected.size > 0 ? (
            <>
              <button type="button" className="button small" disabled={busy}
                onClick={onDownloadSelectedPdf}>
                Descargar facturas (PDF) ({selected.size})
              </button>
              {viewOcultos ? (
                <button type="button" className="button small" disabled={busy}
                  title="Fuerza los seleccionados a la vista pese a su estado"
                  onClick={() => void onForceRows([...selected], true)}>
                  Reincluir ({selected.size})
                </button>
              ) : viewExcluded ? (
                <button type="button" className="button small" disabled={busy}
                  onClick={onIncludeSelected}>
                  Reincluir ({selected.size})
                </button>
              ) : (
                <button type="button" className="button small danger" disabled={busy}
                  title="Quita los seleccionados del seguimiento (reversible; no borra nada)"
                  onClick={onExcludeSelected}>
                  Quitar del seguimiento ({selected.size})
                </button>
              )}
            </>
          ) : null}
        </div>
        {drive && !drive.configured ? (
          <p className="muted small">
            La hoja de Drive no está configurada (la vista funciona igual).
            Un admin puede añadir la cuenta de servicio y el ID de la hoja en
            Configuración ERP.
          </p>
        ) : null}
        {drive?.configured && drive.service_account_email ? (
          <p className="muted small">
            Hoja de Drive conectada con {drive.service_account_email}. La
            sincronización solo AÑADE pedidos nuevos: nunca borra filas, nunca
            reescribe una fila ya escrita ni pisa celdas editadas a mano.
          </p>
        ) : null}
      </section>

      {facturaLink ? (
        <section className="erp-card">
          <h3>Vincular facturas de FACTUSOL — previsualización</h3>
          <p className="muted small">
            Pedidos no facturados en BoHub que ya tienen factura en FACTUSOL
            (por referencia). Nada se ha escrito todavía; solo se enlaza en BoHub.
          </p>
          <ul className="item-list">
            <li><strong>{facturaLink.to_link.length}</strong> facturas a enlazar.</li>
            <li className="muted small">
              {facturaLink.no_match} pedidos sin factura en FACTUSOL (se dejan como están).
            </li>
          </ul>
          {facturaLink.to_link.length > 0 ? (
            <ul className="item-list">
              {facturaLink.to_link.map((it) => (
                <li key={it.order_id} className="small">
                  <strong>{it.order_number}</strong> → factura {it.numero}
                  <span className="muted"> (ref {it.ref}
                    {it.total != null ? `, ${it.total} €` : ""}
                    {it.fecha ? `, ${it.fecha}` : ""})</span>
                </li>
              ))}
            </ul>
          ) : null}
          {facturaLink.conflicts.length > 0 ? (
            <>
              <h4>Conflictos — NO se enlazan (decide tú)</h4>
              <p className="muted small">
                Pedidos con MÁS de una factura para la misma referencia: hay que
                anular una. No se enlaza ninguna automáticamente.
              </p>
              <ul className="item-list">
                {facturaLink.conflicts.map((c, i) => (
                  <li key={i} className="small">
                    <strong>{c.order_number}</strong> (ref {c.ref}):{" "}
                    {c.facturas.map((f) => f.numero).join(" · ")}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          <div className="modal-actions">
            <button type="button" className="button secondary" disabled={busy}
              onClick={() => setFacturaLink(null)}>
              Cancelar
            </button>
            <button type="button" className="button" disabled={busy || facturaLink.to_link.length === 0}
              onClick={onFacturaLinkApply}>
              {busy ? "Enlazando…" : `Enlazar ${facturaLink.to_link.length} facturas`}
            </button>
          </div>
        </section>
      ) : null}

      {reconcile ? (
        <section className="erp-card">
          <h3>Puesta al día de estados WooCommerce — previsualización</h3>
          <p className="muted small">
            Re-consultados {reconcile.scanned} pedidos activos. Nada se ha
            cambiado todavía.{reconcile.capped
              ? ` (Tope ${reconcile.limit}: vuelve a ejecutar para el resto.)`
              : ""}
          </p>
          <ul className="item-list">
            <li>
              <strong>{reconcile.removed_total}</strong> saldrían del seguimiento:{" "}
              {reconcile.to_cancel} cancelados · {reconcile.to_fail} fallidos ·{" "}
              {reconcile.to_unpaid} sin pagar / en espera ·{" "}
              {reconcile.to_trash} en papelera.
            </li>
            <li>
              <strong>{reconcile.to_refunded}</strong> quedarían marcados
              «Reembolsado». No salen del seguimiento: es su propio estado.
            </li>
            {(reconcile.unknown_total ?? 0) > 0 ? (
              <li>
                <strong>{reconcile.unknown_total}</strong> sin estado, consultados
                uno a uno: {reconcile.to_filled ?? 0} recuperarían su estado real ·{" "}
                {reconcile.to_not_found ?? 0} ya no existen en la tienda (quedan ocultos).
              </li>
            ) : null}
            {(reconcile.to_payment_method ?? 0) > 0 || (reconcile.payment_method_pending ?? 0) > 0 ? (
              <li aria-label="Métodos de pago">
                <strong>{reconcile.to_payment_method ?? 0}</strong> pedidos web sin método
                de pago lo rellenarían (cambien o no de estado)
                {paymentMethodByStore(reconcile)}
                {(reconcile.payment_method_pending ?? 0) > (reconcile.to_payment_method ?? 0)
                  ? ` · ${(reconcile.payment_method_pending ?? 0) - (reconcile.to_payment_method ?? 0)} `
                    + "siguen sin él (la tienda no lo tiene)"
                  : ""}.
              </li>
            ) : null}
            <li className="muted small">
              {reconcile.unchanged} siguen activos.
              {reconcile.errors.length > 0
                ? ` ${reconcile.errors.length} no se pudieron consultar.`
                : ""}
            </li>
          </ul>
          <div className="modal-actions">
            <button type="button" className="button secondary" disabled={busy}
              onClick={() => setReconcile(null)}>
              Cancelar
            </button>
            <button type="button" className="button" disabled={busy}
              onClick={onReconcileApply}>
              {busy ? "Aplicando…" : `Aplicar (${reconcileChanges(reconcile)} cambios)`}
            </button>
          </div>
        </section>
      ) : null}

      {previewSummary && isManagedSummary(previewSummary) ? (
        <section className="erp-card">
          <h3>Previsualización — revisa antes de escribir</h3>
          <ManagedPreview summary={previewSummary} />
          <div className="modal-actions">
            <button type="button" className="button secondary" disabled={busy}
              onClick={() => setPreviewSummary(null)}>
              Cancelar
            </button>
            <button type="button" className="button" disabled={busy}
              onClick={onConfirmSync}>
              {busy
                ? "Escribiendo…"
                : `Confirmar y escribir ${previewSummary.rows + (previewSummary.manuales ?? 0)} filas`}
            </button>
          </div>
        </section>
      ) : null}

      {previewSummary && !isManagedSummary(previewSummary) ? (
        <section className="erp-card">
          <h3>Previsualización — revisa antes de escribir</h3>
          <p className="muted small">
            Sobre una hoja de {previewSummary.sheet_rows} filas. Nada se ha
            escrito todavía. La sincronización solo AÑADE.
          </p>
          {/* 1) A AÑADIR */}
          <ul className="item-list">
            <li><strong>{previewSummary.appended_rows}</strong> filas a añadir (pedidos que no estaban).</li>
            <li className="muted small">
              {previewSummary.already_present} pedidos ya estaban en la hoja: no se tocan.
            </li>
            {previewSummary.omitted_columns.length > 0 ? (
              <li>Columnas omitidas (no están en la hoja): {previewSummary.omitted_columns.join(", ")}.</li>
            ) : null}
          </ul>
          {/* 2) PARA TU INFORMACIÓN (no requiere decisión) */}
          <UnknownInvoices items={previewSummary.unknown_invoices} />
          {/* 3) CONFLICTOS REALES (a revisar) */}
          {previewSummary.review_groups.length > 0 ? (
            <>
              <h4>Conflictos reales — a revisar ({previewSummary.orders_to_review})</h4>
              <p className="muted small">
                Contradicciones o coincidencias probables; no se añadirán ni se
                tocarán hasta que lo resuelvas.
              </p>
              <ReviewGroups groups={previewSummary.review_groups} />
            </>
          ) : null}
          <div className="modal-actions">
            <button type="button" className="button secondary" disabled={busy}
              onClick={() => setPreviewSummary(null)}>
              Cancelar
            </button>
            <button type="button" className="button" disabled={busy}
              onClick={onConfirmSync}>
              {busy ? "Escribiendo…" : `Confirmar y añadir ${previewSummary.appended_rows} filas`}
            </button>
          </div>
        </section>
      ) : null}

      {syncSummary && isManagedSummary(syncSummary) ? (
        <section className="erp-card">
          <h3>Hoja actualizada</h3>
          <p className="muted small">
            {syncSummary.rows} filas de BoHub
            {syncSummary.manuales ? ` + ${syncSummary.manuales} a mano` : ""} en
            «{syncSummary.tab}» ·{" "}
            {syncSummary.incidencias} en «{syncSummary.incidencias_tab}».
            {syncSummary.created_tabs?.length
              ? ` Pestañas creadas: ${syncSummary.created_tabs.join(", ")}.`
              : ""}
          </p>
          {syncSummary.historic_tab ? (
            <p className="muted small">
              La pestaña «{syncSummary.historic_tab}» no se ha tocado.
            </p>
          ) : null}
          <CourierMigrationDone summary={syncSummary} />
        </section>
      ) : null}

      {syncSummary && !isManagedSummary(syncSummary) ? (
        <section className="erp-card">
          <h3>Hoja actualizada</h3>
          <p className="muted small">
            {syncSummary.appended_rows} filas añadidas · {syncSummary.already_present} ya
            estaban.
          </p>
          <UnknownInvoices items={syncSummary.unknown_invoices} />
          {syncSummary.review_groups.length > 0 ? (
            <>
              <h4>A revisar ({syncSummary.orders_to_review} pedidos)</h4>
              <ReviewGroups groups={syncSummary.review_groups} />
            </>
          ) : null}
        </section>
      ) : null}

      <section className="erp-card">
        <p className="muted small" role="status">
          {page ? `${page.total} pedidos` : "Cargando…"}
          {viewExcluded
            ? " excluidos"
            : viewOcultos
              ? " ocultados por estado (anulado, sin pagar, en espera, cancelado…)"
              : filters.en_curso === false ? " (todos)" : " en curso"}
        </p>
        <ScrollTable label="Tabla de seguimiento" className="erp-seg-scroll">
          <table className="data-table erp-seguimiento-table is-compact">
            <thead>
              <tr>
                {canEdit ? (
                  <th className={`seg-col-sel${stickyClass("sel")}`}>
                    <input
                      type="checkbox"
                      aria-label="Seleccionar todo"
                      checked={items.length > 0 && selected.size === items.length}
                      onChange={toggleAll}
                    />
                  </th>
                ) : null}
                {visibleCols.map((h) => (
                  <th
                    key={h.key}
                    className={`seg-col-${h.key}${h.sort ? " sortable" : ""}${stickyClass(h.key)}`}
                    aria-sort={filters.sort === h.sort
                      ? (filters.dir === "asc" ? "ascending" : "descending")
                      : undefined}
                    onClick={() => toggleSort(h.sort)}
                  >
                    {h.label}
                    {filters.sort === h.sort ? (filters.dir === "asc" ? " ↑" : " ↓") : ""}
                  </th>
                ))}
                {/* Control manual — en «Ver excluidos»: cuándo, quién y por qué. */}
                {viewExcluded ? <th className="seg-col-quitado">Quitado</th> : null}
                {canEdit ? <th className="seg-col-acciones sticky-r" aria-label="Acciones" /> : null}
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id} className={r.excluido ? "muted" : undefined}>
                  {canEdit ? (
                    <td className={`seg-col-sel${stickyClass("sel")}`}>
                      <input
                        type="checkbox"
                        aria-label={`Seleccionar ${r.albaran_pedido}`}
                        checked={selected.has(r.id)}
                        onChange={() => toggleRow(r.id)}
                      />
                    </td>
                  ) : null}
                  {visibleCols.map((c) => (
                    <td key={c.key} className={`seg-col-${c.key}${cellClass(c.key, r)}${stickyClass(c.key)}`}
                        title={cellTitle(c.key, r)}>
                      {renderCell(c.key, r)}
                    </td>
                  ))}
                  {viewExcluded ? (
                    <td className="seg-col-quitado small">
                      {d(r.excluido_en)}{r.excluido_por_nombre ? ` · ${r.excluido_por_nombre}` : ""}
                      <br />
                      <span className="muted">{r.excluido_motivo || "sin motivo"}</span>
                    </td>
                  ) : null}
                  {canEdit ? (
                    <td className="seg-col-acciones sticky-r">
                      {viewOcultos ? (
                        <button
                          type="button" className="button small secondary" disabled={busy}
                          title={r.forzado
                            ? "Deja de forzarlo: vuelve a estar oculto por su estado"
                            : "Fuérzalo a la vista del seguimiento pese a su estado"}
                          aria-label={r.forzado
                            ? `Dejar de forzar ${r.albaran_pedido} en el seguimiento`
                            : `Reincluir ${r.albaran_pedido} en el seguimiento`}
                          onClick={() => void onForceRows([r.id], !r.forzado)}
                        >
                          {r.forzado ? "Dejar de forzar" : "Reincluir"}
                        </button>
                      ) : r.excluido ? (
                        <button
                          type="button" className="button small secondary" disabled={busy}
                          title="Vuelve a incluir este pedido en el seguimiento"
                          aria-label={`Reincluir ${r.albaran_pedido} en el seguimiento`}
                          onClick={() => void onIncludeRows([r.id])}
                        >
                          Reincluir
                        </button>
                      ) : (
                        <button
                          type="button" className="button small secondary" disabled={busy}
                          title="Quitar del seguimiento (reversible; no borra nada)"
                          aria-label={`Quitar ${r.albaran_pedido} del seguimiento`}
                          onClick={() => openExclude([r])}
                        >
                          Quitar
                        </button>
                      )}
                    </td>
                  ) : null}
                </tr>
              ))}
              {page && items.length === 0 ? (
                <tr>
                  <td colSpan={visibleCols.length + (canEdit ? 2 : 0) + (viewExcluded ? 1 : 0)} className="muted">
                    {viewExcluded
                      ? "No hay pedidos excluidos."
                      : viewOcultos
                        ? "No hay pedidos ocultados por estado."
                        : "Sin pedidos."}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </ScrollTable>
      </section>

      {excludeTarget ? (
        <ExcludeSeguimientoModal
          rows={excludeTarget.map((r) => ({
            id: r.id, order_number: r.albaran_pedido, cliente: r.cliente,
          }))}
          busy={busy}
          onConfirm={onConfirmExclude}
          onCancel={() => setExcludeTarget(null)}
        />
      ) : null}
    </main>
  );
}

const REVIEW_KIND_LABEL: Record<string, string> = {
  contradicted: "contradicción",
  ambiguous: "coincidencia ambigua",
  probable_match: "coincidencia probable",
};

/** Vista previa: la pestaña aún no tiene la columna «Courier». Con 19
 *  columnas se inserta (con recuento de celdas); con 17, se reescribe. */
function CourierMigrationPending({ summary }: { summary: DriveManagedSummary }) {
  const m = summary.migracion_courier;
  if (m?.estado !== "pendiente") return null;
  if (m.formato === "sin_recogido") {
    return (
      <p className="form-info small" role="note">
        La pestaña está escrita con un formato antiguo (17 columnas, sin
        «Fecha recogido» ni <strong>«Courier»</strong>): al confirmar se reescribe
        entera con las 20 columnas; lo de detrás de «Envío» corre dos posiciones.
      </p>
    );
  }
  return (
    <>
      <p className="form-info small" role="note">
        La pestaña aún no tiene la columna <strong>«Courier»</strong>: al confirmar
        se insertará entre «Envío» y «Fecha recogido», en la cabecera y en todas
        las filas (también el histórico), sin mover nada más. Antes de seguir se
        comprueba que no se pierde ninguna de sus {m.celdas_antes ?? 0} celdas
        con dato.
      </p>
      {m.celdas_que_no_caben ? (
        <p className="form-error small" role="alert">
          La columna Z tiene {m.celdas_que_no_caben} celda(s) con dato: al insertar
          la columna se saldrían del rango de la app, así que la actualización se
          parará sin escribir nada. Muévelas a otra pestaña (o bórralas) antes.
        </p>
      ) : null}
    </>
  );
}

/** Tras escribir: la columna «Courier» ya está en la hoja. «Ninguna perdida»
 *  solo si el recuento de celdas cuadra (si no cuadrara, la pasada se habría
 *  parado; aun así no se afirma). */
function CourierMigrationDone({ summary }: { summary: DriveManagedSummary }) {
  const m = summary.migracion_courier;
  if (m?.estado === "reescrita") {
    return (
      <p className="muted small" role="note">
        La pestaña «{summary.tab}» estaba en un formato antiguo (17 columnas) y se
        ha reescrito con las 20, con «Courier» entre «Envío» y «Fecha recogido».
      </p>
    );
  }
  if (m?.estado !== "hecha") return null;
  const antes = m.celdas_antes ?? 0;
  const despues = m.celdas_despues ?? 0;
  return (
    <p className="muted small" role="note">
      Columna «Courier» añadida en «{summary.tab}» (entre «Envío» y «Fecha
      recogido»): {m.filas ?? 0} filas, {antes} celdas con dato antes y {despues}{" "}
      después —{" "}
      {antes === despues ? "ninguna perdida." : "no cuadra: revisa la hoja."}
    </p>
  );
}

/** ERP-F6-fix4 — a revisar, agrupado POR PEDIDO (Parte G). Cada pedido lista
 *  sus motivos; nada se toca, Bart decide. */
/** Previsualización del volcado a la pestaña gestionada: a qué pestañas va,
 *  cuántas filas y el desglose por Situación — y que el histórico no se toca,
 *  que es lo que a Bart le importa antes de pulsar. */
function ManagedPreview({ summary }: { summary: DriveManagedSummary }) {
  const situaciones = Object.entries(summary.por_situacion);
  return (
    <>
      <p className="muted small">
        Se reescribirá la pestaña <strong>«{summary.tab}»</strong> con las{" "}
        {summary.columns.length} columnas del seguimiento, ordenada por fecha
        del pedido (más reciente primero) y con la cabecera congelada. Nada se
        ha escrito todavía.
      </p>
      <CourierMigrationPending summary={summary} />
      <ul className="item-list">
        <li>
          Se escribirán <strong>{summary.rows}</strong> filas de BoHub (los mismos
          pedidos que la vista en curso; fuera los excluidos y los ocultos por
          estado)
          {summary.manuales ? (
            <>
              {" "}+ <strong>{summary.manuales}</strong> añadidas a mano
              (Origen = MANUAL), que se <strong>conservan</strong>, mezcladas
              por fecha con las de BoHub
            </>
          ) : null}
          .
        </li>
        {summary.manuales_fusionadas ? (
          <li>
            <strong>{summary.manuales_fusionadas}</strong> fila(s) a mano ya
            tienen su pedido en BoHub: una sola fila, con los huecos rellenados
            desde BoHub
            {summary.manuales_entregadas
              ? ` (${summary.manuales_entregadas} pasan a BoHub: ya no tenían nada que perder)`
              : ""}
            {summary.conflictos
              ? `; ${summary.conflictos} dato(s) no coinciden: se conserva lo escrito a mano y se marca «[⚠ BoHub …]» en la Nota`
              : ""}
            .
          </li>
        ) : null}
        <li>
          <strong>{summary.incidencias}</strong> en «{summary.incidencias_tab}»
          (Situación = Incidencia).
        </li>
        {situaciones.length > 0 ? (
          <li className="muted small">
            {situaciones.map(([label, n]) => `${label}: ${n}`).join(" · ")}
          </li>
        ) : null}
        {summary.historico_preservado ? (
          <li>
            <strong>{summary.historico_preservado}</strong> filas del histórico se
            conservan bajo el separador (no se reescriben).
          </li>
        ) : null}
        {summary.pendientes_preservados ? (
          <li>
            <strong>{summary.pendientes_preservados}</strong> pendientes heredados
            se conservan en «{summary.incidencias_tab}».
          </li>
        ) : null}
        {summary.historic_tab ? (
          <li className="muted small">
            La pestaña «{summary.historic_tab}» no se toca.
          </li>
        ) : null}
      </ul>
      {summary.espejo ? <EspejoResumen e={summary.espejo} /> : null}
    </>
  );
}

/** Espejo bidireccional (Fase 2): qué se ha leído de la hoja y qué se hará. En
 *  la PRIMERA pasada es lo que conviene revisar antes de encender el automático
 *  (p. ej. que «histórico nuevo» sea ~0: si no, el casado del histórico falla). */
function EspejoResumen({ e }: { e: SeguimientoEspejoStats }) {
  const n = (v?: number) => v ?? 0;
  return (
    <div className="erp-espejo-resumen" aria-label="Espejo BoHub ↔ hoja">
      <p className="muted small">
        <strong>Espejo BoHub ↔ hoja</strong>
        {e.bootstrap ? " — primera pasada: se toma la foto, no se lee ninguna edición" : ""}
      </p>
      <ul className="item-list">
        <li>
          Ediciones a mano leídas: <strong>{n(e.ediciones_leidas)}</strong>
          {n(e.tracking_genei_ignorados)
            ? ` (${n(e.tracking_genei_ignorados)} Tracking con envío Genei: manda Genei)`
            : ""}
        </li>
        <li>
          Filas a mano: <strong>{n(e.manuales_nuevas)}</strong> nuevas con id
          {n(e.manuales_invalidas) ? (
            <>, <strong>{n(e.manuales_invalidas)}</strong> a revisar (marcadas «⚠ revisar», no se ingieren)</>
          ) : null}
        </li>
        <li>
          Histórico: <strong>{n(e.historico_ids_asignados)}</strong> ids asignados,{" "}
          {n(e.historico_editadas)} editadas, {n(e.historico_nuevas)} nuevas,{" "}
          {n(e.historico_duplicados_suprimidos)} duplicados quitados (ya salen arriba)
          {n(e.historico_no_encontradas)
            ? `, ${n(e.historico_no_encontradas)} de BoHub no están en la hoja`
            : ""}
        </li>
        {n(e.borradas) ? (
          <li>
            <strong>{n(e.borradas)}</strong> fila(s) borradas a mano: se quitan (borrado
            lógico en BoHub, recuperables)
          </li>
        ) : null}
        {e.borrado_masivo ? (
          <li className="form-error">
            Han desaparecido {n(e.restauradas)} filas de golpe: se trata como un
            accidente y se vuelven a poner.
          </li>
        ) : null}
        {e.proteccion_error ? (
          <li className="form-error">No se pudo proteger la hoja: {e.proteccion_error}</li>
        ) : null}
      </ul>
    </div>
  );
}

function ReviewGroups({ groups }: { groups: DriveSyncReviewGroup[] }) {
  if (groups.length === 0) return null;
  return (
    <ul className="item-list">
      {groups.map((g, gi) => (
        <li key={gi}>
          <strong>{g.order_number ?? "(sin nº)"}</strong>
          <ul>
            {g.items.map((c, i) => (
              <li key={i} className="muted small">
                <span className="badge muted">{REVIEW_KIND_LABEL[c.kind] ?? c.kind}</span>{" "}
                {c.detail}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

/** ERP-F6-fix7 — «facturas de tu hoja que BoHub no conoce»: es INFORMACIÓN, no
 *  requiere decisión. Va plegada, aparte de «a revisar». */
function UnknownInvoices({ items }: { items: DriveSyncUnknownInvoice[] }) {
  if (!items || items.length === 0) return null;
  return (
    <details className="erp-info-block">
      <summary>
        Para tu información — facturas de tu hoja que BoHub no conoce ({items.length})
      </summary>
      <ul className="item-list">
        {items.map((c, i) => (
          <li key={i} className="muted small">
            <strong>{c.order_number ?? "(sin nº)"}</strong> · {c.detail}
          </li>
        ))}
      </ul>
    </details>
  );
}
