"use client";

import Link from "next/link";
import {
  customerLabel,
  EXCEPTION_STATUS_LABELS,
  EXCEPTION_TYPE_LABELS,
  type ErpExceptionRow,
} from "../../lib/erpApi";

/** Tabla presentacional de la bandeja de excepciones. La fila muestra un
 *  chip rojo de alerta cuando `eta_overdue` (ETA vencida y sigue abierta).
 *  Las acciones se delegan al contenedor. */
export function ExceptionsTable({
  rows,
  onAssignMe,
  onMarkSeen,
  onResolve,
  busy,
}: {
  rows: ErpExceptionRow[];
  onAssignMe: (id: string) => void;
  onMarkSeen: (id: string) => void;
  onResolve: (id: string) => void;
  busy?: boolean;
}) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Tipo</th>
          <th>Pedido</th>
          <th>Estado</th>
          <th>ETA</th>
          <th>Acciones</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((e) => {
          const st = EXCEPTION_STATUS_LABELS[e.status] ?? { label: e.status, tone: "muted" };
          const closed = e.status === "resolved" || e.status === "dismissed";
          return (
            <tr key={e.id} className={e.eta_overdue ? "erp-exc-overdue" : undefined}>
              <td>
                <strong>{EXCEPTION_TYPE_LABELS[e.type] ?? e.type}</strong>
                {e.subtype ? <div className="muted small">{e.subtype}</div> : null}
                {typeof e.metadata.description === "string" ? (
                  <div className="muted small">{e.metadata.description}</div>
                ) : null}
              </td>
              <td>
                <Link href={`/erp/orders/${e.order_id}`}>
                  {e.order_number ?? "Ver pedido"}
                </Link>
                {customerLabel(e) ? (
                  <div className="muted small">{customerLabel(e)}</div>
                ) : null}
              </td>
              <td><span className={`badge ${st.tone}`}>{st.label}</span></td>
              <td>
                {e.eta_date ? (
                  <span>
                    {e.eta_date}
                    {e.eta_overdue ? (
                      <span className="badge bad erp-exc-eta-chip" title="ETA vencida">
                        ⏰ vencida
                      </span>
                    ) : null}
                  </span>
                ) : (
                  <span className="muted small">—</span>
                )}
              </td>
              <td>
                {!closed ? (
                  <div className="erp-exc-actions">
                    <button type="button" className="button small secondary" disabled={busy}
                      onClick={() => onAssignMe(e.id)}>Asignarme</button>
                    {e.status === "open" ? (
                      <button type="button" className="button small secondary" disabled={busy}
                        onClick={() => onMarkSeen(e.id)}>Marcar vista</button>
                    ) : null}
                    <button type="button" className="button small" disabled={busy}
                      onClick={() => onResolve(e.id)}>Resolver</button>
                  </div>
                ) : (
                  <span className="muted small">
                    {e.resolution_note ? `✓ ${e.resolution_note}` : "cerrada"}
                  </span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
