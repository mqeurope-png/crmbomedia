"use client";

import { Sparkles } from "lucide-react";
import type { Contact } from "../lib/api";

type Props = {
  contact: Contact;
};

/** "Datos adicionales" — dynamic list of every key in
 *  `contact.custom_fields` that carries a non-empty value. We
 *  intentionally don't hardcode per-key labels: Brevo accounts
 *  rename their attributes freely, and the UI should surface
 *  whatever's there. Keys are normalised lightly for display
 *  (snake_case → "Snake case", titlecase Spanish accents).
 *
 *  Values render verbatim when scalar, JSON-stringified when an
 *  object / array, and prefixed with "Sí/No" when boolean.
 */
export function ContactCustomFieldsSection({ contact }: Props) {
  const entries = collectEntries(contact.custom_fields ?? null);
  if (entries.length === 0) return null;

  return (
    <section className="contact-card">
      <h4>
        <Sparkles size={12} aria-hidden /> Datos adicionales
      </h4>
      <ul className="contact-custom-list">
        {entries.map(([key, value]) => (
          <li key={key}>
            <span className="muted small">{prettifyKey(key)}:</span>{" "}
            {renderValue(value)}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Custom-field keys already promoted into first-class columns —
 *  hidden here to avoid duplicating them in two places on the
 *  ficha. Belt-and-suspenders: as of sub-PR 2 fix the backend
 *  enforces a strict whitelist on every import, so this list is
 *  only a safety net for legacy rows the cleanup script hasn't
 *  walked yet. */
const HIDDEN_KEYS = new Set(
  [
    // Identidad / contacto (en columnas propias).
    "NOMBRE",
    "FIRSTNAME",
    "APELLIDOS",
    "LASTNAME",
    "SMS",
    "PHONE",
    "ESTADO_COMERCIAL",
    "PAIS",
    "COUNTRY",
    "CIUDAD",
    "CITY",
    "PROVINCIA",
    "STATE",
    "CODIGO_POSTAL",
    "CODIGOPOSTAL",
    "POSTAL_CODE",
    "POSTCODE",
    "ZIP",
    "PAIS_REGION",
    "REGION",
    "ADDRESS",
    "DIRECCION",
    "DIRECCIO",
    "JOB_TITLE",
    "JOBTITLE",
    "PUESTO",
    "CARGO",
    "TITLE",
    "LINKEDIN",
    "LINKEDIN_URL",
    "WEB",
    "WEBSITE",
    "LEAD_SCORE",
    // Empresa (resuelta en el resolver de companies).
    "EMPRESA",
    "CIF",
    // Estado de envío que ya tiene su propio chip / sección.
    "EMAILABLE_UNSUBSCRIBED",
    "BLOCKLISTED",
  ].map((k) => k.toUpperCase()),
);

function collectEntries(
  custom: Record<string, unknown> | null,
): Array<[string, unknown]> {
  if (!custom || typeof custom !== "object") return [];
  return Object.entries(custom)
    .filter(([k, v]) => {
      if (HIDDEN_KEYS.has(k.toUpperCase())) return false;
      if (v === null || v === undefined) return false;
      if (typeof v === "string" && v.trim() === "") return false;
      return true;
    })
    .sort(([a], [b]) => a.localeCompare(b));
}

function prettifyKey(key: string): string {
  // GRADO_DE_INTERES → "Grado de interes"; tipoDeCentro → "Tipo de centro"
  const spaced = key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function renderValue(value: unknown): React.ReactNode {
  if (typeof value === "boolean") return value ? "Sí" : "No";
  if (typeof value === "number") return value.toString();
  if (typeof value === "string") return value;
  return <code>{JSON.stringify(value)}</code>;
}
