"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "../../../components/PageHeader";
import { MarkExternalModal } from "../../../components/erp/MarkExternalModal";
import { OrderApprovalCard } from "../../../components/erp/OrderApprovalCard";
import { getCurrentUser, type User } from "../../../lib/api";
import { extractErrorMessage } from "../../../lib/errors";
import {
  approveOrder,
  bulkMarkExternallyProcessed,
  customerLabel,
  ERP_EDIT_ROLES,
  getErpSettings,
  listPendingApproval,
  markExternallyProcessed,
  type PendingOrder,
} from "../../../lib/erpApi";

type SortDir = "asc" | "desc";

/** Cola PEDIDOS: pendientes de revisión, con sus bloqueos. Lote B8: filtros
 *  ligeros (buscar por nº / cliente en local, tienda y orden por fecha en el
 *  backend); las tarjetas y sus acciones siguen igual. */
export default function PendingApprovalPage() {
  const [rows, setRows] = useState<PendingOrder[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Ids objetivo del modal de externalización (1 = una card; N = selección).
  const [markTarget, setMarkTarget] = useState<string[] | null>(null);
  // Lote B8: filtros ligeros. La búsqueda es local (la cola es corta); tienda
  // y orden los aplica el backend. Ascendente por defecto: lo más antiguo
  // primero, como siempre.
  const [text, setText] = useState("");
  const [store, setStore] = useState("");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [stores, setStores] = useState<{ slug: string; label: string }[]>([]);

  const load = useCallback(() => {
    setLoading(true);
    listPendingApproval({
      store_slug: store || undefined,
      sort: sortDir === "desc" ? "placed_desc" : "placed_asc",
    })
      .then(setRows)
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la cola.")))
      .finally(() => setLoading(false));
  }, [store, sortDir]);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
    getErpSettings()
      .then((s) => setStores((s.woocommerce_stores ?? []).map((t) => ({ slug: t.slug, label: t.label }))))
      .catch(() => undefined);
  }, []);

  useEffect(() => { load(); }, [load]);

  const canManage = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);

  // Búsqueda local por nº de pedido o cliente (sin acentos ni mayúsculas).
  const visible = useMemo(() => {
    const q = normaliza(text);
    if (!q) return rows;
    return rows.filter((o) =>
      normaliza(o.order_number).includes(q) || normaliza(customerLabel(o)).includes(q),
    );
  }, [rows, text]);

  const hasFilters = !!text || !!store || sortDir !== "asc";

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

      <div className="erp-flow-filters erp-approval-filters" role="search" aria-label="Filtros de la cola">
        <label className="field erp-flow-filter-grow">
          <span className="sr-only">Buscar pedido</span>
          <input
            type="search" value={text} placeholder="Nº de pedido o cliente…"
            aria-label="Buscar pedido"
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Tienda</span>
          <select value={store} aria-label="Filtro tienda" onChange={(e) => setStore(e.target.value)}>
            <option value="">Todas</option>
            {stores.map((s) => <option key={s.slug} value={s.slug}>{s.label}</option>)}
          </select>
        </label>
        <button
          type="button" className="button small secondary"
          aria-label={sortDir === "asc" ? "Orden ascendente" : "Orden descendente"}
          title={sortDir === "asc"
            ? "Por fecha del pedido, los más antiguos primero (pulsa para invertir)"
            : "Por fecha del pedido, los más recientes primero (pulsa para invertir)"}
          onClick={() => setSortDir((v) => (v === "asc" ? "desc" : "asc"))}
        >
          {sortDir === "asc" ? "Fecha ↑" : "Fecha ↓"}
        </button>
        {hasFilters ? (
          <button type="button" className="button small secondary"
                  onClick={() => { setText(""); setStore(""); setSortDir("asc"); }}>
            Limpiar filtros
          </button>
        ) : null}
        <span className="muted small">
          {loading ? "Cargando…" : `${visible.length} de ${rows.length} pedido(s)`}
        </span>
      </div>

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
        <p className="muted">
          {store ? "No hay pedidos pendientes de aprobación de esta tienda." : "No hay pedidos pendientes de aprobación. 🎉"}
        </p>
      ) : visible.length === 0 ? (
        <p className="muted">Ningún pedido de la cola casa con «{text}».</p>
      ) : (
        <div className="erp-approval-grid">
          {visible.map((o) => (
            <OrderApprovalCard
              key={o.id}
              order={o}
              canApprove={canManage}
              onApprove={onApprove}
              busy={busy}
              selected={selected.has(o.id)}
              onToggleSelect={canManage ? toggleSelect : undefined}
              onMarkExternal={canManage ? (id) => setMarkTarget([id]) : undefined}
              canEmitFactusol={canManage}
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

function normaliza(s: string | null | undefined): string {
  return (s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
}
