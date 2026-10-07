"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getQuoteEmailPreview,
  sendQuoteEmail,
  type CrmContactHit,
  type FactusolPdfLang,
  type QuoteEmailPreview,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
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

const EMAIL_LANGS: { value: FactusolPdfLang; label: string }[] = [
  { value: "es", label: "ES" },
  { value: "en", label: "EN" },
  { value: "de", label: "DE" },
  { value: "fr", label: "FR" },
  { value: "nl", label: "NL" },
];

/** De dónde salió el idioma propuesto (misma cascada que el PDF). */
const LANG_SOURCE_LABELS: Record<string, string> = {
  pedido: "del pedido",
  cliente: "del cliente",
  pais_documento: "del país en el documento",
  pais_cliente: "del país del cliente",
  empresa: "de la empresa emisora",
  defecto: "por defecto",
};

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

/** Punto A · PREVISUALIZACIÓN obligatoria antes de enviar un presupuesto /
 *  proforma por email, con el mismo molde que el de la factura: contactos de
 *  la empresa (los vinculados premarcados) + direcciones libres, idioma con su
 *  procedencia (editable), asunto y cuerpo editables, remitente de la empresa
 *  emisora de la serie y el PDF adjunto (tipo / moneda / banco elegidos). El
 *  envío es un botón SEPARADO. Si falla, se enseña el error y NO queda marcada
 *  como enviada. No escribe en FACTUSOL. */
