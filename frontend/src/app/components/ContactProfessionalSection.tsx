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
  // Los campos vacíos no gastan una fila cada uno con un «—»: se resumen en
  // una línea discreta y «Añadir» los despliega para editarlos.
  const [mostrarVacios, setMostrarVacios] = useState(false);

  const campos = [
    {
      key: "job_title", icon: Briefcase, label: "Puesto", corto: "puesto",
      value: contact.job_title ?? null,
      href: undefined as ((v: string) => string) | undefined,
    },
    {
      key: "linkedin_url", icon: Linkedin, label: "LinkedIn", corto: "LinkedIn",
      value: contact.linkedin_url ?? null,
      href: (v: string) => (v.startsWith("http") ? v : `https://${v}`),
    },
    {
      key: "personal_website", icon: Globe, label: "Web personal", corto: "web personal",
      value: contact.personal_website ?? null,
      href: (v: string) => (v.startsWith("http") ? v : `https://${v}`),
    },
  ];
  const vacios = campos.filter((c) => !c.value);

  const guardar = (key: string) => async (v: string | null) => {
    try {
      await updateContact(contact.id, { [key]: v ?? null });
      onSaved();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar."));
    }
  };

  return (
    <section className="contact-card">
      <h4>Información profesional</h4>
      {error ? <p className="form-error">{error}</p> : null}
      {campos.map((c) =>
        c.value || mostrarVacios ? (
          <InlineField
            key={c.key}
            icon={c.icon}
            label={c.label}
            value={c.value}
            href={c.href}
            onSave={guardar(c.key)}
          />
        ) : null,
      )}
      {vacios.length > 0 && !mostrarVacios ? (
        <p className="muted small contact-card-vacio">
          Sin {vacios.map((c) => c.corto).join(", ").replace(/, ([^,]*)$/, " ni $1")}.
          <button
            type="button"
            className="contact-summary-link"
            onClick={() => setMostrarVacios(true)}
          >
            Añadir
          </button>
        </p>
      ) : null}
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
