"use client";

/**
 * Strip horizontal de datos clave del contacto. PR-D BoHub.
 *
 * 7 cells: Email | Teléfono | Empresa | Origen | Última actividad |
 * Score | Estado del ciclo.
 *
 * PR-Ficha-Cleanup: la cell "Etiquetas" se movió a una pestaña
 * dedicada (`tags`) + card en Resumen — abarrotaba el strip y los
 * comerciales con > 5 tags acababan viendo "+N" inútil.
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

const STATUS_LABEL: Record<string, string> = {
  new: "Lead nuevo",
  qualified: "Calificado",
  working: "Trabajando",
  won: "Cliente",
  lost: "Perdido",
};

const STATUS_OPTIONS: ReadonlyArray<[string, string]> = [
  ["new", "Lead nuevo"],
  ["qualified", "Calificado"],
  ["working", "Trabajando"],
  ["won", "Cliente"],
  ["lost", "Perdido"],
];

// PR-Ficha-Cleanup: mostramos `{system} · {account_id}` en lugar de
// solo `agilecrm`. Bart prefería ver "AgileCRM · artisjet-europe"
// para distinguir entre las 7 cuentas Agile. La fuente de verdad es
// `external_references_summary` (siempre presente); `origin` legacy
// es ahora solo fallback.
const SYSTEM_LABELS: Record<string, string> = {
  agilecrm: "AgileCRM",
  brevo: "Brevo",
  freshdesk: "Freshdesk",
  factusol: "FactuSOL",
  manual: "Manual",
};

function formatOriginPairs(
  summary: ExternalReferenceSummary[] | undefined,
  fallbackOrigin: string | null | undefined,
): string | null {
  if (summary && summary.length > 0) {
    const parts = summary.map((ref) => {
      const label = SYSTEM_LABELS[ref.system] ?? ref.system;
      return ref.account_id ? `${label} · ${ref.account_id}` : label;
    });
    return parts.join(", ");
  }
  return fallbackOrigin ?? null;
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
  const originLabel = formatOriginPairs(
    contact.external_references_summary,
    contact.origin,
  );

  return (
    <section className="contact-strip" aria-label="Datos clave">
      <div className="contact-strip-cell contact-strip-cell-email">
        <span className="contact-strip-label">Email</span>
        {/* PR-Ficha-Cleanup: NO más mailto. El click en el email no
            debe abrir el cliente del SO (Bart's spec); solo el botón
            Copiar dispara una acción. `break-all` evita overflow
            horizontal en emails largos. */}
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
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Teléfono</span>
        <span className="contact-strip-value contact-strip-value-link">
          {phone ? (
            <>
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
            </>
          ) : (
            <span className="muted">—</span>
          )}
        </span>
      </div>
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Empresa</span>
        <span className="contact-strip-value">
          {companyName ?? <span className="muted">Sin empresa</span>}
        </span>
      </div>
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Origen del lead</span>
        <span className="contact-strip-value">
          {originLabel ?? <span className="muted">—</span>}
        </span>
      </div>
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Última actividad</span>
        <span className="contact-strip-value">
          {lastActivityAt ? formatDate(lastActivityAt) : <span className="muted">—</span>}
        </span>
      </div>
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Score</span>
        <span className="contact-strip-value">
          {/* Bart: editable por cualquier user — click → input numérico
              save-on-blur. Sin validación de rango (mantenemos el
              lead_score libre como el modelo backend).
              PR-Ficha-Fix: forzamos width=80px y spin buttons via la
              clase compartida `lead-score-input` (también la usa el
              modal Editar). */}
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
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Estado del ciclo</span>
        <span className="contact-strip-value">
          <InlineEdit
            kind="select"
            value={contact.commercial_status ?? "new"}
            options={STATUS_OPTIONS}
            ariaLabel="Estado del ciclo"
            display={
              <span>
                {STATUS_LABEL[contact.commercial_status ?? "new"] ??
                  contact.commercial_status ??
                  "—"}
              </span>
            }
            onSave={(next) => onPatch({ commercial_status: next })}
          />
        </span>
      </div>
    </section>
  );
}
