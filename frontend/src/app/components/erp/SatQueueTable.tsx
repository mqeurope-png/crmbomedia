"use client";

import Link from "next/link";
import { customerLabel, STATUS_LABELS, type SatQueueItem } from "../../lib/erpApi";
import { SatAlbaranChip, useSatAlbaranAction } from "./SatPreparingCard";
import { SatReadyButtons, SatReadyDocChips, useSatReadyActions } from "./SatReadyCard";
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

/** Lote 2 · PR-2: las observaciones del comercial van ENCIMA de la fila, en
 *  ámbar y a todo el ancho — solo si hay nota. */
function NotesRow({ order }: { order: SatQueueItem }) {
  if (!(order.notes ?? "").trim()) return null;
  return (
    <tr className="sat-row-notes">
      <td colSpan={COLS}><SatObservaciones notes={order.notes} /></td>
    </tr>
  );
}

function hasTechData(order: SatQueueItem): boolean {
  return Boolean(
    (order.serial_number ?? "").trim()
    || (order.whiterip_license ?? "").trim()
    || (order.shipping_origin ?? "").trim(),
  );
}

/** Celda «Datos técnicos» de la lista (compacta: solo lo que tenga valor, con
 *  su botón de copiar; sin nada → «—»). */
function TechCell({ order }: { order: SatQueueItem }) {
  return (
    <td className="sat-td-tech">
      {hasTechData(order) ? (
        <SatTechData
          compact
          serial={order.serial_number}
          license={order.whiterip_license}
          origin={order.shipping_origin}
        />
      ) : "—"}
    </td>
  );
}

/** Fila de «Por embalar»: mismas acciones que la card (abrir modo trabajo +
 *  albarán sin salir de la lista). */
function SatPreparingRow({ order, onChanged }: { order: SatQueueItem; onChanged: () => void }) {
  const albaran = useSatAlbaranAction(order, onChanged);
  return (
    <>
      <NotesRow order={order} />
      <tr>
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
          <td colSpan={COLS}>
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
function SatReadyRow({ order, onChanged }: { order: SatQueueItem; onChanged: () => void }) {
  const actions = useSatReadyActions(order, onChanged);
  return (
    <>
      <NotesRow order={order} />
      <tr>
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
            <SatReadyButtons actions={actions} compact />
          </div>
        </td>
      </tr>
      {actions.error ? (
        <tr className="sat-row-error">
          <td colSpan={COLS}><p className="form-error small" role="status">{actions.error}</p></td>
        </tr>
      ) : null}
    </>
  );
}

/** Vista lista de la Cola SAT (Lote B6): tabla compacta con las MISMAS
 *  acciones que las tarjetas. `variant` decide qué fila se pinta. Lote 2 ·
 *  PR-2: columna «Datos técnicos» (nº de serie / licencia con «copiar»,
 *  origen) y las observaciones del comercial encima de la fila. */
export function SatQueueTable({
  items,
  variant,
  onChanged,
  ariaLabel,
}: {
  items: SatQueueItem[];
  variant: "preparing" | "ready";
  onChanged: () => void;
  ariaLabel: string;
}) {
  return (
    <div className="sat-table-wrap">
      <table className="sat-table" aria-label={ariaLabel}>
        <thead>
          <tr>
            <th>Nº</th>
            <th>Cliente</th>
            <th>Tienda</th>
            <th>Estado</th>
            <th>Datos técnicos</th>
            <th>Acciones</th>
          </tr>
        </thead>
        <tbody>
          {items.map((o) => variant === "preparing" ? (
            <SatPreparingRow key={o.id} order={o} onChanged={onChanged} />
          ) : (
            <SatReadyRow key={o.id} order={o} onChanged={onChanged} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
