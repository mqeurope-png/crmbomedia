"use client";

import Link from "next/link";
import { customerLabel, STATUS_LABELS, type SatQueueItem } from "../../lib/erpApi";
import { SatAlbaranChip, useSatAlbaranAction } from "./SatPreparingCard";
import {
  SatReadyButtons, SatReadyDocChips, SatTrackingField, satShippedLabel, useSatReadyActions,
} from "./SatReadyCard";
import { SatObservaciones, SatTechData } from "./SatTechData";

/** Fecha corta del pedido (dd/mm/aaaa) para tablas del taller. */
export function satShortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("es-ES");
}

/** Fecha + hora (dd/mm/aaaa hh:mm) para el historial. */
export function satDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("es-ES", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** Nº de columnas de la tabla (para las filas de aviso/observaciones).
 *  Lote 4 · #2 — lista compacta real: nº · cliente · tienda · estado · nº de
 *  serie / licencia · acciones. Se quitan «Fecha» y la columna de flags de
 *  «Documentos» (su estado ya lo dan los chips de acción: albarán y etiqueta),
 *  para que la fila quepa sin scroll horizontal y se distinga de las tarjetas. */
const COLS = 6;

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge ${STATUS_LABELS[status]?.tone ?? "muted"}`}>
      {STATUS_LABELS[status]?.label ?? status}
    </span>
  );
}

/** Casilla de selección de una fila (solo cuando la cola es seleccionable,
 *  para marcar «Sin seguimiento» en lote). */
type SelectProps = {
  selectable?: boolean;
  selected?: boolean;
  onToggle?: (id: string) => void;
};

function SelectCell({ order, selectable, selected, onToggle }: { order: SatQueueItem } & SelectProps) {
  if (!selectable) return null;
  return (
    <td className="sat-td-select">
      <input
        type="checkbox"
        aria-label={`Seleccionar ${order.order_number}`}
        checked={!!selected}
        onChange={() => onToggle?.(order.id)}
      />
    </td>
  );
}

/** Lote 2 · PR-2: las observaciones del comercial van ENCIMA de la fila, en
 *  ámbar y a todo el ancho — solo si hay nota. */
function NotesRow({ order, cols }: { order: SatQueueItem; cols: number }) {
  if (!(order.notes ?? "").trim()) return null;
  return (
    <tr className="sat-row-notes">
      <td colSpan={cols}><SatObservaciones notes={order.notes} /></td>
    </tr>
  );
}

function hasTechData(order: SatQueueItem): boolean {
  return Boolean(
    (order.serial_number ?? "").trim()
    || (order.whiterip_license ?? "").trim(),
  );
}

/** Celda «Datos técnicos» de la lista (compacta: solo lo que tenga valor, con
 *  su botón de copiar; sin nada → «—»). Lote 5 · #2: sin origen. */
function TechCell({ order }: { order: SatQueueItem }) {
  return (
    <td className="sat-td-tech">
      {hasTechData(order) ? (
        <SatTechData
          compact
          serial={order.serial_number}
          license={order.whiterip_license}
        />
      ) : "—"}
    </td>
  );
}

/** Fila de «Por embalar»: mismas acciones que la card (abrir modo trabajo +
 *  albarán sin salir de la lista). */
function SatPreparingRow(
  { order, onChanged, cols, ...sel }:
  { order: SatQueueItem; onChanged: () => void; cols: number } & SelectProps,
) {
  const albaran = useSatAlbaranAction(order, onChanged);
  return (
    <>
      <NotesRow order={order} cols={cols} />
      <tr>
        <SelectCell order={order} {...sel} />
        <td className="sat-td-num">
          <Link href={`/erp/sat/${order.id}`}>{order.order_number}</Link>
          {order.payment_status !== "paid" ? (
            <span className="sat-row-warn" title="Sin cobrar">⚠ SIN COBRAR</span>
          ) : null}
        </td>
        <td className="sat-td-cliente">{customerLabel(order) || "—"}</td>
        <td>{order.store_slug ?? "—"}</td>
        <td><StatusBadge status={order.preparation_status} /></td>
        <TechCell order={order} />
        <td>
          <div className="sat-td-actions">
            <Link href={`/erp/sat/${order.id}`} className="button small">Abrir →</Link>
            <SatAlbaranChip order={order} albaran={albaran} />
            <Link href={`/erp/orders/${order.id}`} className="button secondary small">Ficha</Link>
          </div>
        </td>
      </tr>
      {albaran.error ? (
        <tr className="sat-row-error">
          <td colSpan={cols}>
            <p className="form-error small" role="status">
              {albaran.error}{" "}
              <Link href={`/erp/orders/${order.id}`}>Ir a la ficha</Link>
            </p>
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** Fila de «Listos para envío»: imprimir albarán/etiqueta, marcar recogido
 *  (con confirmación) y reabrir — los mismos handlers que la card. */
function SatReadyRow(
  { order, onChanged, cols, ...sel }:
  { order: SatQueueItem; onChanged: () => void; cols: number } & SelectProps,
) {
  const actions = useSatReadyActions(order, onChanged);
  return (
    <>
      <NotesRow order={order} cols={cols} />
      <tr>
        <SelectCell order={order} {...sel} />
        <td className="sat-td-num">
          <Link href={`/erp/orders/${order.id}`}>{order.order_number}</Link>
          <span className="muted small sat-row-total">
            {order.total_amount.toFixed(2)} {order.currency}
          </span>
        </td>
        <td className="sat-td-cliente">{customerLabel(order) || "—"}</td>
        <td>{order.store_slug ?? "—"}</td>
        <td><StatusBadge status={order.preparation_status} /></td>
        <TechCell order={order} />
        <td>
          <div className="sat-td-actions">
            <SatReadyDocChips order={order} actions={actions} />
            {/* Lote 5 · #3 — nº de seguimiento en la fila de «Listos». */}
            <SatTrackingField order={order} onChanged={onChanged} compact />
            <SatReadyButtons actions={actions} compact />
          </div>
        </td>
      </tr>
      {actions.error ? (
        <tr className="sat-row-error">
          <td colSpan={cols}><p className="form-error small" role="status">{actions.error}</p></td>
        </tr>
      ) : null}
    </>
  );
}

/** Fila de un pedido YA ENVIADO (tras «Marcar recogido» en su sitio). */
function SatShippedRow({ order, ...sel }: { order: SatQueueItem } & SelectProps) {
  return (
    <tr>
      <SelectCell order={order} {...sel} />
      <td className="sat-td-num">
        <Link href={`/erp/orders/${order.id}`}>{order.order_number}</Link>
      </td>
      <td className="sat-td-cliente">{customerLabel(order) || "—"}</td>
      <td>{order.store_slug ?? "—"}</td>
      <td><span className={`badge ${order.sin_seguimiento ? "muted" : "ok"}`}>
        {satShippedLabel(order)}
      </span></td>
      {/* En la columna de datos técnicos, el nº de seguimiento. */}
      <td className="mono">{order.tracking_number || order.genei?.tracking || "—"}</td>
      <td>
        <Link href={`/erp/orders/${order.id}`} className="button secondary small">Ficha</Link>
      </td>
    </tr>
  );
}

function rowKind(o: SatQueueItem, variant: "preparing" | "ready" | "auto") {
  if (variant !== "auto") return variant;
  if (o.sat_tab === "embalados" || o.sat_tab === "pendiente_recogida") return "ready";
  if (o.sat_tab === "enviados" || o.sat_tab === "sin_seguimiento") return "shipped";
  return "preparing";
}

/** Vista lista de la Cola SAT (Lote B6): tabla compacta con las MISMAS
 *  acciones que las tarjetas. `variant` decide qué fila se pinta (`auto` =
 *  según el paso ACTUAL de cada pedido). Lote 2 · PR-2: columna «Datos
 *  técnicos» (nº de serie / licencia con «copiar», origen) y las observaciones
 *  del comercial encima de la fila. Con `onItemChanged`, una acción refresca
 *  SOLO su pedido (se queda en su sitio) en vez de recargar la cola. */
export function SatQueueTable({
  items,
  variant,
  onChanged,
  onItemChanged,
  ariaLabel,
  selectable = false,
  selected,
  onToggle,
}: {
  items: SatQueueItem[];
  variant: "preparing" | "ready" | "auto";
  onChanged: () => void;
  onItemChanged?: (id: string) => void;
  ariaLabel: string;
  /** Selección múltiple (para «Sin seguimiento» en lote). */
  selectable?: boolean;
  selected?: Set<string>;
  onToggle?: (id: string) => void;
}) {
  const cols = selectable ? COLS + 1 : COLS;
  const sel = { selectable, onToggle };
  const changed = (id: string) => () => (onItemChanged ? onItemChanged(id) : onChanged());
  return (
    <div className="sat-table-wrap">
      <table className="sat-table" aria-label={ariaLabel}>
        <thead>
          <tr>
            {selectable ? <th className="sat-td-select" aria-label="Selección" /> : null}
            <th>Nº</th>
            <th>Cliente</th>
            <th>Tienda</th>
            <th>Estado</th>
            <th>Datos técnicos</th>
            <th>Acciones</th>
          </tr>
        </thead>
        <tbody>
          {items.map((o) => {
            const kind = rowKind(o, variant);
            if (kind === "shipped") {
              return <SatShippedRow key={o.id} order={o}
                                    selected={selected?.has(o.id)} {...sel} />;
            }
            return kind === "preparing" ? (
              <SatPreparingRow key={o.id} order={o} onChanged={changed(o.id)} cols={cols}
                               selected={selected?.has(o.id)} {...sel} />
            ) : (
              <SatReadyRow key={o.id} order={o} onChanged={changed(o.id)} cols={cols}
                           selected={selected?.has(o.id)} {...sel} />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
