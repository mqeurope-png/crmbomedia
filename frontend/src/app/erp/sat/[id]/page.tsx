"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PackingForm } from "../../../components/erp/EmbalarModal";
import { GeneiShipmentSection } from "../../../components/erp/GeneiShipmentSection";
import { ReportExceptionModal } from "../../../components/erp/ReportExceptionModal";
import { SatObservaciones, SatTechData } from "../../../components/erp/SatTechData";
import { getCurrentUser } from "../../../lib/api";
import { Cap, can } from "../../../lib/capabilities";
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
 *  estado (Empezar / Embalar) o reportar un problema. Táctil, botones grandes.
 *  Lote 2 · PR-2: lo mismo que la card — observaciones del comercial arriba en
 *  ámbar (solo si hay) y nº de serie / licencia WhiteRIP grandes con «copiar».
 *
 *  Cada paso se hace SIN SALIR del pedido: al pulsar «Empezar» aparecen aquí
 *  mismo los bultos (peso y medidas, varios si hace falta) y «Embalar»; al
 *  embalar, el pedido sigue abierto con el siguiente paso (el envío). Solo se
 *  vuelve a la cola cuando lo decide la persona. */
export default function SatOrderWorkPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showReport, setShowReport] = useState(false);
  const [canShip, setCanShip] = useState(false);

  useEffect(() => {
    getCurrentUser()
      .then((u) => setCanShip(can(u, Cap.SAT_SHIPPING)))
      .catch(() => setCanShip(false));
  }, []);

  const load = useCallback(() => {
    getOrder(id)
      .then(setOrder)
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar el pedido.")));
  }, [id]);

  useEffect(() => { load(); }, [load]);

  /** Avanza el estado y SIGUE en el pedido (se recarga aquí mismo). */
  async function advance(toStatus: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fireTransition(id, { domain: "preparation", to_status: toStatus });
      if (toStatus === "preparing") {
        setNotice("Preparación empezada. Cuando lo tengas, mete los bultos y embala.");
      }
      load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo avanzar el estado."));
    } finally {
      setBusy(false);
    }
  }

  function onPacked() {
    setNotice("Embalado. Siguiente paso: el envío (o volver a la cola).");
    load();
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
      setNotice("Problema reportado: el pedido queda bloqueado.");
      load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo reportar."));
    } finally {
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
      <Link href="/erp/sat" className="sat-work-back">← Volver a la Cola SAT</Link>
      <h1>{order.order_number}</h1>
      <span className={`badge ${STATUS_LABELS[prep]?.tone ?? "muted"}`}>
        {STATUS_LABELS[prep]?.label ?? prep}
      </span>
      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}

      <SatObservaciones notes={order.notes} />
      <section className="sat-work-tech" aria-label="Datos técnicos">
        <h2>Datos técnicos</h2>
        <SatTechData
          serial={order.serial_number}
          license={order.whiterip_license}
          origin={order.shipping_origin}
        />
      </section>

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

      {prep === "preparing" ? (
        /* Preparación empezada: los bultos y «Embalar» AQUÍ, sin reabrir nada. */
        <section className="sat-work-embalar" aria-label="Embalar">
          <h2>📦 Embalar</h2>
          <PackingForm orderId={id} onDone={onPacked} submitLabel="📦 Embalar" />
        </section>
      ) : null}

      {prep === "packed" ? (
        <section className="sat-work-embalado" aria-label="Envío">
          <h2>✓ Embalado</h2>
          {canShip ? (
            <GeneiShipmentSection orderId={id} canManage={canShip} onChanged={load} />
          ) : (
            <p className="muted small">El envío lo gestiona la oficina desde la Cola SAT.</p>
          )}
        </section>
      ) : null}

      <div className="sat-work-actions">
        {prep === "in_queue" ? (
          <button type="button" className="sat-btn start" disabled={busy}
            onClick={() => advance("preparing")}>▶ EMPEZAR PREPARACIÓN</button>
        ) : null}
        {prep !== "blocked" ? (
          <button type="button" className="sat-btn issue" disabled={busy}
            onClick={() => setShowReport(true)}>⚠ Reportar problema</button>
        ) : (
          <p className="muted">Pedido bloqueado — resolver desde la bandeja de excepciones.</p>
        )}
      </div>

      {showReport ? (
        <ReportExceptionModal onSubmit={onReport} onClose={() => setShowReport(false)} busy={busy} />
      ) : null}
    </div>
  );
}
