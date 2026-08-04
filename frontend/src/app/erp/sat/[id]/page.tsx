"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { EmbalarModal } from "../../../components/erp/EmbalarModal";
import { ReportExceptionModal } from "../../../components/erp/ReportExceptionModal";
import { extractErrorMessage } from "../../../lib/errors";
import {
  attachDocument,
  fireTransition,
  getOrder,
  reportException,
  STATUS_LABELS,
  type OrderDetail,
} from "../../../lib/erpApi";

/** Modo trabajo SAT de un pedido: líneas verificables, subir foto y avanzar el
 *  estado (Empezar / Embalado) o reportar un problema. Táctil, botones grandes.
 *  Fase D: «Embalado» abre el modal multi-bulto (el backend exige ≥1 bulto). */
export default function SatOrderWorkPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showReport, setShowReport] = useState(false);
  const [embalarOpen, setEmbalarOpen] = useState(false);

  const load = useCallback(() => {
    getOrder(id)
      .then(setOrder)
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar el pedido.")));
  }, [id]);

  useEffect(() => { load(); }, [load]);

  async function advance(toStatus: string) {
    setBusy(true);
    setError(null);
    try {
      await fireTransition(id, { domain: "preparation", to_status: toStatus });
      router.push("/erp/sat");
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo avanzar el estado."));
      setBusy(false);
    }
  }

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      await attachDocument(id, file);
      load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo subir la foto."));
    } finally {
      setBusy(false);
      e.target.value = "";
    }
  }

  async function onReport(data: { type: string; subtype?: string; description: string }) {
    setBusy(true);
    try {
      await reportException(id, data);
      setShowReport(false);
      router.push("/erp/sat");
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo reportar."));
      setBusy(false);
    }
  }

  if (!order) {
    return <div className="sat-work"><p className="muted">{error ?? "Cargando…"}</p></div>;
  }

  const prep = order.preparation_status;
  const docs = ((order.packing?.documents as unknown[]) ?? []);

  return (
    <div className="sat-work">
      <h1>{order.order_number}</h1>
      <span className={`badge ${STATUS_LABELS[prep]?.tone ?? "muted"}`}>
        {STATUS_LABELS[prep]?.label ?? prep}
      </span>
      {error ? <p className="form-error">{error}</p> : null}

      <section className="sat-work-lines">
        <h2>Líneas</h2>
        {order.lines.map((l) => (
          <label key={l.id} className="sat-line-check">
            <input type="checkbox" /> <strong>{l.quantity}×</strong> {l.description}
            <code className="muted"> {l.product_sku}</code>
          </label>
        ))}
      </section>

      <section className="sat-work-packing">
        <h2>Fotos / documentos</h2>
        <label className="sat-upload">
          📷 Subir foto / documento
          <input type="file" accept="image/*,application/pdf" onChange={onUpload}
            aria-label="Subir foto o documento" disabled={busy} hidden />
        </label>
        {docs.length > 0 ? (
          <p className="muted small">{docs.length} documento(s) adjunto(s).</p>
        ) : null}
      </section>

      <div className="sat-work-actions">
        {prep === "in_queue" ? (
          <button type="button" className="sat-btn start" disabled={busy}
            onClick={() => advance("preparing")}>▶ EMPEZAR</button>
        ) : null}
        {prep === "preparing" ? (
          <button type="button" className="sat-btn pack" disabled={busy}
            onClick={() => setEmbalarOpen(true)}>📦 EMBALADO</button>
        ) : null}
        {prep !== "blocked" ? (
          <button type="button" className="sat-btn issue" disabled={busy}
            onClick={() => setShowReport(true)}>⚠ Reportar problema</button>
        ) : (
          <p className="muted">Pedido bloqueado — resolver desde la bandeja de excepciones.</p>
        )}
      </div>

      {embalarOpen ? (
        <EmbalarModal
          orderId={id}
          onCancel={() => setEmbalarOpen(false)}
          onDone={() => router.push("/erp/sat")}
        />
      ) : null}

      {showReport ? (
        <ReportExceptionModal onSubmit={onReport} onClose={() => setShowReport(false)} busy={busy} />
      ) : null}
    </div>
  );
}
