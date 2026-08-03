"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../../components/PageHeader";
import { OrderStatusMachine } from "../../../components/erp/OrderStatusMachine";
import { extractErrorMessage } from "../../../lib/errors";
import {
  getOrder,
  getOrderTimeline,
  fireTransition,
  type AvailableTransition,
  type OrderDetail,
  type StatusDomain,
  type TimelineEvent,
} from "../../../lib/erpApi";

export default function ErpOrderDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getOrder(id)
      .then(setOrder)
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar el pedido.")));
    getOrderTimeline(id).then((r) => setTimeline(r.items)).catch(() => undefined);
  }, [id]);

  useEffect(() => { load(); }, [load]);

  async function onFire(domain: StatusDomain, t: AvailableTransition) {
    const evidence: Record<string, unknown> = {};
    for (const key of t.required_evidence) {
      const val = window.prompt(`«${t.label}» requiere: ${key}`);
      if (val === null) return; // cancelado
      evidence[key] = val;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await fireTransition(id, {
        domain, to_status: t.to_status, evidence,
        reason: (evidence.reason as string) ?? undefined,
      });
      setOrder(updated);
      const tl = await getOrderTimeline(id);
      setTimeline(tl.items);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo aplicar la transición."));
    } finally {
      setBusy(false);
    }
  }

  if (!order) {
    return <main className="shell"><p className="muted">{error ?? "Cargando…"}</p></main>;
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title={`Pedido ${order.order_number}`}
        eyebrow="ERP"
        description={`${order.external_source} · ${order.total_amount.toFixed(2)} ${order.currency}`}
        crumbs={[
          { label: "ERP" },
          { label: "Pedidos", href: "/erp/orders" },
          { label: order.order_number },
        ]}
      />
      {error ? <p className="form-error">{error}</p> : null}
      {order.externally_processed_at ? (
        <p className="form-info" role="status">
          <span className="badge muted">Externalizado</span>{" "}
          Gestionado fuera del ERP el{" "}
          {new Date(order.externally_processed_at).toLocaleString("es-ES")}
          {order.externally_processed_note ? ` — ${order.externally_processed_note}` : ""}
        </p>
      ) : null}
      {order.blockers.length > 0 ? (
        <ul className="erp-blockers" aria-label="Bloqueos del pedido">
          {order.blockers.map((b) => (
            <li key={b.code} className="erp-blocker">
              <span className="badge bad">{b.code}</span> {b.detail}
            </li>
          ))}
        </ul>
      ) : null}
      {order.warnings.length > 0 ? (
        <ul className="erp-warnings" aria-label="Avisos del pedido">
          {order.warnings.map((w) => (
            <li key={w.code} className="erp-warning">
              <span className="badge warn">{w.code}</span> {w.detail}
            </li>
          ))}
        </ul>
      ) : null}

      <OrderStatusMachine order={order} onFire={onFire} busy={busy} />

      <div className="erp-detail-grid">
        <section className="erp-card">
          <h3>Líneas</h3>
          <table className="data-table">
            <thead>
              <tr><th>SKU</th><th>Artículo</th><th>Cant.</th><th>Total</th><th>Mapping</th></tr>
            </thead>
            <tbody>
              {order.lines.map((l) => (
                <tr key={l.id}>
                  <td><code>{l.product_sku}</code></td>
                  <td>{l.description}</td>
                  <td>{l.quantity}</td>
                  <td>{l.line_total.toFixed(2)}</td>
                  <td>{l.product_codart
                    ? <span className="badge ok">{l.product_codart}</span>
                    : <span className="badge bad">sin mapear</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="erp-card">
          <h3>Timeline</h3>
          {timeline.length === 0 ? (
            <p className="muted small">Sin eventos.</p>
          ) : (
            <ul className="erp-timeline">
              {timeline.map((e, i) => (
                <li key={i} className={`erp-timeline-item is-${e.type}`}>
                  <span className="erp-timeline-when">
                    {new Date(e.at).toLocaleString("es-ES")}
                  </span>
                  <span className="erp-timeline-title">{e.title}</span>
                  {typeof e.detail.reason === "string" && e.detail.reason ? (
                    <span className="muted small"> — {e.detail.reason}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {order.exceptions.length > 0 ? (
        <section className="erp-card">
          <h3>Excepciones</h3>
          <ul>
            {order.exceptions.map((e) => (
              <li key={e.id}>
                <span className="badge warn">{e.type}{e.subtype ? `:${e.subtype}` : ""}</span>{" "}
                <span className="badge muted">{e.status}</span>
              </li>
            ))}
          </ul>
          <Link href="/erp/exceptions" className="link-button small">Ver bandeja de excepciones</Link>
        </section>
      ) : null}
    </main>
  );
}
