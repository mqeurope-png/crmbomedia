"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getOrderEmailPreview,
  sendOrderEmail,
  type FactusolPdfLang,
  type OrderEmailPreview,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const EMAIL_LANGS: { value: FactusolPdfLang; label: string }[] = [
  { value: "es", label: "ES" },
  { value: "en", label: "EN" },
  { value: "de", label: "DE" },
  { value: "fr", label: "FR" },
  { value: "nl", label: "NL" },
];

/** Coma o punto y coma separan destinatarios. */
function parseRecipients(raw: string): string[] {
  return raw.split(/[,;]/).map((s) => s.trim()).filter(Boolean);
}

function looksLikeEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

/** ERP · enviar el PEDIDO por email al SAT / taller (y a quien haga falta).
 *
 *  Confirmación obligatoria: se enseñan destinatarios y adjuntos y el envío es
 *  un botón aparte. El ALBARÁN va marcado por defecto (es el envío típico al
 *  taller); el PDF del pedido y el de la factura son opcionales. Si el pedido
 *  aún no tiene albarán, se avisa y se ofrece crearlo — o se envía sin él. */
export function OrderEmailModal({
  orderId,
  orderNumber,
  onClose,
  onSent,
  onCreateAlbaran,
}: {
  orderId: string;
  orderNumber?: string;
  onClose: () => void;
  /** Tras enviar (para refrescar el timeline de la ficha). */
  onSent?: (result: { to: string[]; attachments: string[] }) => void;
  /** «Crear albarán en FACTUSOL» desde el aviso de albarán ausente. */
  onCreateAlbaran?: () => void;
}) {
  const [preview, setPreview] = useState<OrderEmailPreview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [bcc, setBcc] = useState("");
  const [showCopies, setShowCopies] = useState(false);
  const [lang, setLang] = useState<FactusolPdfLang>("es");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [withAlbaran, setWithAlbaran] = useState(true);
  const [withPedido, setWithPedido] = useState(false);
  const [withFactura, setWithFactura] = useState(false);

  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string[] | null>(null);

  const loadPreview = useCallback(
    (langOverride?: FactusolPdfLang, keepRecipients = false) => {
      setLoadError(null);
      let alive = true;
      getOrderEmailPreview(orderId, langOverride)
        .then((p) => {
          if (!alive) return;
          setPreview(p);
          setLang(p.lang);
          setSubject(p.subject);
          setBody(p.body_text);
          if (!keepRecipients) {
            setTo(p.to.join(", "));
            setWithAlbaran(p.defaults.albaran);
            setWithPedido(p.defaults.pedido);
            setWithFactura(p.defaults.factura);
          }
        })
        .catch((e) => {
          if (alive) {
            setLoadError(extractErrorMessage(
              e, "No se pudo preparar el email del pedido.",
            ));
          }
        });
      return () => { alive = false; };
    },
    [orderId],
  );

  useEffect(() => loadPreview(), [loadPreview]);

  const recipients = parseRecipients(to);
  const ccList = parseRecipients(cc);
  const bccList = parseRecipients(bcc);
  const allValid = [...recipients, ...ccList, ...bccList].every(looksLikeEmail);
  const anyAttachment = withAlbaran || withPedido || withFactura;
  const canSend =
    !!preview && !sending && recipients.length > 0 && allValid
    && subject.trim().length > 0 && body.trim().length > 0
    && !!preview.from_alias && anyAttachment;

  async function send() {
    if (!preview || !canSend) return;
    setSending(true);
    setSendError(null);
    try {
      const result = await sendOrderEmail(orderId, {
        confirm: true,
        to: recipients,
        cc: ccList,
        bcc: bccList,
        subject: subject.trim(),
        body_text: body,
        lang,
        from_alias: preview.from_alias,
        include_albaran: withAlbaran,
        include_pedido: withPedido,
        include_factura: withFactura,
      });
      setSentTo(result.to);
      onSent?.({ to: result.to, attachments: result.attachments });
    } catch (e) {
      setSendError(extractErrorMessage(
        e, "No se pudo enviar el pedido. No se ha enviado nada.",
      ));
    } finally {
      setSending(false);
    }
  }

  const label = preview?.order_number ?? orderNumber ?? "";
  const alb = preview?.attachments.albaran;
  const ped = preview?.attachments.pedido;
  const fac = preview?.attachments.factura;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Enviar pedido ${label} por email`}>
      <div className="modal-dialog erp-emit-modal erp-invoice-email">
        <h2>Enviar pedido por email <span className="muted">{label}</span></h2>

        {loadError ? <p className="form-error">{loadError}</p> : null}
        {!preview && !loadError ? <p className="muted">Preparando…</p> : null}

        {sentTo !== null ? (
          <>
            <p className="form-success" role="status">
              Pedido enviado a <strong>{sentTo.join(", ")}</strong>.
            </p>
            <div className="modal-actions">
              <button type="button" className="button" onClick={onClose}>
                Cerrar
              </button>
            </div>
          </>
        ) : preview ? (
          <>
            <p className="muted small">
              Revisa destinatarios y adjuntos antes de enviar. Se envía con la
              cuenta de Gmail integrada y queda registrado en el pedido.
            </p>

            <label className="field">
              <span>Para</span>
              <input
                type="text" value={to} aria-label="Destinatarios"
                placeholder="taller@ejemplo.com, otro@ejemplo.com"
                disabled={sending}
                onChange={(e) => setTo(e.target.value)}
              />
            </label>
            {preview.sat_configured ? (
              <span className="muted small">
                Precargado el SAT de Ajustes ERP ({preview.sat_email}). Puedes
                añadir más separándolos por comas.
              </span>
            ) : (
              <span className="muted small" role="note">
                No hay email del SAT configurado (Ajustes ERP → «Email del SAT
                / taller»). Escribe el destinatario a mano.
              </span>
            )}

            <p>
              <button type="button" className="button small secondary"
                      aria-expanded={showCopies}
                      onClick={() => setShowCopies((v) => !v)}>
                {showCopies ? "▾" : "▸"} CC / CCO
              </button>
            </p>
            {showCopies ? (
              <>
                <label className="field">
                  <span>CC</span>
                  <input type="text" value={cc} aria-label="CC"
                         disabled={sending}
                         onChange={(e) => setCc(e.target.value)} />
                </label>
                <label className="field">
                  <span>CCO</span>
                  <input type="text" value={bcc} aria-label="CCO"
                         disabled={sending}
                         onChange={(e) => setBcc(e.target.value)} />
                </label>
              </>
            ) : null}
            {(to.trim() || cc.trim() || bcc.trim()) && !allValid ? (
              <span className="muted small form-error">
                Revisa las direcciones de correo.
              </span>
            ) : null}

            <fieldset className="erp-email-attachments">
              <legend>Adjuntos</legend>
              <label className="field-toggle">
                <input type="checkbox" checked={withAlbaran}
                       aria-label="Adjuntar albarán"
                       disabled={sending || !alb?.available}
                       onChange={(e) => setWithAlbaran(e.target.checked)} />
                <span>
                  Albarán {alb?.numero ? `(${alb.numero})` : ""}
                  {alb?.available ? " — se adjunta por defecto" : ""}
                </span>
              </label>
              {alb && !alb.available ? (
                <p className="form-error" role="status">
                  {alb.reason}
                  {onCreateAlbaran ? (
                    <>
                      {" "}
                      <button type="button" className="button small"
                              disabled={sending}
                              onClick={onCreateAlbaran}>
                        Crear albarán en FACTUSOL
                      </button>
                    </>
                  ) : null}
                </p>
              ) : null}
              <label className="field-toggle">
                <input type="checkbox" checked={withPedido}
                       aria-label="Adjuntar PDF del pedido"
                       disabled={sending || !ped?.available}
                       onChange={(e) => setWithPedido(e.target.checked)} />
                <span>PDF del pedido {ped?.numero ? `(${ped.numero})` : ""}</span>
              </label>
              {ped && !ped.available ? (
                <span className="muted small">{ped.reason}</span>
              ) : null}
              <label className="field-toggle">
                <input type="checkbox" checked={withFactura}
                       aria-label="Adjuntar factura"
                       disabled={sending || !fac?.available}
                       onChange={(e) => setWithFactura(e.target.checked)} />
                <span>Factura {fac?.numero ? `(${fac.numero})` : ""}</span>
              </label>
              {fac && !fac.available ? (
                <span className="muted small">{fac.reason}</span>
              ) : null}
            </fieldset>
            {!anyAttachment ? (
              <p className="muted small" role="note">
                Marca al menos un documento para adjuntar.
              </p>
            ) : null}

            <label className="field">
              <span>Idioma del correo y de los PDF</span>
              <select value={lang} aria-label="Idioma del correo"
                      disabled={sending}
                      onChange={(e) => {
                        const next = e.target.value as FactusolPdfLang;
                        setLang(next);
                        loadPreview(next, true);
                      }}>
                {EMAIL_LANGS.map((l) => (
                  <option key={l.value} value={l.value}>{l.label}</option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Asunto</span>
              <input type="text" value={subject} aria-label="Asunto"
                     disabled={sending}
                     onChange={(e) => setSubject(e.target.value)} />
            </label>

            <label className="field">
              <span>Mensaje</span>
              <textarea value={body} aria-label="Cuerpo del mensaje" rows={7}
                        disabled={sending}
                        onChange={(e) => setBody(e.target.value)} />
            </label>

            <p className="muted small">
              Se envía desde <strong>{preview.from_alias || "—"}</strong>.
            </p>
            {!preview.from_alias ? (
              <p className="form-error">
                No tienes un alias de envío configurado (en /account).
              </p>
            ) : null}

            {sendError ? <p className="form-error">{sendError}</p> : null}

            <div className="modal-actions">
              <button type="button" className="button secondary"
                      onClick={onClose} disabled={sending}>
                Cancelar
              </button>
              <button type="button" className="button"
                      onClick={send} disabled={!canSend}>
                {sending ? "Enviando…" : "Enviar pedido"}
              </button>
            </div>
          </>
        ) : (
          <div className="modal-actions">
            <button type="button" className="button secondary" onClick={onClose}>
              Cerrar
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
