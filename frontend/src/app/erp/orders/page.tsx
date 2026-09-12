"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { CobroFactusolBadge } from "../../components/erp/CobroFactusolBadge";
import { ExcludeSeguimientoModal } from "../../components/erp/ExcludeSeguimientoModal";
import { OrderStatusBadge } from "../../components/erp/OrderStatusBadge";
import { RegistrarCobroModal } from "../../components/erp/RegistrarCobroModal";
import { getCurrentUser, type User } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import {
  completeOrder,
  completeOrdersBulk,
  customerLabel,
  ERP_EDIT_ROLES,
  excludeSeguimiento,
  type ExclusionReasonCode,
  includeSeguimiento,
  listOrders,
  type OrderCobroInfo,
  type OrderSummary,
  refreshOrdersFactusolCobro,
  uncompleteOrder,
} from "../../lib/erpApi";

const STORES = [
  { value: "", label: "Todas las tiendas" },
];

const INVOICED_STATUSES = new Set(["generated", "invoiced_by_erp", "already_invoiced_externally"]);
function isInvoiced(o: { invoice_status: string; factusol_invoice_number: string | null }): boolean {
  return INVOICED_STATUSES.has(o.invoice_status) || !!o.factusol_invoice_number;
}

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
  // «Completado»: "" = todos, "yes" = solo completados, "no" = sin completar.
  const [completedFilter, setCompletedFilter] = useState("");
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
  // Cobro FACTUSOL (estado contable, no el «Pagado» del CRM): filtro
  // cobrada / pendiente / sin comprobar, la fila con el modal «Registrar
  // cobro» abierto y el refresco en bloque «Actualizar cobros FACTUSOL».
  const [cobroFilter, setCobroFilter] = useState("");
  const [cobroTarget, setCobroTarget] = useState<OrderSummary | null>(null);
  const [refreshingCobros, setRefreshingCobros] = useState(false);

  const canEdit = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listOrders({
        preparation: prep || undefined,
        payment: payment || undefined,
        show_external: showExternal,
        show_excluded: showExcluded,
        completed: completedFilter === "" ? undefined : completedFilter === "yes",
        cobro: (cobroFilter || undefined) as "cobrada" | "pendiente" | "sin_comprobar" | undefined,
      }));
      // Al cambiar de vista/filtros o tras una acción, la selección deja de tener sentido.
      setSelected(new Set());
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar los pedidos."));
    } finally {
      setLoading(false);
    }
  }, [prep, payment, showExternal, showExcluded, completedFilter, cobroFilter]);

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

  // «Marcar completado» / «Desmarcar» (solo BoHub, reversible). No exige envío
  // ni factura: si no está facturado, avisa y deja continuar. Nunca toca
  // WooCommerce.
  async function onToggleComplete(o: OrderSummary) {
    if (
      !o.completed && !isInvoiced(o)
      && !window.confirm(`${o.order_number} aún no está facturado. ¿Marcarlo completado igualmente?`)
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (o.completed) {
        await uncompleteOrder(o.id);
        setNotice(`${o.order_number} ya no está marcado como completado.`);
      } else {
        const r = await completeOrder(o.id);
        setNotice(
          `${o.order_number} marcado como completado (solo en BoHub; WooCommerce no cambia).`
          + (r.completion_avisos.length ? ` Aviso: ${r.completion_avisos.join("; ")}.` : ""),
        );
      }
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cambiar el estado de completado."));
    } finally {
      setBusy(false);
    }
  }

  // «Completar seleccionados»: la misma semántica que «Marcar completado»
  // (solo BoHub, reversible, idempotente) aplicada a la selección de una vez.
  // Confirma con el recuento (y cuántos van sin factura: avisa, no bloquea);
  // repinta las filas afectadas sin recargar; los fallos se informan y el
  // resto se completa igualmente.
  async function onCompleteSelected() {
    const target = rows.filter((r) => selected.has(r.id));
    if (target.length === 0) return;
    const pendientes = target.filter((r) => !r.completed);
    const sinFactura = pendientes.filter((r) => !isInvoiced(r)).length;
    const yaCompletados = target.length - pendientes.length;
    const msg = `¿Marcar ${target.length} pedido(s) como completados? Solo en BoHub; WooCommerce no cambia.`
      + (sinFactura ? ` Aviso: ${sinFactura} aún sin facturar.` : "")
      + (yaCompletados ? ` (${yaCompletados} ya estaba(n) completado(s).)` : "");
    if (!window.confirm(msg)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await completeOrdersBulk(target.map((x) => x.id));
      const byId = new Map(r.items.map((it) => [it.id, it]));
      setRows((prev) => prev.map((row) => {
        const fresh = byId.get(row.id);
        return fresh ? { ...row, ...fresh } : row;
      }));
      const numero = (id: string) => target.find((x) => x.id === id)?.order_number ?? id;
      const fallos = r.failed.map((f) => `${numero(f.order_id)}: ${f.error}`);
      setNotice(
        `${r.completed} pedido(s) marcado(s) como completado(s) (solo en BoHub; WooCommerce no cambia)`
        + (r.already_completed ? `, ${r.already_completed} ya lo estaba(n)` : "")
        + (r.sin_facturar ? `. Aviso: ${r.sin_facturar} sin facturar` : "")
        + (fallos.length ? `. No se pudo completar ${fallos.length}: ${fallos.join("; ")}` : "")
        + ".",
      );
      setSelected(new Set());
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron completar los pedidos seleccionados."));
    } finally {
      setBusy(false);
    }
  }

  // «Actualizar cobros FACTUSOL»: comprueba en bloque (solo lectura) y
  // repinta las filas en su sitio, sin recargar la bandeja.
  async function onRefreshCobros() {
    setRefreshingCobros(true);
    setError(null);
    setNotice(null);
    try {
      const r = await refreshOrdersFactusolCobro();
      const byId = new Map(r.items.map((it) => [it.id, it]));
      setRows((prev) => prev.map((row) => {
        const fresh = byId.get(row.id);
        return fresh ? { ...row, ...fresh } : row;
      }));
      setNotice(
        r.checked === 0
          ? "Ningún pedido con factura que comprobar."
          : `Cobro FACTUSOL comprobado en ${r.checked} pedido(s) con factura.`,
      );
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo comprobar el cobro en FACTUSOL."));
    } finally {
      setRefreshingCobros(false);
    }
  }

  // Tras registrar (o comprobar) el cobro desde el modal: solo esa fila.
  function onCobroDone(info: OrderCobroInfo) {
    const status = info.status === "cobrada" || info.status === "pendiente" ? info.status : null;
    setRows((prev) => prev.map((row) => (
      row.id !== info.order_id ? row : {
        ...row,
        factusol_cobro_status: status ?? row.factusol_cobro_status ?? null,
        factusol_cobro_checked_at: info.checked_at ?? row.factusol_cobro_checked_at ?? null,
        factusol_invoice_serie: info.invoice?.serie ?? row.factusol_invoice_serie ?? null,
        factusol_cobro: status && info.invoice?.serie != null && info.invoice.codigo != null ? {
          numero: info.invoice.numero, serie: info.invoice.serie, codigo: info.invoice.codigo,
          total: info.total ?? null, total_cobrado: info.total_cobrado ?? null,
          saldo_pendiente: info.saldo_pendiente ?? null, estfac: info.estfac ?? null,
          cobros: info.cobros ?? null, cobrada: status === "cobrada",
          checked_at: info.checked_at ?? new Date().toISOString(), source: "modal",
        } : row.factusol_cobro ?? null,
      }
    )));
    if (status === "cobrada") {
      setNotice(`${info.order_number}: cobro registrado en FACTUSOL (factura ${info.invoice?.numero ?? ""}).`);
    }
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
        <select value={completedFilter} onChange={(e) => setCompletedFilter(e.target.value)} aria-label="Filtro completado">
          <option value="">Completado: todos</option>
          <option value="yes">Solo completados</option>
          <option value="no">Sin completar</option>
        </select>
        {/* Cobro FACTUSOL (contable): distinto del filtro «Pago» del CRM. */}
        <select value={cobroFilter} onChange={(e) => setCobroFilter(e.target.value)} aria-label="Filtro cobro FACTUSOL">
          <option value="">Cobro FACTUSOL: todos</option>
          <option value="cobrada">Cobrado en FACTUSOL</option>
          <option value="pendiente">Pendiente de cobro</option>
          <option value="sin_comprobar">Con factura, sin comprobar</option>
        </select>
        <button
          type="button" className="button small secondary" disabled={busy || refreshingCobros || loading}
          title="Comprueba en FACTUSOL (solo lectura) el estado de cobro de los pedidos con factura y lo deja guardado en cada fila"
          onClick={() => void onRefreshCobros()}
        >
          {refreshingCobros ? "Comprobando cobros…" : "Actualizar cobros FACTUSOL"}
        </button>
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
            <>
              <button type="button" className="button small" disabled={busy}
                title="Marca los seleccionados como completados (estado final, solo en BoHub; no toca WooCommerce; reversible uno a uno con «Desmarcar»)"
                onClick={() => void onCompleteSelected()}>
                Completar seleccionados ({selected.size})
              </button>
              <button type="button" className="button small danger" disabled={busy}
                title="Quita los seleccionados de la bandeja y del seguimiento (reversible; no borra nada)"
                onClick={onExcludeSelected}>
                Quitar de la bandeja ({selected.size})
              </button>
            </>
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
                  {o.completed ? (
                    <span className="badge ok" style={{ marginLeft: 6 }}
                      title={`Completado${o.completed_at ? ` el ${d(o.completed_at)}` : ""}${o.completed_by_name ? ` por ${o.completed_by_name}` : ""} (solo BoHub)`}>
                      Completado
                    </span>
                  ) : null}
                  <div className="muted small">{o.external_source} · {o.placed_at?.slice(0, 10) ?? "—"}</div>
                </td>
                <td>{customerLabel(o) || <span className="muted">—</span>}</td>
                <td>{o.total_amount.toFixed(2)} {o.currency}</td>
                <td><OrderStatusBadge status={o.payment_status} /></td>
                <td><OrderStatusBadge status={o.preparation_status} /></td>
                <td><OrderStatusBadge status={o.transport_status} /></td>
                <td>
                  <OrderStatusBadge status={o.invoice_status} />
                  {/* Estado de cobro EN FACTUSOL (contable), separado del
                      «Pagado» de la columna PAGO (estado del CRM). */}
                  {o.factusol_invoice_number ? (
                    <div style={{ marginTop: 4 }}>
                      <CobroFactusolBadge
                        hasInvoice
                        status={o.factusol_cobro_status ?? null}
                        cobro={o.factusol_cobro ?? null}
                      />
                    </div>
                  ) : null}
                </td>
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
                    <button
                      type="button" className="button small secondary" disabled={busy}
                      title={o.completed
                        ? "Quitar la marca de completado (solo BoHub)"
                        : "Marcar como completado: estado final, solo en BoHub (no toca WooCommerce)"}
                      aria-label={o.completed
                        ? `Desmarcar completado ${o.order_number}`
                        : `Marcar completado ${o.order_number}`}
                      onClick={() => void onToggleComplete(o)}
                    >
                      {o.completed ? "Desmarcar" : "Completar"}
                    </button>{" "}
                    {/* Cobro manual sin entrar al pedido: mismo modal que la
                        ficha. Solo con factura pendiente de cobro. */}
                    <button
                      type="button" className="button small secondary"
                      disabled={busy || !o.factusol_invoice_number || o.factusol_cobro_status === "cobrada"}
                      title={!o.factusol_invoice_number
                        ? "Emite la factura primero: el cobro se registra sobre la factura del pedido"
                        : o.factusol_cobro_status === "cobrada"
                          ? "La factura ya consta cobrada en FACTUSOL"
                          : "Registrar el cobro de la factura en FACTUSOL (F_LCO + ESTFAC=2)"}
                      aria-label={`Registrar cobro ${o.order_number}`}
                      onClick={() => { setError(null); setNotice(null); setCobroTarget(o); }}
                    >
                      {o.factusol_cobro_status === "cobrada" ? "Cobrado" : "Registrar cobro"}
                    </button>{" "}
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

      {cobroTarget ? (
        <RegistrarCobroModal
          orderId={cobroTarget.id}
          orderNumber={cobroTarget.order_number}
          onClose={() => setCobroTarget(null)}
          onDone={onCobroDone}
        />
      ) : null}
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
