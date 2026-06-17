"use client";

import { MapPin } from "lucide-react";
import { useState } from "react";
import type { Contact } from "../lib/api";
import { updateContact } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  contact: Contact;
  onSaved: () => void;
};

/** "Dirección" card — multi-field editor that ships every column
 *  in a single PATCH so the operator doesn't have to save line by
 *  line. Empty inputs become NULL on the backend. */
export function ContactAddressSection({ contact, onSaved }: Props) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState({
    address_line: contact.address_line ?? "",
    address_city: contact.address_city ?? "",
    address_state: contact.address_state ?? "",
    address_postal_code: contact.address_postal_code ?? "",
    address_country: contact.address_country ?? "",
    address_region: contact.address_region ?? "",
  });

  const display = formatAddress(contact);

  if (!editing) {
    return (
      <section className="contact-card">
        <h4>
          <MapPin size={12} aria-hidden /> Dirección
        </h4>
        {display ? (
          <p className="muted small">{display}</p>
        ) : (
          <p className="muted small">Sin dirección.</p>
        )}
        <button
          type="button"
          className="button secondary small"
          onClick={() => setEditing(true)}
        >
          Editar
        </button>
      </section>
    );
  }

  const onChange = <K extends keyof typeof draft>(k: K, v: string) =>
    setDraft((prev) => ({ ...prev, [k]: v }));

  const onSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await updateContact(contact.id, {
        address_line: draft.address_line.trim() || null,
        address_city: draft.address_city.trim() || null,
        address_state: draft.address_state.trim() || null,
        address_postal_code: draft.address_postal_code.trim() || null,
        address_country: draft.address_country.trim() || null,
        address_region: draft.address_region.trim() || null,
      });
      setEditing(false);
      onSaved();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="contact-card">
      <h4>
        <MapPin size={12} aria-hidden /> Dirección
      </h4>
      <form onSubmit={onSave} className="contact-address-form">
        <label className="field">
          Calle
          <input
            type="text"
            value={draft.address_line}
            onChange={(e) => onChange("address_line", e.target.value)}
          />
        </label>
        <div className="contact-address-row">
          <label className="field">
            Código postal
            <input
              type="text"
              value={draft.address_postal_code}
              onChange={(e) =>
                onChange("address_postal_code", e.target.value)
              }
            />
          </label>
          <label className="field">
            Ciudad
            <input
              type="text"
              value={draft.address_city}
              onChange={(e) => onChange("address_city", e.target.value)}
            />
          </label>
        </div>
        <div className="contact-address-row">
          <label className="field">
            Provincia
            <input
              type="text"
              value={draft.address_state}
              onChange={(e) => onChange("address_state", e.target.value)}
            />
          </label>
          <label className="field">
            País
            <input
              type="text"
              value={draft.address_country}
              onChange={(e) => onChange("address_country", e.target.value)}
            />
          </label>
        </div>
        <label className="field">
          Región
          <input
            type="text"
            value={draft.address_region}
            onChange={(e) => onChange("address_region", e.target.value)}
          />
        </label>
        {error ? <p className="form-error">{error}</p> : null}
        <div className="form-actions">
          <button
            type="button"
            className="button secondary small"
            onClick={() => setEditing(false)}
            disabled={busy}
          >
            Cancelar
          </button>
          <button
            type="submit"
            className="button small"
            disabled={busy}
          >
            {busy ? "Guardando…" : "Guardar"}
          </button>
        </div>
      </form>
    </section>
  );
}

function formatAddress(c: Contact): string {
  const bits = [
    c.address_line,
    [c.address_postal_code, c.address_city].filter(Boolean).join(" "),
    c.address_state,
    c.address_region,
    c.address_country_name ?? c.address_country,
  ].filter((s) => s && s.toString().trim().length > 0);
  return bits.join(", ");
}
