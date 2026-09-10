"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { ExcludeSeguimientoModal } from "../../components/erp/ExcludeSeguimientoModal";
import { OrderStatusBadge } from "../../components/erp/OrderStatusBadge";
import { getCurrentUser, type User } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import {
  customerLabel,
  ERP_EDIT_ROLES,
  excludeSeguimiento,
  type ExclusionReasonCode,
  includeSeguimiento,
  listOrders,
  type OrderSummary,
} from "../../lib/erpApi";

const STORES = [
  { value: "", label: "Todas las tiendas" },
];

function d(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, day] = iso.slice(0, 10).split("-");
  return `${Number(day)}/${Number(m)}/${y}`;
}

/** ERP · Pedidos — la bandeja principal (los 4 estados de cada pedido).
 *  Control manual (#388 + bandeja): «Quitar» por fila y en bloque saca el
 *  pedido de TODAS las listas de trabajo (bandeja, Cola PEDIDOS, seguimiento y
 *  Drive) con un solo flag reversible; «Ver ocultados» los enseña con su motivo
 *  y «Reincluir» los devuelve. */
export default function ErpOrdersPage() {
  const [user, setUser] = useState<User | null>(null);
  const [rows, setRows] = useState<OrderSummary[]>([]);
  const [prep, setPrep] = useState("");
  const [payment, setPayment] = useState("");
  // B-2-fix4: por defecto la bandeja esconde los procesados externamente.
  const [showExternal, setShowExternal] = useState(false);
  // Control manual — «Ver ocultados»: SOLO los quitados a mano.
  const [showExcluded, setShowExcluded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Selección múltiple de filas (para quitar / reincluir en bloque).
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Pedidos a QUITAR (abre el diálogo de motivo + avisos): una fila o la selección.
  const [excludeTarget, setExcludeTarget] = useState<OrderSummary[] | null>(null);

  const canEdit = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listOrders({
        preparation: prep || undefined,
        payment: payment || undefined,
        show_external: showExternal,
        show_excluded: showExcluded,
      }));
      // Al cambiar de vista/filtros o tras una acción, la selección deja de tener sentido.
      setSelected(new Set());
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar los pedidos."));
    } finally {
      setLoading(false);
    }
  }, [prep, payment, showExternal, showExcluded]);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const stores = useMemo(() => STORES, []);

  function toggleRow(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) =>
      prev.size === rows.length ? new Set() : new Set(rows.map((r) => r.id)),
    );
  }

  // «Quitar»: abre el diálogo (motivo + avisos) para una fila o la selección.
  // Cualquier pedido, cualquier estado; con factura/cobro/albarán se AVISA.
  function openExclude(target: OrderSummary[]) {
    if (target.length === 0) return;
    setError(null);
    setNotice(null);
    setExcludeTarget(target);
  }

  function onExcludeSelected() {
    openExclude(rows.filter((r) => selected.has(r.id)));
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
        `${r.excluded} pedido(s) quitado(s) de la bandeja (y del seguimiento)`
        + (r.already_excluded > 0 ? ` (${r.already_excluded} ya estaban fuera)` : "")
        + ". Se deshace con «Reincluir» en «Ver ocultados».",
      );
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron quitar los pedidos de la bandeja."));
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
      setNotice(`${r.included} pedido(s) reincluido(s) en la bandeja (y en el seguimiento).`);
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

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Pedidos"
        eyebrow="ERP"
        description="Bandeja principal — los 4 estados de cada pedido."
        crumbs={[{ label: "ERP" }, { label: "Pedidos" }]}
        actions={
          <>
            <Link href="/erp/orders/pending-approval" className="button secondary small">
              Cola PEDIDOS
            </Link>
            <Link href="/erp/orders/new" className="button small">
              + Nuevo pedido manual
            </Link>
          </>
        }
      />
      <div className="wf-list-filters" style={{ marginBottom: 12, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <select value={prep} onChange={(e) => setPrep(e.target.value)} aria-label="Filtro preparación">
          <option value="">Preparación: todas</option>
          <option value="pending_review">Pend. revisión</option>
          <option value="in_queue">En cola</option>
          <option value="preparing">Preparando</option>
          <option value="packed">Embalado</option>
          <option value="blocked">Bloqueado</option>
        </select>
        <select value={payment} onChange={(e) => setPayment(e.target.value)} aria-label="Filtro pago">
          <option value="">Pago: todos</option>
          <option value="pending">Pendiente</option>
          <option value="paid">Pagado</option>
          <option value="failed">Fallido</option>
          <option value="refunded">Reembolsado</option>
        </select>
        <label className="checkbox-inline" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={showExternal}
            disabled={showExcluded}
            aria-label="Mostrar procesados externamente"
            onChange={(e) => setShowExternal(e.target.checked)}
          />
          <span className="small">Mostrar procesados externamente</span>
        </label>
        {/* Control manual — ver SOLO los quitados a mano, con motivo y «Reincluir». */}
        <label className="checkbox-inline" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={showExcluded}
            aria-label="Ver pedidos ocultados de la bandeja"
            onChange={(e) => setShowExcluded(e.target.checked)}
          />
          <span className="small">Ver ocultados</span>
        </label>
        {canEdit && selected.size > 0 ? (
          showExcluded ? (
            <button type="button" className="button small" disabled={busy}
              onClick={onIncludeSelected}>
              Reincluir ({selected.size})
            </button>
          ) : (
            <button type="button" className="button small danger" disabled={busy}
              title="Quita los seleccionados de la bandeja y del seguimiento (reversible; no borra nada)"
              onClick={onExcludeSelected}>
              Quitar de la bandeja ({selected.size})
            </button>
          )
        ) : null}
        {stores.length > 1 ? <span /> : null}
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="muted">
          {showExcluded ? "No hay pedidos ocultados." : "No hay pedidos con este filtro."}
        </p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              {canEdit ? (
                <th>
                  <input
                    type="checkbox"
                    aria-label="Seleccionar todo"
                    checked={rows.length > 0 && selected.size === rows.length}
                    onChange={toggleAll}
                  />
                </th>
              ) : null}
              <th>Pedido</th>
              <th>Cliente</th>
              <th>Total</th>
              <th>Pago</th>
              <th>Preparación</th>
              <th>Transporte</th>
              <th>Facturación</th>
              {showExcluded ? <th>Oculto</th> : null}
              {canEdit ? <th aria-label="Acciones" /> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.id} className={o.excluded ? "muted" : undefined}>
                {canEdit ? (
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Seleccionar ${o.order_number}`}
                      checked={selected.has(o.id)}
                      onChange={() => toggleRow(o.id)}
                    />
                  </td>
                ) : null}
                <td>
                  <Link href={`/erp/orders/${o.id}`}><strong>{o.order_number}</strong></Link>
                  {o.externally_processed_at ? (
                    <span className="badge muted" style={{ marginLeft: 6 }}>Externalizado</span>
                  ) : null}
                  {o.excluded ? (
                    <span className="badge muted" style={{ marginLeft: 6 }}
                      title="Quitado a mano de las listas de trabajo (reversible)">
                      Oculto
                    </span>
                  ) : null}
                  <div className="muted small">{o.external_source} · {o.placed_at?.slice(0, 10) ?? "—"}</div>
                </td>
                <td>{customerLabel(o) || <span className="muted">—</span>}</td>
                <td>{o.total_amount.toFixed(2)} {o.currency}</td>
                <td><OrderStatusBadge status={o.payment_status} /></td>
                <td><OrderStatusBadge status={o.preparation_status} /></td>
                <td><OrderStatusBadge status={o.transport_status} /></td>
                <td><OrderStatusBadge status={o.invoice_status} /></td>
                {showExcluded ? (
                  <td className="small">
                    {d(o.seguimiento_excluded_at)}
                    {o.seguimiento_excluded_by_name ? ` · ${o.seguimiento_excluded_by_name}` : ""}
                    <br />
                    <span className="muted">{o.seguimiento_excluded_reason || "sin motivo"}</span>
                  </td>
                ) : null}
                {canEdit ? (
                  <td>
                    {o.excluded ? (
                      <button
                        type="button" className="button small secondary" disabled={busy}
                        title="Vuelve a incluir este pedido en la bandeja y en el seguimiento"
                        aria-label={`Reincluir ${o.order_number} en la bandeja`}
                        onClick={() => void onIncludeRows([o.id])}
                      >
                        Reincluir
                      </button>
                    ) : (
                      <button
                        type="button" className="button small secondary" disabled={busy}
                        title="Quitar de la bandeja y del seguimiento (reversible; no borra nada)"
                        aria-label={`Quitar ${o.order_number} de la bandeja`}
                        onClick={() => openExclude([o])}
                      >
                        Quitar
                      </button>
                    )}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {excludeTarget ? (
        <ExcludeSeguimientoModal
          context="bandeja"
          rows={excludeTarget.map((o) => ({
            id: o.id, order_number: o.order_number, cliente: customerLabel(o) || null,
          }))}
          busy={busy}
          onConfirm={onConfirmExclude}
          onCancel={() => setExcludeTarget(null)}
        />
      ) : null}
    </main>
  );
}
