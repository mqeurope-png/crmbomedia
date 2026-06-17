"use client";

import { Briefcase, Globe, Linkedin } from "lucide-react";
import { useState } from "react";
import type { Contact } from "../lib/api";
import { updateContact } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  contact: Contact;
  onSaved: () => void;
};

/** "Información profesional" card on the contact-detail sidebar.
 *  Inline-editable: clicking a value swaps it for an input + Save
 *  button. Empty fields collapse to a "—" placeholder so the
 *  card stays compact when nothing's known. */
export function ContactProfessionalSection({ contact, onSaved }: Props) {
  const [error, setError] = useState<string | null>(null);

  return (
    <section className="contact-card">
      <h4>Información profesional</h4>
      {error ? <p className="form-error">{error}</p> : null}
      <InlineField
        icon={Briefcase}
        label="Puesto"
        value={contact.job_title ?? null}
        onSave={async (v) => {
          try {
            await updateContact(contact.id, { job_title: v ?? null });
            onSaved();
          } catch (err) {
            setError(extractErrorMessage(err, "No se pudo guardar."));
          }
        }}
      />
      <InlineField
        icon={Linkedin}
        label="LinkedIn"
        value={contact.linkedin_url ?? null}
        href={(v) => (v.startsWith("http") ? v : `https://${v}`)}
        onSave={async (v) => {
          try {
            await updateContact(contact.id, { linkedin_url: v ?? null });
            onSaved();
          } catch (err) {
            setError(extractErrorMessage(err, "No se pudo guardar."));
          }
        }}
      />
      <InlineField
        icon={Globe}
        label="Web personal"
        value={contact.personal_website ?? null}
        href={(v) => (v.startsWith("http") ? v : `https://${v}`)}
        onSave={async (v) => {
          try {
            await updateContact(contact.id, { personal_website: v ?? null });
            onSaved();
          } catch (err) {
            setError(extractErrorMessage(err, "No se pudo guardar."));
          }
        }}
      />
    </section>
  );
}

type FieldProps = {
  icon: React.ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  label: string;
  value: string | null;
  /** Optional href builder — when set + value present, the
   *  display state becomes a clickable link in addition to the
   *  Edit toggle. */
  href?: (value: string) => string;
  onSave: (next: string | null) => Promise<void>;
};

function InlineField({ icon: Icon, label, value, href, onSave }: FieldProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value ?? "");
  const [busy, setBusy] = useState(false);

  if (!editing) {
    return (
      <p className="contact-pro-row">
        <Icon size={12} aria-hidden /> <strong>{label}:</strong>{" "}
        {value ? (
          href ? (
            <a href={href(value)} target="_blank" rel="noreferrer noopener">
              {value}
            </a>
          ) : (
            value
          )
        ) : (
          <span className="muted">—</span>
        )}
        <button
          type="button"
          className="contact-pro-edit"
          onClick={() => {
            setDraft(value ?? "");
            setEditing(true);
          }}
          aria-label={`Editar ${label}`}
        >
          Editar
        </button>
      </p>
    );
  }

  return (
    <form
      className="contact-pro-row contact-pro-row-editing"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          const next = draft.trim();
          await onSave(next || null);
          setEditing(false);
        } finally {
          setBusy(false);
        }
      }}
    >
      <Icon size={12} aria-hidden /> <strong>{label}:</strong>
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        autoFocus
      />
      <button
        type="submit"
        className="button small"
        disabled={busy}
      >
        {busy ? "…" : "Guardar"}
      </button>
      <button
        type="button"
        className="button secondary small"
        onClick={() => setEditing(false)}
        disabled={busy}
      >
        Cancelar
      </button>
    </form>
  );
}
