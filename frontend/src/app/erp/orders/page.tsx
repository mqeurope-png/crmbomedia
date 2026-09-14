"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { PageHeader } from "../../components/PageHeader";
import { CobroFactusolBadge } from "../../components/erp/CobroFactusolBadge";
import { ExcludeSeguimientoModal } from "../../components/erp/ExcludeSeguimientoModal";
import { OrderStatusBadge } from "../../components/erp/OrderStatusBadge";
import { RegistrarCobroModal } from "../../components/erp/RegistrarCobroModal";
import {
  QUEUE_HINT,
  QUEUE_LABEL,
  WorkflowQueueCards,
} from "../../components/erp/flow/WorkflowQueueCards";
import { WorkflowAlerts } from "../../components/erp/flow/WorkflowAlerts";
import { regimeLabel } from "../../components/erp/flow/RegimePill";
import { ActionsMenu } from "../../components/erp/flow/ActionsMenu";
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
  type WorkflowQueue,
} from "../../lib/erpApi";

const INVOICED_STATUSES = new Set(["generated", "invoiced_by_erp", "already_invoiced_externally"]);
function isInvoiced(o: { invoice_status: string; factusol_invoice_number: string | null }): boolean {
  return INVOICED_STATUSES.has(o.invoice_status) || !!o.factusol_invoice_number;
}

function d(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, day] = iso.slice(0, 10).split("-");
  return `${Number(day)}/${Number(m)}/${y}`;
}

/** Origen del pedido, como pastilla (web de tal tienda / manual / FACTUSOL). */
function sourcePill(o: OrderSummary) {
  const web = o.external_source === "woocommerce";
  const manual = o.external_source === "manual";
  return (
    <span className={`erp-flow-src${web ? " is-woo" : manual ? " is-man" : ""}`}>
      {web ? "woo" : o.external_source}
    </span>
  );
}

/** ERP · Pedidos — la BANDEJA DE TRABAJO (rediseño de flujo, Fase 1).
 *
 *  Ya no es una tabla de cuatro estados que hay que interpretar: arriba están
 *  las colas con su contador (por revisar / por facturar / por cobrar / por
 *  enviar / incidencias / listo) y cada pedido enseña su SIGUIENTE ACCIÓN como
 *  botón principal, su alerta si la tiene y el importe con el régimen de IVA
 *  del cliente. Quién decide todo eso es el backend (`workflow`), el mismo
 *  bloque que consume la ficha.
 *
 *  No se ha perdido ninguna acción: los filtros de siempre quedan como
 *  refinamiento y las acciones por fila (Completar/Desmarcar, Registrar cobro,
 *  Quitar/Reincluir) viven en el menú «⋯» de cada tarjeta; las de bloque
 *  (Completar seleccionados, Quitar de la bandeja, Reincluir) siguen sobre la
 *  lista con la selección múltiple. */
