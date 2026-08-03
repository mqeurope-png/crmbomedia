"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../../components/PageHeader";
import { MarkExternalModal } from "../../../components/erp/MarkExternalModal";
import { OrderApprovalCard } from "../../../components/erp/OrderApprovalCard";
import { getCurrentUser, type User } from "../../../lib/api";
import { extractErrorMessage } from "../../../lib/errors";
import {
  approveOrder,
  bulkMarkExternallyProcessed,
  ERP_EDIT_ROLES,
  listPendingApproval,
  markExternallyProcessed,
  type PendingOrder,
} from "../../../lib/erpApi";

export default function PendingApprovalPage() {
  const [rows, setRows] = useState<PendingOrder[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Ids objetivo del modal de externalización (1 = una card; N = selección).
  const [markTarget, setMarkTarget] = useState<string[] | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    listPendingApproval()
      .then(setRows)
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la cola.")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
    load();
  }, [load]);

  const canManage = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);

  async function onApprove(orderId: string) {
    setBusy(true);
    setError(null);
    try {
      await approveOrder(orderId);
      removeRows([orderId]); // sale de la cola
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo aprobar el pedido."));
    } finally {
      setBusy(false);
    }
  }

  function removeRows(ids: string[]) {
    const drop = new Set(ids);
    setRows((r) => r.filter((o) => !drop.has(o.id)));
    setSelected((s) => {
      const next = new Set(s);
      for (const id of ids) next.delete(id);
      return next;
    });
  }

  function toggleSelect(id: string) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function confirmMarkExternal(note: string) {
    if (!markTarget) return;
    const ids = markTarget;
    setBusy(true);
    setError(null);
    try {
      if (ids.length === 1) {
        await markExternallyProcessed(ids[0], note || null);
      } else {
        await bulkMarkExternallyProcessed({ order_ids: ids, note: note || null });
      }
      removeRows(ids); // salen de la cola activa
      setMarkTarget(null);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo marcar como externalizado."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Cola PEDIDOS"
        eyebrow="ERP"
        description="Pedidos pendientes de revisión. Aprobar mueve a la Cola SAT."
        crumbs={[
          { label: "ERP" },
          { label: "Pedidos", href: "/erp/orders" },
          { label: "Cola PEDIDOS" },
        ]}
      />
      {error ? <p className="form-error">{error}</p> : null}
      {canManage && selected.size > 0 ? (
        <div className="erp-bulk-bar">
          <span>{selected.size} seleccionado(s)</span>
          <button
            type="button"
            className="button small"
            disabled={busy}
            onClick={() => setMarkTarget([...selected])}
          >
            Marcar como procesados externamente
          </button>
          <button
            type="button"
            className="button small secondary"
            disabled={busy}
            onClick={() => setSelected(new Set())}
          >
            Limpiar selección
          </button>
        </div>
      ) : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="muted">No hay pedidos pendientes de aprobación. 🎉</p>
      ) : (
        <div className="erp-approval-grid">
          {rows.map((o) => (
            <OrderApprovalCard
              key={o.id}
              order={o}
              canApprove={canManage}
              onApprove={onApprove}
              busy={busy}
              selected={selected.has(o.id)}
              onToggleSelect={canManage ? toggleSelect : undefined}
              onMarkExternal={canManage ? (id) => setMarkTarget([id]) : undefined}
            />
          ))}
        </div>
      )}
      {markTarget ? (
        <MarkExternalModal
          count={markTarget.length}
          busy={busy}
          onConfirm={confirmMarkExternal}
          onCancel={() => setMarkTarget(null)}
        />
      ) : null}
    </main>
  );
}
