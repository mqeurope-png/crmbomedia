"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../../components/PageHeader";
import { OrderApprovalCard } from "../../../components/erp/OrderApprovalCard";
import { getCurrentUser, type User } from "../../../lib/api";
import { extractErrorMessage } from "../../../lib/errors";
import {
  approveOrder,
  ERP_EDIT_ROLES,
  listPendingApproval,
  type PendingOrder,
} from "../../../lib/erpApi";

export default function PendingApprovalPage() {
  const [rows, setRows] = useState<PendingOrder[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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

  const canApprove = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);

  async function onApprove(orderId: string) {
    setBusy(true);
    setError(null);
    try {
      await approveOrder(orderId);
      setRows((r) => r.filter((o) => o.id !== orderId)); // sale de la cola
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo aprobar el pedido."));
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
              canApprove={canApprove}
              onApprove={onApprove}
              busy={busy}
            />
          ))}
        </div>
      )}
    </main>
  );
}