export default function ErpOrdersPage() {
  const [user, setUser] = useState<User | null>(null);
  const [rows, setRows] = useState<OrderSummary[]>([]);
  const [counts, setCounts] = useState<Partial<Record<WorkflowQueue, number>>>({});
  // Cola de trabajo elegida (organización primaria); null = todas.
  const [queue, setQueue] = useState<WorkflowQueue | null>(null);
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
      const r = await listOrders({
        preparation: prep || undefined,
        payment: payment || undefined,
        show_external: showExternal,
        show_excluded: showExcluded,
        completed: completedFilter === "" ? undefined : completedFilter === "yes",
        cobro: (cobroFilter || undefined) as "cobrada" | "pendiente" | "sin_comprobar" | undefined,
        queue: queue ?? undefined,
      });
      setRows(r.items);
      // Los contadores llegan de TODO lo filtrado (antes de elegir cola): la
      // cabecera no se vacía al meterse en una cola.
      setCounts(r.queue_counts);
      // Al cambiar de vista/filtros o tras una acción, la selección deja de tener sentido.
      setSelected(new Set());
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar los pedidos."));
    } finally {
      setLoading(false);
    }
  }, [prep, payment, showExternal, showExcluded, completedFilter, cobroFilter, queue]);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
  }, []);

  useEffect(() => { void load(); }, [load]);

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

  /** El botón principal de la tarjeta: la acción que el backend dice que toca.
   *  Las que la bandeja sabe hacer sin salir (cobro, completar) se disparan
   *  aquí mismo; el resto lleva a la ficha, que es donde viven. */
  function primaryAction(o: OrderSummary): ReactNode {
    const wf = o.workflow;
    if (!wf || wf.next_action === "ninguna") {
      return (
        <Link href={`/erp/orders/${o.id}`} className="button small secondary">
          Abrir
        </Link>
      );
    }
    const label = wf.next_action_label;
    if (canEdit && wf.next_action === "registrar_cobro" && o.factusol_invoice_number) {
      return (
        <button
          type="button" className="button small" disabled={busy}
          aria-label={`${label} ${o.order_number}`} title={wf.next_action_hint}
          onClick={() => { setError(null); setNotice(null); setCobroTarget(o); }}
        >
          {label}
        </button>
      );
    }
    if (canEdit && wf.next_action === "marcar_completado") {
      return (
        <button
          type="button" className="button small" disabled={busy}
          aria-label={`${label} ${o.order_number}`} title={wf.next_action_hint}
          onClick={() => void onToggleComplete(o)}
        >
          {label}
        </button>
      );
    }
    return (
      <Link
        href={`/erp/orders/${o.id}`} className="button small"
        aria-label={`${label} ${o.order_number}`} title={wf.next_action_hint}
      >
        {label}
      </Link>
    );
  }

  const enCola = queue ? QUEUE_LABEL[queue] : "Todos los pedidos";
  const subtitulo = queue
    ? `· ${rows.length} pedido(s) ${QUEUE_HINT[queue]}`
    : `· ${rows.length} pedido(s)`;

  return (
    <main className="shell shell-wide erp-flow">
      <PageHeader
        title="Pedidos"
        eyebrow="ERP"
        description="Bandeja de trabajo — ordenada por lo que hay que hacer."
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

      <WorkflowQueueCards counts={counts} active={queue} onSelect={setQueue} />

      {/* Los filtros de siempre: ahora REFINAN la cola elegida. */}
      <div className="wf-list-filters erp-flow-filters">
        <span className="erp-flow-filters-label">Refinar</span>
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
      </div>

      <div className="erp-flow-qtitle">
        <span>{enCola}</span>
        <span className="cnt">{subtitulo}</span>
        {queue ? (
          <button type="button" className="button small secondary" onClick={() => setQueue(null)}>
            Ver todas las colas
          </button>
        ) : null}
        {canEdit && rows.length > 0 ? (
          <label className="checkbox-inline" style={{ display: "flex", alignItems: "center", gap: 6, marginLeft: "auto" }}>
            <input
              type="checkbox"
              aria-label="Seleccionar todo"
              checked={rows.length > 0 && selected.size === rows.length}
              onChange={toggleAll}
            />
            <span className="small muted">Seleccionar todo</span>
          </label>
        ) : null}
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
      </div>

      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="muted">
          {showExcluded
            ? "No hay pedidos ocultados."
            : queue
              ? "Esta cola está vacía con los filtros actuales."
              : "No hay pedidos con este filtro."}
        </p>
      ) : (
        <div className="erp-flow-list">
          {rows.map((o) => {
            const wf = o.workflow;
            const cobrada = o.factusol_cobro_status === "cobrada";
            return (
              <article
                key={o.id}
                data-order-row={o.order_number}
                className={`erp-flow-item${wf?.blocked ? " is-alert" : ""}${o.excluded ? " is-muted" : ""}`}
              >
                {canEdit ? (
                  <input
                    type="checkbox"
                    aria-label={`Seleccionar ${o.order_number}`}
                    checked={selected.has(o.id)}
                    onChange={() => toggleRow(o.id)}
                  />
                ) : <span />}
                <div className="erp-flow-item-main">
                  <div className="erp-flow-item-r1">
                    <Link href={`/erp/orders/${o.id}`}><strong>{o.order_number}</strong></Link>
                    {sourcePill(o)}
                    <OrderStatusBadge status={o.payment_status} />
                    {o.factusol_invoice_number ? (
                      <CobroFactusolBadge
                        hasInvoice
                        status={o.factusol_cobro_status ?? null}
                        cobro={o.factusol_cobro ?? null}
                      />
                    ) : null}
                    {o.externally_processed_at ? (
                      <span className="badge muted">Externalizado</span>
                    ) : null}
                    {o.excluded ? (
                      <span className="badge muted"
                        title="Quitado a mano de las listas de trabajo (reversible)">
                        Oculto
                      </span>
                    ) : null}
                    {o.completed ? (
                      <span className="badge ok"
                        title={`Completado${o.completed_at ? ` el ${d(o.completed_at)}` : ""}${o.completed_by_name ? ` por ${o.completed_by_name}` : ""} (solo BoHub)`}>
                        Completado
                      </span>
                    ) : null}
                  </div>
                  <p className="erp-flow-item-r2">
                    <span>{customerLabel(o) || "—"}</span>
                    <span>{d(o.placed_at)}</span>
                    {wf ? <span>{wf.queue_label}</span> : null}
                  </p>
                  {o.excluded ? (
                    <p className="erp-flow-item-r2 small">
                      <span>
                        {d(o.seguimiento_excluded_at)}
                        {o.seguimiento_excluded_by_name ? ` · ${o.seguimiento_excluded_by_name}` : ""}
                      </span>
                      <span className="muted">{o.seguimiento_excluded_reason || "sin motivo"}</span>
                    </p>
                  ) : null}
                  {wf ? <WorkflowAlerts alerts={wf.alerts} variant="inline" max={2} /> : null}
                </div>
                <div className="erp-flow-item-side">
                  <p className="erp-flow-amount">
                    {o.total_amount.toFixed(2)} {o.currency}
                    {regimeLabel(wf?.regime) ? <small>{regimeLabel(wf?.regime)}</small> : null}
                  </p>
                  <div className="erp-flow-item-actions">
                    {primaryAction(o)}
                    {canEdit ? (
                      <ActionsMenu label={`Más acciones ${o.order_number}`}>
                        <Link href={`/erp/orders/${o.id}`}>Abrir ficha</Link>
                        {wf?.next_action === "marcar_completado" ? null : (
                          <button
                            type="button" disabled={busy}
                            title={o.completed
                              ? "Quitar la marca de completado (solo BoHub)"
                              : "Marcar como completado: estado final, solo en BoHub (no toca WooCommerce)"}
                            aria-label={o.completed
                              ? `Desmarcar completado ${o.order_number}`
                              : `Marcar completado ${o.order_number}`}
                            onClick={() => void onToggleComplete(o)}
                          >
                            {o.completed ? "Desmarcar completado" : "Marcar completado"}
                          </button>
                        )}
                        {/* Cobro manual sin entrar al pedido: mismo modal que la
                            ficha. Solo con factura pendiente de cobro. */}
                        {wf?.next_action === "registrar_cobro" && o.factusol_invoice_number ? null : (
                          <button
                            type="button"
                            disabled={busy || !o.factusol_invoice_number || cobrada}
                            title={!o.factusol_invoice_number
                              ? "Emite la factura primero: el cobro se registra sobre la factura del pedido"
                              : cobrada
                                ? "La factura ya consta cobrada en FACTUSOL"
                                : "Registrar el cobro de la factura en FACTUSOL (F_LCO + ESTFAC=2)"}
                            aria-label={`Registrar cobro ${o.order_number}`}
                            onClick={() => { setError(null); setNotice(null); setCobroTarget(o); }}
                          >
                            {cobrada ? "Cobrado" : "Registrar cobro"}
                          </button>
                        )}
                        {o.excluded ? (
                          <button
                            type="button" disabled={busy}
                            title="Vuelve a incluir este pedido en la bandeja y en el seguimiento"
                            aria-label={`Reincluir ${o.order_number} en la bandeja`}
                            onClick={() => void onIncludeRows([o.id])}
                          >
                            Reincluir en la bandeja
                          </button>
                        ) : (
                          <button
                            type="button" disabled={busy}
                            title="Quitar de la bandeja y del seguimiento (reversible; no borra nada)"
                            aria-label={`Quitar ${o.order_number} de la bandeja`}
                            onClick={() => openExclude([o])}
                          >
                            Quitar de la bandeja
                          </button>
                        )}
                      </ActionsMenu>
                    ) : null}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
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
