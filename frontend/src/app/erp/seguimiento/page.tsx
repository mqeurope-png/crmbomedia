"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { getCurrentUser, type User } from "../../lib/api";
import {
  ERP_EDIT_ROLES,
  exportSeguimientoXlsx,
  getErpSettings,
  listSeguimiento,
  saveBlob,
  syncSeguimientoDrive,
  type DriveSyncReviewGroup,
  type DriveSyncSummary,
  type SeguimientoFilters,
  type SeguimientoPage,
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
  { label: "Estado", sort: "estado" },
];

function d(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, day] = iso.split("-");
  return `${Number(day)}/${Number(m)}/${y}`;
}

/** ERP-F6 — Seguimiento de pedidos: la vista que sustituye el Excel manual de
 *  Bart. Por defecto enseña los pedidos EN CURSO (la parte de arriba del
 *  Excel); el histórico de 7.743 filas se queda en su fichero. */
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

  const canEdit = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);

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

  function toggleSort(key: string | null) {
    if (!key) return;
    setFilters((f) => ({
      ...f,
      sort: key,
      dir: f.sort === key && f.dir !== "asc" ? "asc" : f.sort === key ? "desc" : "asc",
    }));
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
        `Hoja actualizada: ${summary.appended_rows} filas añadidas, `
        + `${summary.updated_rows} actualizadas (${summary.updated_cells} celdas), `
        + `${summary.conflicts.length} conflictos sin tocar.`,
      );
    } catch (e) {
      setError(extractErrorMessage(
        e, "No se pudo actualizar la hoja de Drive.",
      ));
    } finally {
      setBusy(false);
    }
  }

  const drive = page?.drive;

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
              onChange={(e) => setFilters({
                ...filters, en_curso: e.target.checked ? undefined : false,
              })}
            />
            <span>Solo en curso</span>
          </label>
          <button type="button" className="button small secondary" disabled={busy}
            onClick={onExport}>
            Descargar Excel
          </button>
          {canEdit ? (
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
            sincronización nunca borra filas ni pisa celdas editadas a mano.
          </p>
        ) : null}
      </section>

      {previewSummary ? (
        <section className="erp-card">
          <h3>Previsualización — revisa antes de escribir</h3>
          <p className="muted small">
            Sobre una hoja de {previewSummary.sheet_rows} filas. Nada se ha
            escrito todavía.
          </p>
          <ul className="item-list">
            <li><strong>{previewSummary.appended_rows}</strong> filas a añadir (pedidos que no estaban).</li>
            <li><strong>{previewSummary.updated_rows}</strong> filas a actualizar (ya estaban; se rellenan celdas vacías).</li>
            <li><strong>{previewSummary.orders_to_review}</strong> pedidos a revisar (contradicciones o coincidencias probables).</li>
            {previewSummary.omitted_columns.length > 0 ? (
              <li>Columnas omitidas (no están en la hoja): {previewSummary.omitted_columns.join(", ")}.</li>
            ) : null}
          </ul>
          <ReviewGroups groups={previewSummary.review_groups} />
          <div className="modal-actions">
            <button type="button" className="button secondary" disabled={busy}
              onClick={() => setPreviewSummary(null)}>
              Cancelar
            </button>
            <button type="button" className="button" disabled={busy}
              onClick={onConfirmSync}>
              {busy ? "Escribiendo…" : "Confirmar y escribir en la hoja"}
            </button>
          </div>
        </section>
      ) : null}

      {syncSummary && syncSummary.review_groups.length > 0 ? (
        <section className="erp-card">
          <h3>A revisar ({syncSummary.orders_to_review} pedidos)</h3>
          <p className="muted small">
            Contradicciones o coincidencias probables; no se ha modificado nada
            de ellas.
          </p>
          <ReviewGroups groups={syncSummary.review_groups} />
        </section>
      ) : null}

      <section className="erp-card">
        <p className="muted small" role="status">
          {page ? `${page.total} pedidos` : "Cargando…"}
          {filters.en_curso === false ? " (todos)" : " en curso"}
        </p>
        <div style={{ overflowX: "auto" }}>
          <table className="data-table erp-seguimiento-table">
            <thead>
              <tr>
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
              {(page?.items ?? []).map((r) => (
                <tr key={r.id}>
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
                  <td>{r.whiterip ?? "—"}</td>
                  <td>
                    <span className={`badge ${ESTADO_TONE[r.estado] ?? "muted"}`}>
                      {r.estado}
                    </span>
                  </td>
                </tr>
              ))}
              {page && page.items.length === 0 ? (
                <tr><td colSpan={HEADERS.length} className="muted">Sin pedidos.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}

const REVIEW_KIND_LABEL: Record<string, string> = {
  manual_cell: "celda manual distinta",
  contradicted: "contradicción",
  ambiguous: "coincidencia ambigua",
  probable_match: "coincidencia probable",
};

/** ERP-F6-fix4 — a revisar, agrupado POR PEDIDO (Parte G). Cada pedido lista
 *  sus celdas/motivos; nada se toca, Bart decide. */
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
                {c.kind === "manual_cell" ? (
                  <>{c.column}: la hoja dice «{c.sheet_value}», BoHub tiene «{c.bohub_value}».</>
                ) : (
                  <>{c.detail}</>
                )}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}
