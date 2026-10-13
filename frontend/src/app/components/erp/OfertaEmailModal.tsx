"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { type BomediaSustituida, generarBorradorNuevo } from "../../lib/emailsApi";
import {
  getOfertaEmailPreview,
  sendOfertaEmail,
  type CrmContactHit,
  type Oferta,
  type OfertaEmailPreview,
  type OfertaEmailResult,
  type OfertaIdioma,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { AvisoBomedia } from "../email/AsistenteFranja";
import { ModalCloseButton } from "../ModalCloseButton";
import { UNSAVED_CHANGES_MESSAGE, useModalBehaviour } from "../useModalBehaviour";
import {
  CompanyContactsPicker,
  splitContactChannels,
  type ContactChannel,
} from "./CompanyContactsPicker";
import { CrmContactSearch } from "./CrmContactSearch";
import { SenderSelect } from "./SenderSelect";

/** Contacto del CRM añadido con el buscador (de cualquier empresa). */
type CrmPick = { id: string; name: string; email: string; channel: ContactChannel };

const EMAIL_LANGS: { value: OfertaIdioma; label: string }[] = [
  { value: "es", label: "ES" },
  { value: "en", label: "EN" },
  { value: "de", label: "DE" },
  { value: "fr", label: "FR" },
  { value: "nl", label: "NL" },
];

/** Lo que se le pide a la IA si la persona no escribe otra cosa. */
export const INSTRUCCION_POR_DEFECTO = "Envíale la oferta adjunta y resume qué incluye";

const ALIAS_PROBLEMS: Record<string, string> = {
  alias_not_allowed:
    "El remitente no está en tus preferencias (/account) ni es un remitente configurado en Ajustes ERP.",
  not_in_gmail:
    "El remitente no es un «enviar como» verificado de la cuenta de Gmail: añádelo en Gmail (Configuración → Cuentas → Enviar como) y vuelve a abrir.",
  gmail_unavailable:
    "No se pudo comprobar el remitente en Gmail (desconectado o sin permiso).",
  sin_alias:
    "No hay remitente configurado para esta empresa emisora ni alias propio: configúralo en Ajustes ERP → Remitentes.",
};

function parseRecipients(raw: string): string[] {
  return raw.split(/[,;]/).map((s) => s.trim()).filter(Boolean);
}

function looksLikeEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

function dedupeEmails(...lists: string[][]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const list of lists) {
    for (const raw of list) {
      const email = raw.trim();
      const key = email.toLowerCase();
      if (email && !seen.has(key)) {
        seen.add(key);
        out.push(email);
      }
    }
  }
  return out;
}

function asLang(value: string | null | undefined, fallback: OfertaIdioma): OfertaIdioma {
  return EMAIL_LANGS.some((l) => l.value === value) ? (value as OfertaIdioma) : fallback;
}

/** Enviar una OFERTA por email con el PDF adjunto, con el molde del envío de
 *  presupuestos (`QuoteEmailModal`): previsualización obligatoria (contactos
 *  de la empresa con el de la oferta premarcado + buscador del CRM +
 *  direcciones libres), idioma del correo y del PDF, remitente de la empresa
 *  emisora, asunto y cuerpo editables. «Redactar con la IA» pide al asistente
 *  un correo nuevo con la oferta en su contexto y lo vuelca en asunto y
 *  cuerpo (el HTML va como `body_html`). NADA se envía solo: «Enviar oferta»
 *  exige marcar la casilla de confirmación y manda `confirm: true`. Si el
 *  envío falla, se enseña el error y la oferta NO queda como enviada. No
 *  escribe en FACTUSOL. */
