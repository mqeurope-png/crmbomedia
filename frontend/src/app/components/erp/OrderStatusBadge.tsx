import { STATUS_LABELS } from "../../lib/erpApi";

/** Badge de un estado de dominio: usa las clases .badge.{ok,warn,bad,muted,
 *  active} ya definidas en styles.css (mismos tonos que el resto del CRM). */
export function OrderStatusBadge({ status }: { status: string }) {
  const meta = STATUS_LABELS[status] ?? { label: status, tone: "muted" };
  return <span className={`badge ${meta.tone}`}>{meta.label}</span>;
}
