"use client";

import { useEffect, useState } from "react";
import {
  getOrderEmailPreview,
  mandarASat,
  type MandarSatResult,
  type OrderDetail,
  type OrderEmailPreview,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";
import { SenderSelect } from "./SenderSelect";

/** «Mandar a SAT para su preparación y envío» (30/10/2026).
 *
 *  Un pedido creado desde un documento de FACTUSOL ya está decidido: lo que
 *  falta es que el taller lo prepare y lo envíe. Se enseña lo que se manda y,
 *  con una sola confirmación, el pedido entra en la Cola SAT y —si se marca la
 *  casilla— sale el correo al taller: el MISMO de «Enviar a SAT», a la
 *  dirección del taller y sin factura.
 *
 *  La casilla viene MARCADA cuando hay dirección del taller y se puede
 *  escribirle: mandarlo a preparar y avisar de que va es una sola decisión, y
 *  lo normal es querer las dos. Sin dirección configurada viene sin marcar. */

function lista(raw: string): string[] {
  return raw.split(/[,;]/).map((s) => s.trim()).filter(Boolean);
}

function esEmail(v: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
}

export function MandarSatModal({
  order, puedeEscribirAlTaller, onClose, onDone,
}: {
  order: OrderDetail;
  /** Capacidad de mandar el correo al SAT (sin ella no hay casilla). */
  puedeEscribirAlTaller: boolean;
  onClose: () => void;
  onDone: (r: MandarSatResult) => void;
}) {
  const [conCorreo, setConCorreo] = useState(puedeEscribirAlTaller);
  const [preview, setPreview] = useState<OrderEmailPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [fromAlias, setFromAlias] = useState("");
  const [withAlbaran, setWithAlbaran] = useState(true);
  const [withPedido, setWithPedido] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { overlayProps, requestClose } = useModalBehaviour({ onClose, disabled: busy });

  const info = order.mandar_sat;
  // Sin albarán aún pero lo tendrá: se crea (proforma) o se vincula el de la
  // factura. El correo, si lo lleva, sale en cuanto exista.
  const albaranLlegara = !!info?.albaran_lo_crea_bohub || !!info?.factura_sin_albaran;

  useEffect(() => {
    if (!puedeEscribirAlTaller) return;
    let vivo = true;
    getOrderEmailPreview(order.id)
      .then((p) => {
        if (!vivo) return;
        setPreview(p);
        setTo(p.to.join(", "));
        setSubject(p.subject);
        setBody(p.body_text);
        setFromAlias(p.from_alias);
        // El albarán va si existe o si se crea al mandarlo (el correo espera).
        setWithAlbaran(p.attachments.albaran.available || albaranLlegara);
        if (!p.sat_configured) setConCorreo(false);
      })
      .catch((e) => {
        if (vivo) setPreviewError(extractErrorMessage(e, "No se pudo preparar el correo al taller."));
      });
    return () => { vivo = false; };
  }, [order.id, puedeEscribirAlTaller, albaranLlegara]);

  const destinatarios = lista(to);
  const copias = lista(cc);
  const direccionesOk = [...destinatarios, ...copias].every(esEmail);
  const alb = preview?.attachments.albaran;
  const ped = preview?.attachments.pedido;
  const algoAdjunto = withAlbaran || withPedido;
  const correoListo = !!preview && destinatarios.length > 0 && direccionesOk
    && subject.trim().length > 0 && body.trim().length > 0 && !!fromAlias && algoAdjunto;
  const puedeMandar = !busy && (!conCorreo || correoListo);

  async function mandar() {
    setBusy(true);
    setError(null);
    try {
      const r = await mandarASat(order.id, conCorreo ? {
        to: destinatarios, cc: copias, bcc: [], subject: subject.trim(), body_text: body,
        lang: preview?.lang ?? "es", from_alias: fromAlias,
        include_albaran: withAlbaran, include_pedido: withPedido, include_factura: false,
      } : null);
      onDone(r);
    } catch (e) {
      setError(extractErrorMessage(
        e, "No se ha podido completar. Cierra y mira la ficha antes de volver a intentarlo.",
      ));
    } finally {
      setBusy(false);
    }
  }

  const lineas = (order.lines ?? []).filter((l) => !l.is_shipping);
  const sinCobrar = order.payment_status !== "paid";

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label="Mandar a SAT para su preparación y envío" {...overlayProps}>
      <div className="modal-dialog erp-modal erp-invoice-email">
        <h2>Mandar a SAT <span className="muted">{order.order_number}</span></h2>
        <ModalCloseButton onClose={requestClose} disabled={busy} />
        <p className="muted small">
          Para su preparación y envío: el pedido entra en la Cola SAT del taller.
        </p>

        <dl className="erp-mandar-sat-resumen">
          <div><dt>Cliente</dt><dd>{order.company_name ?? order.contact_name ?? "—"}</dd></div>
          <div>
            <dt>Lleva</dt>
            <dd>
              {lineas.length === 0 ? "—" : (
                <ul>
                  {lineas.slice(0, 6).map((l) => (
                    <li key={l.id}>{l.quantity} × {l.description || l.product_sku}</li>
                  ))}
                  {lineas.length > 6 ? <li className="muted">y {lineas.length - 6} líneas más</li> : null}
                </ul>
              )}
            </dd>
          </div>
          <div>
            <dt>Albarán</dt>
            <dd>
              {order.factusol_albaran_number ? (
                <span className="mono">{order.factusol_albaran_number}</span>
              ) : info?.factura_sin_albaran ? (
                <span>
                  Se vincula el de la factura de origen. Si la factura no lo apunta, el pedido
                  se queda sin albarán y lo vinculas a mano: <strong>no se crea ninguno</strong>.
                </span>
              ) : info?.es_web ? (
                "Lo crea WooCommerce."
              ) : info?.albaran_lo_crea_bohub ? (
                "Se crea en FACTUSOL al mandarlo, como hasta ahora."
              ) : "Sin albarán."}
            </dd>
          </div>
          {sinCobrar ? (
            <div><dt>Cobro</dt><dd className="muted">Aún sin cobrar: se prepara y se envía igual.</dd></div>
          ) : null}
        </dl>

        {puedeEscribirAlTaller ? (
          <label className="field-toggle">
            <input type="checkbox" checked={conCorreo} disabled={busy}
                   aria-label="Avisar al taller por correo"
                   onChange={(e) => setConCorreo(e.target.checked)} />
            <span>Avisar al taller por correo</span>
          </label>
        ) : null}

        {conCorreo ? (
          <fieldset className="erp-mandar-sat-correo" aria-label="Correo al taller">
            {previewError ? <p className="form-error">{previewError}</p> : null}
            {!preview && !previewError ? <p className="muted">Preparando el correo…</p> : null}
            {preview ? (
              <>
                <label className="field">
                  <span>Para</span>
                  <input type="text" value={to} aria-label="Destinatarios" disabled={busy}
                         readOnly={!!preview.recipients_locked}
                         onChange={(e) => setTo(e.target.value)} />
                </label>
                {!preview.recipients_locked ? (
                  <label className="field">
                    <span>CC</span>
                    <input type="text" value={cc} aria-label="CC" disabled={busy}
                           onChange={(e) => setCc(e.target.value)} />
                  </label>
                ) : null}
                {(to.trim() || cc.trim()) && !direccionesOk ? (
                  <span className="muted small form-error">Revisa las direcciones de correo.</span>
                ) : null}
                <label className="field-toggle">
                  <input type="checkbox" checked={withAlbaran} aria-label="Adjuntar albarán"
                         disabled={busy || (!alb?.available && !albaranLlegara)}
                         onChange={(e) => setWithAlbaran(e.target.checked)} />
                  <span>
                    Albarán {alb?.numero ? `(${alb.numero})` : ""}
                    {!alb?.available && albaranLlegara && withAlbaran
                      ? " — el correo sale en cuanto el albarán exista" : ""}
                  </span>
                </label>
                <label className="field-toggle">
                  <input type="checkbox" checked={withPedido} aria-label="Adjuntar PDF del pedido"
                         disabled={busy || !ped?.available}
                         onChange={(e) => setWithPedido(e.target.checked)} />
                  <span>PDF del pedido {ped?.numero ? `(${ped.numero})` : ""}</span>
                </label>
                {!algoAdjunto ? (
                  <p className="muted small" role="note">Marca al menos un documento para adjuntar.</p>
                ) : null}
                <p className="muted small">Sin factura: es el correo al taller de siempre.</p>
                <label className="field">
                  <span>Asunto</span>
                  <input type="text" value={subject} aria-label="Asunto" disabled={busy}
                         onChange={(e) => setSubject(e.target.value)} />
                </label>
                <label className="field">
                  <span>Mensaje</span>
                  <textarea value={body} aria-label="Cuerpo del mensaje" rows={5} disabled={busy}
                            onChange={(e) => setBody(e.target.value)} />
                </label>
                <SenderSelect defaultAlias={preview.from_alias} value={fromAlias}
                              onChange={setFromAlias} disabled={busy} />
              </>
            ) : null}
          </fieldset>
        ) : (
          <p className="muted small">No se manda ningún correo.</p>
        )}

        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button type="button" className="button" onClick={() => void mandar()}
                  disabled={!puedeMandar}>
            {busy ? "Mandando…" : conCorreo ? "Mandar a SAT y avisar al taller" : "Mandar a SAT"}
          </button>
        </div>
      </div>
    </div>
  );
}