export function QuoteEmailModal({
  codpre,
  serie,
  numero,
  variant,
  currency,
  bank,
  initialLang,
  onClose,
  onSent,
}: {
  codpre: string | number;
  serie: number;
  /** Nº visible para el título («2-000075»); si no, se compone. */
  numero?: string | null;
  /** Tipo del PDF adjunto: presupuesto (null) o «proforma», como en «Descargar PDF». */
  variant?: "proforma" | null;
  currency?: string | null;
  bank?: number | null;
  /** Idioma ya elegido (modal de Documentos); si no, la cascada del backend. */
  initialLang?: FactusolPdfLang | null;
  onClose: () => void;
  onSent?: (result: { to: string[]; lang: FactusolPdfLang }) => void;
}) {
  const [preview, setPreview] = useState<QuoteEmailPreview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [contactSel, setContactSel] = useState<Record<string, ContactChannel>>({});
  const [crmPicks, setCrmPicks] = useState<CrmPick[]>([]);
  const [fromAlias, setFromAlias] = useState("");
  const [lang, setLang] = useState<FactusolPdfLang>(initialLang ?? "es");
  const [langSource, setLangSource] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  // El operador tocó asunto o cuerpo: no se vuelven a generar solos.
  const [textDirty, setTextDirty] = useState(false);
  // Recarga en curso (cambio de idioma o de destinatario): no se envía hasta
  // que el cuerpo y el PDF vayan en el mismo idioma; solo la última respuesta aplica.
  const [reloading, setReloading] = useState(false);
  const cancelLoad = useRef<(() => void) | null>(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string[] | null>(null);

  const docLabel = numero ?? `${serie}-${String(codpre).padStart(6, "0")}`;
  const kind = variant === "proforma" ? "proforma" : "presupuesto";

  const loadPreview = useCallback(
    (langOverride?: FactusolPdfLang, keepRecipient = false, contactId?: string) => {
      cancelLoad.current?.();
      let alive = true;
      getQuoteEmailPreview(codpre, serie, {
        lang: langOverride, variant: variant ?? undefined,
        currency: currency ?? undefined, contact_id: contactId,
      })
        .then((p) => {
          if (!alive) return;
          setLoadError(null);
          setPreview(p);
          setLang(p.lang);
          setLangSource(p.lang_source === "selector" ? null : p.lang_source);
          setSubject(p.subject);
          setBody(p.body_text);
          setTextDirty(false);
          if (!keepRecipient) {
            setFromAlias(p.from_alias);
            const contacts = p.company_contacts ?? [];
            // Premarcados: el vinculado (email de la cabecera) o el principal.
            const sel: Record<string, ContactChannel> = {};
            for (const c of contacts) {
              if (c.is_primary && c.has_email && c.email) sel[c.email] = "to";
            }
            setContactSel(sel);
            // Sin ningún contacto premarcado (empresa sin contactos con email
            // o sin empresa), el «Para» es el email de la cabecera.
            setTo(Object.keys(sel).length > 0 ? "" : p.to);
            setCc("");
            setCrmPicks([]);
          }
        })
        .catch((e) => {
          if (alive) {
            setLoadError(extractErrorMessage(e, `No se pudo preparar el email del ${kind}.`));
          }
        })
        .finally(() => { if (alive) setReloading(false); });
      const cancel = () => { alive = false; };
      cancelLoad.current = cancel;
      return cancel;
    },
    [codpre, serie, variant, currency, kind],
  );

  useEffect(() => loadPreview(initialLang ?? undefined), [loadPreview, initialLang]);

  const { to: contactTo, cc: contactCc } = splitContactChannels(contactSel);
  const crmTo = crmPicks.filter((p) => p.channel === "to").map((p) => p.email);
  const crmCc = crmPicks.filter((p) => p.channel === "cc").map((p) => p.email);
  const recipients = dedupeEmails(contactTo, crmTo, parseRecipients(to));
  const ccRecipients = dedupeEmails(contactCc, crmCc, parseRecipients(cc));
  const recipientsValid = recipients.length > 0 && recipients.every(looksLikeEmail);
  const ccValid = ccRecipients.every(looksLikeEmail);
  const canSend =
    !!preview && !sending && !reloading && recipientsValid && ccValid
    && subject.trim().length > 0 && body.trim().length > 0 && !!fromAlias;

  /** A quién saluda el cuerpo según el primer destinatario elegido: el id del
   *  primer contacto de la empresa en «Para»; si no hay, el del primer
   *  contacto del CRM añadido con el buscador; "none" si solo hay direcciones
   *  libres, o null si no hay nadie todavía. */
  function greetingFor(
    sel: Record<string, ContactChannel>, picks: CrmPick[], free: string,
  ): string | null {
    const firstEmail = splitContactChannels(sel).to[0];
    if (firstEmail) {
      const c = (preview?.company_contacts ?? []).find((x) => x.email === firstEmail);
      return c ? c.id : "none";
    }
    const firstPick = picks.find((p) => p.channel === "to");
    if (firstPick) return firstPick.id;
    return parseRecipients(free).length > 0 ? "none" : null;
  }

  /** Si cambia a quién va el correo, se vuelve a generar el saludo del cuerpo
   *  (salvo que el operador ya lo haya escrito a mano). */
  function regreet(sel: Record<string, ContactChannel>, picks: CrmPick[], free: string) {
    if (!preview || textDirty) return;
    const wanted = greetingFor(sel, picks, free);
    if (wanted === null || wanted === (preview.contacto_id ?? "none")) return;
    setReloading(true);
    loadPreview(lang, true, wanted);
  }

  function addCrmPick(contact: CrmContactHit, channel: ContactChannel) {
    const next = [
      ...crmPicks.filter((p) => p.email.toLowerCase() !== contact.email.toLowerCase()),
      { id: contact.id, name: contact.name, email: contact.email, channel },
    ];
    setCrmPicks(next);
    regreet(contactSel, next, to);
  }

  function updateCrmPicks(next: CrmPick[]) {
    setCrmPicks(next);
    regreet(contactSel, next, to);
  }

  async function send() {
    if (!preview || !canSend) return;
    setSending(true);
    setSendError(null);
    try {
      const result = await sendQuoteEmail(codpre, serie, {
        confirm: true,
        to: recipients,
        cc: ccRecipients,
        subject: subject.trim(),
        body_text: body,
        lang,
        from_alias: fromAlias,
        variant: variant ?? null,
        currency: currency ?? "EUR",
        bank: bank ?? null,
      });
      setSentTo(result.to);
      onSent?.({ to: result.to, lang: result.lang });
    } catch (e) {
      setSendError(extractErrorMessage(
        e, `No se pudo enviar el ${kind}. No se ha marcado como enviado.`,
      ));
    } finally {
      setSending(false);
    }
  }

  const sent = sentTo !== null;
  const { overlayProps, requestClose } = useModalBehaviour({
    onClose,
    disabled: sending,
    // Asunto o cuerpo retocados a mano y sin enviar: se pregunta antes de cerrar.
    confirmClose: () => !textDirty || sent || window.confirm(UNSAVED_CHANGES_MESSAGE),
  });

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Enviar ${kind} ${docLabel} por email`} {...overlayProps}>
      <div className="modal-dialog erp-modal erp-invoice-email">
        <h2>
          Enviar {kind} por email{" "}
          <span className="muted">{preview?.numero ?? docLabel}</span>
        </h2>
        <ModalCloseButton onClose={requestClose} disabled={sending} />

        {loadError ? <p className="form-error">{loadError}</p> : null}
        {!preview && !loadError ? <p className="muted">Preparando…</p> : null}

        {sent ? (
          <>
            <p className="form-success" role="status">
              {kind === "proforma" ? "Proforma enviada" : "Presupuesto enviado"} a{" "}
              <strong>{sentTo!.join(", ")}</strong> en{" "}
              {EMAIL_LANGS.find((l) => l.value === lang)?.label ?? lang}.
            </p>
            <div className="modal-actions">
              <button type="button" className="button" onClick={onClose}>Cerrar</button>
            </div>
          </>
        ) : preview ? (
          <>
            <p className="muted small">
              Revisa el correo antes de enviarlo. El PDF del {kind} se adjunta y se
              genera en el idioma seleccionado
              {preview.company_name ? <> para <strong>{preview.company_name}</strong></> : null}.
            </p>

            {!preview.company_id ? (
              <p className="muted small" role="note">
                Este cliente no está vinculado a una empresa del CRM
                {preview.customer_name ? ` («${preview.customer_name}»)` : ""}: busca el
                contacto en el CRM o escribe la dirección.
              </p>
            ) : null}

            {(preview.company_contacts?.length ?? 0) > 0 ? (
              <CompanyContactsPicker
                contacts={preview.company_contacts ?? []}
                value={contactSel}
                onChange={(next) => { setContactSel(next); regreet(next, crmPicks, to); }}
                disabled={sending}
              />
            ) : null}

            <CrmContactSearch
              onAdd={addCrmPick}
              disabled={sending}
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
                        <button key={ch} type="button" disabled={sending}
                                className={`button small ${p.channel === ch ? "" : "secondary"}`}
                                aria-pressed={p.channel === ch}
                                onClick={() => updateCrmPicks(crmPicks.map((x) => (
                                  x.id === p.id ? { ...x, channel: ch } : x)))}>
                          {ch === "to" ? "Para" : "CC"}
                        </button>
                      ))}
                      <button type="button" className="button small tertiary" disabled={sending}
                              aria-label={`Quitar a ${p.name}`}
                              onClick={() => updateCrmPicks(crmPicks.filter((x) => x.id !== p.id))}>
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
                     placeholder="cliente@ejemplo.com" disabled={sending}
                     onChange={(e) => setTo(e.target.value)}
                     onBlur={(e) => regreet(contactSel, crmPicks, e.target.value)} />
            </label>
            <label className="field">
              <span>CC (otras direcciones)</span>
              <input type="text" value={cc} aria-label="Copia (CC)" placeholder="opcional"
                     disabled={sending} onChange={(e) => setCc(e.target.value)} />
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
              <select value={lang} aria-label="Idioma del correo" disabled={sending || reloading}
                      onChange={(e) => {
                        const next = e.target.value as FactusolPdfLang;
                        setLang(next);
                        setLangSource(null);
                        setReloading(true);
                        loadPreview(next, true, greetingFor(contactSel, crmPicks, to) ?? undefined);
                      }}>
                {EMAIL_LANGS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
              </select>
            </label>
            <span className="muted small">
              {langSource
                ? `Idioma ${LANG_SOURCE_LABELS[langSource] ?? langSource} (editable).`
                : "Idioma elegido a mano."}
            </span>

            <label className="field">
              <span>Asunto</span>
              <input type="text" value={subject} aria-label="Asunto" disabled={sending || reloading}
                     onChange={(e) => { setSubject(e.target.value); setTextDirty(true); }} />
            </label>
            <label className="field">
              <span>Mensaje</span>
              <textarea value={body} aria-label="Cuerpo del mensaje" rows={10}
                        disabled={sending || reloading}
                        onChange={(e) => { setBody(e.target.value); setTextDirty(true); }} />
            </label>
            {reloading ? <p className="muted small" role="status">Actualizando el texto…</p> : null}

            <p className="muted small">
              <span aria-hidden="true">📎</span>{" "}
              Adjunto: <strong>{preview.attachment_filename}</strong>
            </p>
            <SenderSelect
              defaultAlias={preview.from_alias}
              value={fromAlias}
              onChange={setFromAlias}
              disabled={sending}
              hint={
                <>
                  Por defecto, el remitente
                  {preview.from_alias_source === "serie"
                    ? " de la empresa emisora de la serie"
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

            {sendError ? <p className="form-error">{sendError}</p> : null}

            <div className="modal-actions">
              <button type="button" className="button secondary" onClick={onClose}
                      disabled={sending}>
                Cancelar
              </button>
              <button type="button" className="button" onClick={send} disabled={!canSend}>
                {sending ? "Enviando…" : kind === "proforma" ? "Enviar proforma" : "Enviar presupuesto"}
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

/** «Enviada 02/10» (con los destinatarios en el título) o null si nunca se envió. */
export function emailedMark(
  emailedAt: string | null | undefined, emailedTo: string[] | null | undefined,
): { label: string; title: string } | null {
  if (!emailedAt) return null;
  // Fecha en la zona del navegador (el evento se guarda en UTC: a las 0:30
  // hora española sigue siendo «hoy», no «ayer»).
  const dt = new Date(emailedAt);
  let d: string; let m: string; let y: string;
  if (Number.isNaN(dt.getTime())) {
    [y, m, d] = emailedAt.slice(0, 10).split("-");
  } else {
    d = String(dt.getDate()).padStart(2, "0");
    m = String(dt.getMonth() + 1).padStart(2, "0");
    y = String(dt.getFullYear());
  }
  const to = (emailedTo ?? []).join(", ");
  return {
    label: `Enviada ${d}/${m}`,
    title: `Enviada por email el ${d}/${m}/${y}${to ? ` a ${to}` : ""}`,
  };
}