export function OfertaEmailModal({
  oferta,
  onClose,
  onSent,
}: {
  oferta: Oferta;
  onClose: () => void;
  onSent: (resultado: OfertaEmailResult) => void;
}) {
  const [preview, setPreview] = useState<OfertaEmailPreview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [contactSel, setContactSel] = useState<Record<string, ContactChannel>>({});
  const [crmPicks, setCrmPicks] = useState<CrmPick[]>([]);
  const [fromAlias, setFromAlias] = useState("");
  const [lang, setLang] = useState<OfertaIdioma>(asLang(oferta.idioma, "es"));
  const [langSource, setLangSource] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  // El HTML del borrador de la IA: viaja como `body_html` mientras el cuerpo
  // sea el suyo; en cuanto la persona lo retoca, se manda solo el texto.
  const [bodyHtml, setBodyHtml] = useState<string | null>(null);
  // Asunto o cuerpo retocados a mano o redactados por la IA: se pregunta
  // antes de cerrar sin enviar.
  const [textDirty, setTextDirty] = useState(false);
  // Recarga en curso (cambio de idioma): el cuerpo y el PDF van en el mismo
  // idioma; solo la última respuesta aplica.
  const [reloading, setReloading] = useState(false);
  const cancelLoad = useRef<(() => void) | null>(null);
  const [instruccion, setInstruccion] = useState(INSTRUCCION_POR_DEFECTO);
  const [redactando, setRedactando] = useState(false);
  const [iaError, setIaError] = useState<string | null>(null);
  const [iaAviso, setIaAviso] = useState<string | null>(null);
  // La guardia de la marca cambió «Bomedia» en el borrador: qué frases, antes y ahora.
  const [iaBomedia, setIaBomedia] = useState<BomediaSustituida | null>(null);
  const [confirmado, setConfirmado] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sentResult, setSentResult] = useState<OfertaEmailResult | null>(null);

  const loadPreview = useCallback(
    (langOverride?: OfertaIdioma, keepRecipients = false) => {
      cancelLoad.current?.();
      let alive = true;
      getOfertaEmailPreview(oferta.id, langOverride)
        .then((p) => {
          if (!alive) return;
          setLoadError(null);
          setPreview(p);
          setLang(asLang(p.lang, langOverride ?? "es"));
          setLangSource(p.lang_source === "selector" ? null : p.lang_source);
          setSubject(p.subject);
          setBody(p.body_text);
          setBodyHtml(null);
          setTextDirty(false);
          setIaAviso(null);
          setIaBomedia(null);
          if (!keepRecipients) {
            setFromAlias(p.from_alias);
            const contacts = p.company_contacts ?? [];
            // Premarcado: el contacto de la oferta (o el principal con email).
            const sel: Record<string, ContactChannel> = {};
            for (const c of contacts) {
              if (c.is_primary && c.has_email && c.email) sel[c.email] = "to";
            }
            setContactSel(sel);
            // Sin ningún contacto premarcado, el «Para» es el email de la oferta.
            setTo(Object.keys(sel).length > 0 ? "" : p.to);
            setCc("");
            setCrmPicks([]);
          }
        })
        .catch((e) => {
          if (alive) {
            setLoadError(extractErrorMessage(e, "No se pudo preparar el email de la oferta."));
          }
        })
        .finally(() => { if (alive) setReloading(false); });
      const cancel = () => { alive = false; };
      cancelLoad.current = cancel;
      return cancel;
    },
    [oferta.id],
  );

  useEffect(() => loadPreview(), [loadPreview]);

  const { to: contactTo, cc: contactCc } = splitContactChannels(contactSel);
  const crmTo = crmPicks.filter((p) => p.channel === "to").map((p) => p.email);
  const crmCc = crmPicks.filter((p) => p.channel === "cc").map((p) => p.email);
  const recipients = dedupeEmails(contactTo, crmTo, parseRecipients(to));
  const ccRecipients = dedupeEmails(contactCc, crmCc, parseRecipients(cc));
  const recipientsValid = recipients.length > 0 && recipients.every(looksLikeEmail);
  const ccValid = ccRecipients.every(looksLikeEmail);
  const busy = sending || reloading || redactando;
  const readyToSend =
    !!preview && !busy && recipientsValid && ccValid
    && subject.trim().length > 0 && body.trim().length > 0 && !!fromAlias;
  const canSend = readyToSend && confirmado;

  function addCrmPick(contact: CrmContactHit, channel: ContactChannel) {
    setCrmPicks([
      ...crmPicks.filter((p) => p.email.toLowerCase() !== contact.email.toLowerCase()),
      { id: contact.id, name: contact.name, email: contact.email, channel },
    ]);
  }

  /** «Redactar con la IA»: un correo nuevo del asistente con la oferta en su
   *  contexto, para el primer destinatario. No envía nada. */
  async function redactar() {
    if (busy || !preview) return;
    const destinatario = recipients[0];
    if (!destinatario || !looksLikeEmail(destinatario)) {
      setIaError("Elige primero un destinatario: la IA redacta para esa persona.");
      return;
    }
    setRedactando(true);
    setIaError(null);
    try {
      const b = await generarBorradorNuevo({
        destinatario,
        instruccion: instruccion.trim() || INSTRUCCION_POR_DEFECTO,
        alcance: "contacto",
        contact_id: oferta.contact_id,
        oferta_id: oferta.id,
        formato: "normal",
      });
      if (b.asunto) setSubject(b.asunto);
      if (b.texto) {
        setBody(b.texto);
        setBodyHtml(b.html || null);
        setTextDirty(true);
        // El aviso va con el cuerpo que describe: solo cambia si cambia el cuerpo.
        setIaBomedia(b.bomedia && b.bomedia.cambios.length > 0 ? b.bomedia : null);
      }
      setIaAviso(b.aviso ?? null);
      if (!b.texto && !b.aviso) {
        setIaError("La IA no ha redactado el correo: revisa la instrucción e inténtalo otra vez.");
      }
    } catch (e) {
      setIaError(extractErrorMessage(e, "La IA no ha podido redactar el correo."));
    } finally {
      setRedactando(false);
    }
  }

  async function send() {
    if (!preview || !canSend) return;
    setSending(true);
    setSendError(null);
    try {
      const result = await sendOfertaEmail(oferta.id, {
        confirm: true,
        to: recipients,
        cc: ccRecipients,
        bcc: [],
        subject: subject.trim(),
        body_text: body,
        body_html: bodyHtml,
        lang,
        from_alias: fromAlias,
      });
      setSentResult(result);
      onSent(result);
    } catch (e) {
      setSendError(extractErrorMessage(
        e, "No se pudo enviar la oferta. No se ha marcado como enviada.",
      ));
    } finally {
      setSending(false);
    }
  }

  const sent = sentResult !== null;
  const { overlayProps, requestClose } = useModalBehaviour({
    onClose,
    disabled: sending,
    confirmClose: () => !textDirty || sent || window.confirm(UNSAVED_CHANGES_MESSAGE),
  });

  const langLabel = EMAIL_LANGS.find((l) => l.value === lang)?.label ?? lang;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Enviar oferta ${oferta.numero} por email`} {...overlayProps}>
      <div className="modal-dialog erp-modal erp-invoice-email">
        <h2>
          Enviar oferta por email{" "}
          <span className="muted">{preview?.numero ?? oferta.numero}</span>
        </h2>
        <ModalCloseButton onClose={requestClose} disabled={sending} />

        {loadError ? <p className="form-error">{loadError}</p> : null}
        {!preview && !loadError ? <p className="muted small">Preparando…</p> : null}

        {sent ? (
          <>
            <p className="form-success" role="status">
              Oferta {sentResult.numero} enviada a <strong>{sentResult.to.join(", ")}</strong> en{" "}
              {langLabel}.
              {sentResult.etapas_movidas.length > 0
                ? " El contacto pasa a Propuesta enviada."
                : null}
            </p>
            <div className="modal-actions">
              <button type="button" className="button" onClick={onClose}>Cerrar</button>
            </div>
          </>
        ) : preview ? (
          <>
            <p className="muted small">
              Revisa el correo antes de enviarlo. El PDF de la oferta se adjunta y se
              genera en el idioma seleccionado
              {preview.company_name ? <> para <strong>{preview.company_name}</strong></> : null}.
            </p>

            {!preview.company_id ? (
              <p className="muted small" role="note">
                El contacto de la oferta no tiene empresa en el CRM: busca otro contacto
                o escribe la dirección.
              </p>
            ) : null}

            {(preview.company_contacts?.length ?? 0) > 0 ? (
              <CompanyContactsPicker
                contacts={preview.company_contacts ?? []}
                value={contactSel}
                onChange={setContactSel}
                disabled={busy}
              />
            ) : null}

            <CrmContactSearch
              onAdd={addCrmPick}
              disabled={busy}
              exclude={[...contactTo, ...contactCc, ...crmPicks.map((p) => p.email)]}
            />
            {crmPicks.length > 0 ? (
              <ul className="erp-contacts-list erp-crm-picks" aria-label="Contactos del CRM añadidos">
                {crmPicks.map((p) => (
                  <li key={p.id} className="erp-contact-row">
                    <span className="erp-contact-info">
                      <span className="erp-contact-name">{p.name}</span>
                      <span className="erp-contact-email muted small">{p.email}</span>
                    </span>
                    <span className="erp-contact-channel" role="group" aria-label={`Canal de ${p.name}`}>
                      {(["to", "cc"] as const).map((ch) => (
                        <button key={ch} type="button" disabled={busy}
                                className={`button small ${p.channel === ch ? "" : "secondary"}`}
                                aria-pressed={p.channel === ch}
                                onClick={() => setCrmPicks(crmPicks.map((x) => (
                                  x.id === p.id ? { ...x, channel: ch } : x)))}>
                          {ch === "to" ? "Para" : "CC"}
                        </button>
                      ))}
                      <button type="button" className="button small tertiary" disabled={busy}
                              aria-label={`Quitar a ${p.name}`}
                              onClick={() => setCrmPicks(crmPicks.filter((x) => x.id !== p.id))}>
                        ✕
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}

            <label className="field">
              <span>
                {(preview.company_contacts?.length ?? 0) > 0 || crmPicks.length > 0
                  ? "Para (otras direcciones)" : "Para"}
              </span>
              <input type="text" value={to} aria-label="Destinatario"
                     placeholder="cliente@ejemplo.com" disabled={busy}
                     onChange={(e) => setTo(e.target.value)} />
            </label>
            <label className="field">
              <span>CC (otras direcciones)</span>
              <input type="text" value={cc} aria-label="Copia (CC)" placeholder="opcional"
                     disabled={busy} onChange={(e) => setCc(e.target.value)} />
            </label>
            {recipients.length > 0 && !recipientsValid ? (
              <span className="muted small form-error">Revisa las direcciones de «Para».</span>
            ) : null}
            {!ccValid ? (
              <span className="muted small form-error">Revisa las direcciones de «CC».</span>
            ) : null}
            {recipients.length === 0 ? (
              <span className="muted small">
                Elige al menos un destinatario (un contacto o una dirección).
              </span>
            ) : null}

            <label className="field">
              <span>Idioma del correo y del PDF</span>
              <select value={lang} aria-label="Idioma del correo" disabled={busy}
                      onChange={(e) => {
                        const next = e.target.value as OfertaIdioma;
                        setLang(next);
                        setLangSource(null);
                        setReloading(true);
                        loadPreview(next, true);
                      }}>
                {EMAIL_LANGS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
              </select>
            </label>
            <span className="muted small">
              {langSource
                ? "Idioma de la oferta (editable). Al cambiarlo se vuelven a generar el asunto y el cuerpo."
                : "Idioma elegido a mano."}
            </span>

            <div className="erp-oferta-ia">
              <label className="field">
                <span>Qué quieres decirle (para la IA)</span>
                <input type="text" value={instruccion} aria-label="Instrucción para la IA"
                       placeholder={INSTRUCCION_POR_DEFECTO} disabled={busy}
                       onChange={(e) => setInstruccion(e.target.value)} />
              </label>
              <button type="button" className="button secondary small"
                      onClick={() => void redactar()} disabled={busy || !preview}
                      title="La IA redacta asunto y cuerpo con la oferta adjunta en su contexto; tú lo revisas y lo envías">
                {redactando ? "Redactando…" : "Redactar con la IA"}
              </button>
              {iaError ? <p className="form-error small" role="alert">{iaError}</p> : null}
              {iaBomedia ? <AvisoBomedia empresa={iaBomedia.empresa} cambios={iaBomedia.cambios} /> : null}
              {iaAviso ? (
                <p className="muted small" role="status">
                  <strong>Revisa antes de enviar:</strong> {iaAviso}
                </p>
              ) : null}
            </div>

            <label className="field">
              <span>Asunto</span>
              <input type="text" value={subject} aria-label="Asunto" disabled={busy}
                     onChange={(e) => { setSubject(e.target.value); setTextDirty(true); }} />
            </label>
            <label className="field">
              <span>Mensaje</span>
              <textarea value={body} aria-label="Cuerpo del mensaje" rows={10}
                        disabled={busy}
                        onChange={(e) => {
                          setBody(e.target.value);
                          setBodyHtml(null);
                          setTextDirty(true);
                        }} />
            </label>
            {reloading ? <p className="muted small" role="status">Actualizando el texto…</p> : null}

            <p className="muted small">
              <span aria-hidden="true">📎</span>{" "}
              Se adjunta el PDF de la oferta {preview.numero} (versión {preview.version}):{" "}
              <strong>{preview.attachment_filename}</strong>
            </p>
            {preview.pendiente_de_envio ? (
              <p className="muted small" role="note">
                Esta versión aún no se ha enviado: el cliente tiene una versión anterior.
              </p>
            ) : null}

            <SenderSelect
              defaultAlias={preview.from_alias}
              value={fromAlias}
              onChange={setFromAlias}
              disabled={busy}
              hint={
                <>
                  Por defecto, el remitente
                  {preview.from_alias_source === "serie"
                    ? " de la empresa emisora de la oferta"
                    : " por defecto del usuario"}
                  ; las respuestas llegan a esa misma dirección.
                </>
              }
            />
            {!fromAlias ? (
              <p className="form-error">
                No hay ningún remitente disponible (revisa la conexión de Gmail y los
                remitentes de Ajustes ERP).
              </p>
            ) : fromAlias === preview.from_alias && preview.from_alias_ok === false ? (
              <p className="form-error" role="alert">
                {ALIAS_PROBLEMS[preview.from_alias_problem ?? ""]
                  ?? "No se podrá enviar desde ese remitente."}
              </p>
            ) : null}

            <label className="field-toggle">
              <input type="checkbox" aria-label="Confirmo el envío" checked={confirmado}
                     disabled={busy} onChange={(e) => setConfirmado(e.target.checked)} />
              <span>
                Confirmo que quiero enviar la oferta {preview.numero} con el PDF adjunto
                {recipients.length > 0 ? <> a <strong>{recipients.join(", ")}</strong></> : null}.
              </span>
            </label>

            {sendError ? <p className="form-error" role="alert">{sendError}</p> : null}

            <div className="modal-actions">
              <button type="button" className="button secondary" onClick={requestClose}
                      disabled={sending}>
                Cancelar
              </button>
              <button type="button" className="button" onClick={() => void send()}
                      disabled={!canSend}
                      title={readyToSend && !confirmado ? "Marca la casilla de confirmación para enviar" : undefined}>
                {sending ? "Enviando…" : "Enviar oferta"}
              </button>
            </div>
          </>
        ) : (
          <div className="modal-actions">
            <button type="button" className="button secondary" onClick={onClose}>Cerrar</button>
          </div>
        )}
      </div>
    </div>
  );
}
