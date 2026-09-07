"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getInvoiceEmailPreview,
  sendInvoiceEmail,
  type FactusolPdfLang,
  type InvoiceEmailLangSource,
  type InvoiceEmailPreview,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** Idiomas soportados (mismo orden y etiquetas que el selector del PDF). Se
 *  define aquí y no se importa de FactusolDocumentDetailModal para no crear
 *  una dependencia circular (ese modal ya importa este). */
const EMAIL_LANGS: { value: FactusolPdfLang; label: string }[] = [
  { value: "es", label: "ES" },
  { value: "en", label: "EN" },
  { value: "de", label: "DE" },
  { value: "fr", label: "FR" },
  { value: "nl", label: "NL" },
];

/** ERP-F1 — de dónde salió el idioma propuesto para el email (misma cascada
 *  que el PDF), para que quien envía sepa si es dato real o suposición. */
const LANG_SOURCE_LABELS: Record<InvoiceEmailLangSource, string> = {
  pedido: "del pedido",
  cliente: "del cliente",
  pais_cliente: "del país del cliente",
  empresa: "de la empresa emisora",
  defecto: "por defecto",
};

/** Separa el campo de destinatarios (coma o punto y coma) en emails limpios. */
function parseRecipients(raw: string): string[] {
  return raw
    .split(/[,;]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function looksLikeEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

/** ERP-F1 Parte 2 — PREVISUALIZACIÓN OBLIGATORIA antes de enviar la factura
 *  por email. Enseña destinatario (editable), idioma (editable, con su
 *  procedencia), asunto y cuerpo (editables) y el adjunto identificado; el
 *  envío es un botón SEPARADO — nunca un clic directo. El PDF adjunto se
 *  genera al vuelo con las mismas opciones (banco/variante) que la descarga.
 *
 *  Si el envío falla (Gmail sin conectar, dirección inválida) se enseña el
 *  error y NO se marca como enviada: el PDF se regenera y se puede reintentar. */
export function InvoiceEmailModal({
  serie,
  codigo,
  numero,
  bank,
  variant,
  onClose,
  onSent,
}: {
  serie: number;
  codigo: number;
  /** Número legible para el título («5-000063»); si no, se compone. */
  numero?: string;
  /** Índice de cuenta bancaria elegido en la descarga (para que el PDF
   *  adjunto salga idéntico). */
  bank?: number | null;
  /** "anticipo" si se estaba viendo esa variante; si no, factura normal. */
  variant?: "anticipo" | null;
  onClose: () => void;
  /** Se llama tras enviar con éxito (para refrescar el timeline del pedido). */
  onSent?: (result: { to: string[]; lang: FactusolPdfLang }) => void;
}) {
  const [preview, setPreview] = useState<InvoiceEmailPreview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Campos editables del formulario.
  const [to, setTo] = useState("");
  const [lang, setLang] = useState<FactusolPdfLang>("es");
  const [langSource, setLangSource] = useState<InvoiceEmailLangSource | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");

  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string[] | null>(null);

  const docLabel = numero ?? `${serie}-${String(codigo).padStart(6, "0")}`;

  // Carga (o recarga por cambio de idioma) la previsualización. En la primera
  // carga inicializa TODO; al cambiar el idioma preserva lo que el usuario ya
  // tocó del destinatario (el idioma solo re-traduce asunto/cuerpo/adjunto).
  const loadPreview = useCallback(
    (langOverride?: FactusolPdfLang, keepRecipient = false) => {
      setLoadError(null);
      let alive = true;
      getInvoiceEmailPreview(serie, codigo, langOverride)
        .then((p) => {
          if (!alive) return;
          setPreview(p);
          setLang(p.lang);
          setLangSource(p.lang_source);
          setSubject(p.subject);
          setBody(p.body_text);
          if (!keepRecipient) setTo(p.to);
        })
        .catch((e) => {
          if (alive) {
            setLoadError(extractErrorMessage(
              e, "No se pudo preparar el email de la factura.",
            ));
          }
        });
      return () => { alive = false; };
    },
    [serie, codigo],
  );

  useEffect(() => loadPreview(), [loadPreview]);

  const recipients = parseRecipients(to);
  const recipientsValid = recipients.length > 0 && recipients.every(looksLikeEmail);
  const canSend =
    !!preview && !sending && recipientsValid && subject.trim().length > 0
    && body.trim().length > 0 && !!preview.from_alias;

  async function send() {
    if (!preview || !canSend) return;
    setSending(true);
    setSendError(null);
    try {
      const result = await sendInvoiceEmail(serie, codigo, {
        confirm: true,
        to: recipients,
        subject: subject.trim(),
        body_text: body,
        lang,
        from_alias: preview.from_alias,
        reply_to_message_id: preview.reply_to_message_id,
        bank: bank ?? null,
        variant: variant ?? null,
      });
      setSentTo(result.to);
      onSent?.({ to: result.to, lang: result.lang });
    } catch (e) {
      setSendError(extractErrorMessage(
        e, "No se pudo enviar la factura. No se ha marcado como enviada.",
      ));
    } finally {
      setSending(false);
    }
  }

  const sent = sentTo !== null;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Enviar factura ${docLabel} por email`}>
      <div className="modal-dialog erp-emit-modal erp-invoice-email">
        <h2>
          Enviar factura por email{" "}
          <span className="muted">{preview?.numero ?? docLabel}</span>
        </h2>

        {loadError ? <p className="form-error">{loadError}</p> : null}
        {!preview && !loadError ? <p className="muted">Preparando…</p> : null}

        {sent ? (
          <>
            <p className="form-success" role="status">
              Factura enviada a <strong>{sentTo!.join(", ")}</strong> en{" "}
              {EMAIL_LANGS.find((l) => l.value === lang)?.label ?? lang}.
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
              Revisa el correo antes de enviarlo. El PDF de la factura se
              adjunta y se genera en el idioma seleccionado.
            </p>

            <label className="field">
              <span>Para</span>
              <input
                type="text"
                value={to}
                aria-label="Destinatario"
                placeholder="cliente@ejemplo.com"
                disabled={sending}
                onChange={(e) => setTo(e.target.value)}
              />
            </label>
            {to.trim() && !recipientsValid ? (
              <span className="muted small form-error">
                Revisa la dirección de correo.
              </span>
            ) : null}

            <label className="field">
              <span>Idioma del correo y del PDF</span>
              <select
                value={lang}
                aria-label="Idioma del correo"
                disabled={sending}
                onChange={(e) => {
                  const next = e.target.value as FactusolPdfLang;
                  setLang(next);
                  setLangSource(null); // elección manual: ya no es sugerido
                  loadPreview(next, true); // re-traduce asunto/cuerpo/adjunto
                }}
              >
                {EMAIL_LANGS.map((l) => (
                  <option key={l.value} value={l.value}>{l.label}</option>
                ))}
              </select>
            </label>
            <span className="muted small">
              {langSource
                ? `Idioma ${LANG_SOURCE_LABELS[langSource]} (editable).`
                : "Idioma elegido a mano."}
            </span>

            <label className="field">
              <span>Asunto</span>
              <input
                type="text"
                value={subject}
                aria-label="Asunto"
                disabled={sending}
                onChange={(e) => setSubject(e.target.value)}
              />
            </label>

            <label className="field">
              <span>Mensaje</span>
              <textarea
                value={body}
                aria-label="Cuerpo del mensaje"
                rows={8}
                disabled={sending}
                onChange={(e) => setBody(e.target.value)}
              />
            </label>

            <p className="muted small">
              <span aria-hidden="true">📎</span>{" "}
              Adjunto: <strong>{preview.attachment_filename}</strong>
            </p>
            <p className="muted small">
              Se envía desde <strong>{preview.from_alias || "—"}</strong>.
              {preview.replies_to_thread
                ? " Se responderá al hilo del pedido."
                : ""}
            </p>
            {!preview.from_alias ? (
              <p className="form-error">
                No tienes un alias de envío configurado (en /account).
              </p>
            ) : null}
            {preview.order_id === null ? (
              <p className="muted small">
                Nota: no se localizó el pedido del CRM; el envío no se
                registrará en un timeline de pedido.
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
                {sending ? "Enviando…" : "Enviar factura"}
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
