"use client";

/**
 * Datos clave del contacto, en una fila compacta DENTRO de la cabecera:
 * Email · Teléfono · Empresa · Origen del lead · Sincronizado con · Última
 * actividad · Score.
 *
 * Solo pinta lo que tiene valor (el score siempre, porque se edita aquí):
 * «Sin empresa» o un «—» no merecen sitio en la cabecera. El estado del
 * ciclo ya no va aquí: es el mismo dato que el chip de estado junto al
 * nombre, que también se edita.
 *
 * PR-Ficha-Cleanup: la cell "Etiquetas" se movió a una pestaña dedicada
 * (`tags`) + card en Resumen.
 */
import { Copy, Phone as PhoneIcon } from "lucide-react";
import type { Contact, ExternalReferenceSummary } from "../../lib/api";
import { formatBackendDateTime } from "../../lib/dates";
import { InlineEdit } from "./InlineEdit";

type Props = {
  contact: Contact;
  companyName?: string | null;
  lastActivityAt?: string | null;
  primaryPhone?: string | null;
  /** PATCH callback compartido con header — recibe el payload parcial
      y devuelve cuando la mutación está aplicada. */
  onPatch: (payload: Record<string, unknown>) => Promise<void>;
};

const formatDate = (value?: string | null) =>
  formatBackendDateTime(value, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

// «Origen del lead» es `contacts.origin`: de DÓNDE vino el lead («Formulario
// web · boprint.net (español)»). Los vínculos con las integraciones van
// aparte, en «Sincronizado con».
//
// Antes mandaban los vínculos (`external_references_summary`) y `origin` era
// solo el respaldo. Como todo contacto que se sube a Brevo gana una fila de
// `external_references`, el origen de TODOS los leads de formulario acabó
// enseñándose como «Brevo · default» en cuanto se sincronizaban: el dato
// estaba bien, lo que se veía no.
//
// Se mantiene `{system} · {account_id}` en los vínculos porque Bart quiere
// distinguir las 7 cuentas de Agile.
const SYSTEM_LABELS: Record<string, string> = {
  agilecrm: "AgileCRM",
  brevo: "Brevo",
  freshdesk: "Freshdesk",
  factusol: "FactuSOL",
  manual: "Manual",
};

function formatOriginPairs(
  summary: ExternalReferenceSummary[] | undefined,
): string | null {
  if (!summary || summary.length === 0) return null;
  return summary
    .map((ref) => {
      const label = SYSTEM_LABELS[ref.system] ?? ref.system;
      return ref.account_id ? `${label} · ${ref.account_id}` : label;
    })
    .join(", ");
}

function copyToClipboard(value: string) {
  if (typeof navigator !== "undefined" && navigator.clipboard) {
    navigator.clipboard.writeText(value).catch(() => undefined);
  }
}

export function ContactKeyDataStrip({
  contact,
  companyName,
  lastActivityAt,
  primaryPhone,
  onPatch,
}: Props) {
  const phone = primaryPhone ?? contact.phone ?? null;
  const vinculos = formatOriginPairs(contact.external_references_summary);
  // El origen es el del CRM. Los vínculos solo se usan como respaldo cuando
  // no hay origen: un contacto que entró por un sync no tiene otro.
  const originLabel = (contact.origin || "").trim() || vinculos;

  return (
    <section className="contact-strip" aria-label="Datos clave">
      <div className="contact-strip-cell contact-strip-cell-email">
        <span className="contact-strip-label">Email</span>
        {/* PR-Ficha-Cleanup: NO más mailto. El click en el email no
            debe abrir el cliente del SO (Bart's spec); solo el botón
            Copiar dispara una acción. */}
        <span className="contact-strip-value contact-strip-email">
          {contact.email ? (
            <>
              <span className="contact-strip-email-text">{contact.email}</span>
              <button
                type="button"
                className="contact-strip-copy"
                onClick={() => copyToClipboard(contact.email)}
                aria-label="Copiar email"
                title="Copiar email"
              >
                <Copy size={11} aria-hidden />
              </button>
            </>
          ) : (
            <span className="muted">—</span>
          )}
        </span>
      </div>
      {phone ? (
        <div className="contact-strip-cell">
          <span className="contact-strip-label">Teléfono</span>
          <span className="contact-strip-value contact-strip-value-link">
            <a href={`tel:${phone}`}>{phone}</a>
            <button
              type="button"
              className="contact-strip-copy"
              onClick={() => copyToClipboard(phone)}
              aria-label="Copiar teléfono"
              title="Copiar teléfono"
            >
              <PhoneIcon size={11} aria-hidden />
            </button>
          </span>
        </div>
      ) : null}
      {companyName ? (
        <div className="contact-strip-cell">
          <span className="contact-strip-label">Empresa</span>
          <span className="contact-strip-value" title={companyName}>{companyName}</span>
        </div>
      ) : null}
      {originLabel ? (
        <div className="contact-strip-cell">
          <span className="contact-strip-label">Origen del lead</span>
          <span className="contact-strip-value" title={originLabel}>{originLabel}</span>
        </div>
      ) : null}
      {vinculos ? (
        <div className="contact-strip-cell">
          <span className="contact-strip-label">Sincronizado con</span>
          <span className="contact-strip-value" title={vinculos}>{vinculos}</span>
        </div>
      ) : null}
      {lastActivityAt ? (
        <div className="contact-strip-cell">
          <span className="contact-strip-label">Última actividad</span>
          <span className="contact-strip-value">{formatDate(lastActivityAt)}</span>
        </div>
      ) : null}
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Score</span>
        <span className="contact-strip-value">
          {/* Bart: editable por cualquier user — click → input numérico
              save-on-blur. Sin validación de rango (mantenemos el
              lead_score libre como el modelo backend). */}
          <InlineEdit
            kind="number"
            value={contact.lead_score ?? null}
            ariaLabel="Lead score"
            emptyLabel="—"
            display={
              contact.lead_score !== null && contact.lead_score !== undefined ? (
                <strong>{contact.lead_score}</strong>
              ) : (
                <span className="muted">—</span>
              )
            }
            onSave={(next) => onPatch({ lead_score: next })}
            inputStyle={{ width: 80 }}
          />
        </span>
      </div>
    </section>
  );
}
