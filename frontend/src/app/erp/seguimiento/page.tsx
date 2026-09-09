"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { getCurrentUser, type User } from "../../lib/api";
import {
  ERP_EDIT_ROLES,
  excludeSeguimiento,
  exportSeguimientoXlsx,
  getErpSettings,
  includeSeguimiento,
  listSeguimiento,
  reconcileFactusolInvoices,
  reconcileWooStatuses,
  saveBlob,
  waitForFactusolReconcile,
  waitForReconcileWoo,
  type FactusolLinkSummary,
  syncSeguimientoDrive,
  type DriveSyncReviewGroup,
  type DriveSyncSummary,
  type DriveSyncUnknownInvoice,
  type SeguimientoFilters,
  type SeguimientoPage,
  type WooReconcileSummary,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const ESTADO_TONE: Record<string, string> = {
  pendiente: "warn",
  enviado: "info",
  facturado: "ok",
};

/** Cabeceras de la tabla con su clave de orden (null = no ordenable). */
const HEADERS: { label: string; sort: string | null }[] = [
  { label: "Empresa", sort: "empresa" },
  { label: "Fecha", sort: "fecha" },
  { label: "Cliente", sort: "cliente" },
  { label: "Vendedor", sort: "vendedor" },
  { label: "OFI-TER-SAT", sort: "origen" },
  { label: "Transport", sort: "transportista" },
  { label: "Preparado", sort: null },
  { label: "Recogido", sort: null },
  { label: "F Envío Factura", sort: null },
  { label: "Productos", sort: null },
  { label: "Proforma", sort: null },
  { label: "Albarán / Nº Pedido Web", sort: "albaran_pedido" },
  { label: "Nº de Factura", sort: "factura" },
  { label: "Tracking", sort: null },
  { label: "Nº de Serie", sort: null },
  { label: "WhiteRIP", sort: null },
  { label: "Drive", sort: null },
  { label: "Estado", sort: "estado" },
];

function d(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, day] = iso.split("-");
  return `${Number(day)}/${Number(m)}/${y}`;
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
  const [filters, setFilters] = useState<SeguimientoFilters>({});
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
  // ERP-Woo — previsualización de la puesta al día de estados de WooCommerce.
  const [reconcile, setReconcile] = useState<WooReconcileSummary | null>(null);
  // ERP — previsualización de la vinculación de facturas de FACTUSOL.
  const [facturaLink, setFacturaLink] = useState<FactusolLinkSummary | null>(null);

  const canEdit = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);
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
    getCurrentUser().then(setUser).catch(() => undefined);
    getErpSettings()
      .then((cfg) => setOrigins(cfg.shipping_origins ?? []))
      .catch(() => setOrigins([]));
  }, []);

  useEffect(() => { void load(); }, [load]);
  // Al cambiar de vista/filtros, la selección deja de tener sentido.
  useEffect(() => { setSelected(new Set()); }, [filters]);

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

  // ERP-F6-fix7 — excluir del seguimiento los pedidos seleccionados.
  async function onExcludeSelected() {
    if (selected.size === 0) return;
    const reason = window.prompt(
      `Vas a excluir ${selected.size} pedido(s) del seguimiento. `
      + "No se borra ni cambia nada del pedido; solo dejan de listarse y de "
      + "escribirse en la hoja. Motivo (opcional):",
      "",
    );
    if (reason === null) return;   // cancelado
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await excludeSeguimiento([...selected], reason.trim() || undefined);
      setNotice(`${r.excluded} pedido(s) excluido(s) del seguimiento.`);
      setSelected(new Set());
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron excluir los pedidos."));
    } finally {
      setBusy(false);
    }
  }

  async function onIncludeSelected() {
    if (selected.size === 0) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await includeSeguimiento([...selected]);
      setNotice(`${r.included} pedido(s) reincluido(s) en el seguimiento.`);
      setSelected(new Set());
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron reincluir los pedidos."));
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
        `Hoja actualizada: ${summary.appended_rows} filas añadidas. `
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
          + `${r.to_refund_out} reembolsos no cumplidos, ${r.to_trash} en papelera); `
          + `${r.to_refund_kept} reembolsos ya cumplidos quedaron marcados.`,
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
    <main className="shell shell-wide">
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
              value={filters.transportista ?? ""}
              placeholder="UPS, MRW…"
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
          {/* ERP-Woo — ocultados por estado (cancelado/reembolsado/fallido). */}
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
              title="Re-consulta WooCommerce y saca del seguimiento los cancelados / reembolsados / fallidos"
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
            viewExcluded ? (
              <button type="button" className="button small" disabled={busy}
                onClick={onIncludeSelected}>
                Reincluir ({selected.size})
              </button>
            ) : (
              <button type="button" className="button small danger" disabled={busy}
                onClick={onExcludeSelected}>
                Excluir del seguimiento ({selected.size})
              </button>
            )
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
              {reconcile.to_refund_out} reembolsos no cumplidos ·{" "}
              {reconcile.to_trash} en papelera.
            </li>
            <li>
              <strong>{reconcile.to_refund_kept}</strong> reembolsos ya cumplidos
              se quedarían, marcados «reembolsado».
            </li>
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
              {busy ? "Aplicando…" : `Aplicar (${reconcile.removed_total + reconcile.to_refund_kept} cambios)`}
            </button>
          </div>
        </section>
      ) : null}

      {previewSummary ? (
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

      {syncSummary ? (
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
              ? " ocultados por estado (cancelado/reembolsado/fallido)"
              : filters.en_curso === false ? " (todos)" : " en curso"}
        </p>
        <div style={{ overflowX: "auto" }}>
          <table className="data-table erp-seguimiento-table">
            <thead>
              <tr>
                {canEdit ? (
                  <th>
                    <input
                      type="checkbox"
                      aria-label="Seleccionar todo"
                      checked={items.length > 0 && selected.size === items.length}
                      onChange={toggleAll}
                    />
                  </th>
                ) : null}
                {HEADERS.map((h) => (
                  <th
                    key={h.label}
                    className={h.sort ? "sortable" : undefined}
                    aria-sort={filters.sort === h.sort
                      ? (filters.dir === "asc" ? "ascending" : "descending")
                      : undefined}
                    onClick={() => toggleSort(h.sort)}
                  >
                    {h.label}
                    {filters.sort === h.sort ? (filters.dir === "asc" ? " ↑" : " ↓") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id} className={r.excluido ? "muted" : undefined}>
                  {canEdit ? (
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Seleccionar ${r.albaran_pedido}`}
                        checked={selected.has(r.id)}
                        onChange={() => toggleRow(r.id)}
                      />
                    </td>
                  ) : null}
                  <td title={r.empresa ?? undefined}>{r.empresa_corta || "—"}</td>
                  <td>{d(r.fecha)}</td>
                  <td>{r.cliente ?? "—"}</td>
                  <td>{r.vendedor || "—"}</td>
                  <td>{r.origen ?? "—"}</td>
                  <td>{r.transportista ?? "—"}</td>
                  <td>{d(r.preparado)}</td>
                  <td>{d(r.recogido)}</td>
                  <td>{d(r.fecha_envio_factura)}</td>
                  <td className="muted small" title={r.productos}>
                    {r.productos.length > 60 ? `${r.productos.slice(0, 60)}…` : r.productos || "—"}
                  </td>
                  <td>{r.proforma ?? "—"}</td>
                  <td>
                    {/* Cada fila enlaza a la ficha del pedido. */}
                    <Link href={`/erp/orders/${r.id}`}>{r.albaran_pedido}</Link>
                  </td>
                  <td>{r.factura ?? "—"}</td>
                  <td className="muted small">{r.tracking ?? "—"}</td>
                  <td className="muted small">{r.num_serie ?? "—"}</td>
                  <td>
                    <span className={`badge ${r.escrito_drive ? "ok" : "muted"}`}>
                      {r.escrito_drive ? "escrito" : "pendiente"}
                    </span>
                  </td>
                  <td>
                    <span className={`badge ${ESTADO_TONE[r.estado] ?? "muted"}`}>
                      {r.estado}
                    </span>
                    {r.reembolsado ? (
                      <span className="badge warn" title="Reembolsado en WooCommerce (ya enviado/facturado)">
                        {" "}reembolsado
                      </span>
                    ) : null}
                    {r.oculto_por_estado && r.estado_woo_motivo ? (
                      <span className="badge bad" title={`Oculto por estado de WooCommerce: ${r.woo_status ?? ""}`}>
                        {" "}{r.estado_woo_motivo}
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
              {page && items.length === 0 ? (
                <tr>
                  <td colSpan={HEADERS.length + (canEdit ? 1 : 0)} className="muted">
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
        </div>
      </section>
    </main>
  );
}

const REVIEW_KIND_LABEL: Record<string, string> = {
  contradicted: "contradicción",
  ambiguous: "coincidencia ambigua",
  probable_match: "coincidencia probable",
};

/** ERP-F6-fix4 — a revisar, agrupado POR PEDIDO (Parte G). Cada pedido lista
 *  sus motivos; nada se toca, Bart decide. */
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
