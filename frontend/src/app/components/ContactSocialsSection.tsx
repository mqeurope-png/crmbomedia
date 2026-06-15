"use client";

import {
  Facebook,
  Github,
  Globe,
  Pencil,
  Save,
  Twitter,
  Youtube,
} from "lucide-react";
import { useState } from "react";
import type { Contact } from "../lib/api";
import { updateContact } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  contact: Contact;
  onSaved: () => void;
};

/** Twitter + Facebook pinned to columns; the rest of the network
 *  set lives in `social_profiles_json` and renders dynamically
 *  with a per-key icon when known, a generic Globe otherwise.
 *
 *  Editing is "all-or-nothing": one "Editar" button swaps the
 *  whole section to a textarea that holds the JSON + the two
 *  columns. Save sends a single PATCH so the operator doesn't
 *  micro-save each network. */
export function ContactSocialsSection({ contact, onSaved }: Props) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const initialOthers = parseSocials(contact.social_profiles_json);
  const [draft, setDraft] = useState({
    twitter_url: contact.twitter_url ?? "",
    facebook_url: contact.facebook_url ?? "",
    others: stringifyOthers(initialOthers),
  });

  const others = parseSocials(contact.social_profiles_json);
  const hasPinned = Boolean(contact.twitter_url || contact.facebook_url);
  const hasOthers = Object.keys(others).length > 0;

  if (!editing) {
    if (!hasPinned && !hasOthers) {
      return (
        <section className="contact-card">
          <h4>Redes sociales</h4>
          <p className="muted small">Sin redes registradas.</p>
          <button
            type="button"
            className="btn small"
            onClick={() => setEditing(true)}
          >
            <Pencil size={11} aria-hidden /> Editar
          </button>
        </section>
      );
    }
    return (
      <section className="contact-card">
        <h4>Redes sociales</h4>
        <ul className="contact-socials-list">
          {contact.twitter_url ? (
            <SocialRow
              icon={Twitter}
              label="Twitter"
              url={contact.twitter_url}
            />
          ) : null}
          {contact.facebook_url ? (
            <SocialRow
              icon={Facebook}
              label="Facebook"
              url={contact.facebook_url}
            />
          ) : null}
          {Object.entries(others).map(([key, value]) => (
            <SocialRow
              key={key}
              icon={iconForNetwork(key)}
              label={prettifyKey(key)}
              url={String(value)}
            />
          ))}
        </ul>
        <button
          type="button"
          className="btn small"
          onClick={() => setEditing(true)}
        >
          <Pencil size={11} aria-hidden /> Editar
        </button>
      </section>
    );
  }

  const onSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const othersParsed = parseDraftOthers(draft.others);
      await updateContact(contact.id, {
        twitter_url: draft.twitter_url.trim() || null,
        facebook_url: draft.facebook_url.trim() || null,
        // The PATCH endpoint accepts `social_profiles_json` as a
        // raw string; serialise here so the UI doesn't have to
        // know about the JSON column type.
        social_profiles_json: Object.keys(othersParsed).length
          ? JSON.stringify(othersParsed)
          : null,
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
      <h4>Redes sociales</h4>
      <form onSubmit={onSave} className="contact-socials-form">
        <label className="field">
          Twitter
          <input
            type="text"
            value={draft.twitter_url}
            onChange={(e) =>
              setDraft({ ...draft, twitter_url: e.target.value })
            }
            placeholder="https://twitter.com/handle"
          />
        </label>
        <label className="field">
          Facebook
          <input
            type="text"
            value={draft.facebook_url}
            onChange={(e) =>
              setDraft({ ...draft, facebook_url: e.target.value })
            }
            placeholder="https://facebook.com/handle"
          />
        </label>
        <label className="field">
          Otras redes (una por línea: <code>red=url</code>)
          <textarea
            rows={4}
            value={draft.others}
            onChange={(e) => setDraft({ ...draft, others: e.target.value })}
            placeholder={"skype=user.handle\ngithub=https://github.com/handle"}
          />
        </label>
        {error ? <p className="form-error">{error}</p> : null}
        <div className="form-actions">
          <button
            type="button"
            className="btn small"
            onClick={() => setEditing(false)}
            disabled={busy}
          >
            Cancelar
          </button>
          <button
            type="submit"
            className="btn btn-primary small"
            disabled={busy}
          >
            <Save size={11} aria-hidden />{" "}
            {busy ? "Guardando…" : "Guardar"}
          </button>
        </div>
      </form>
    </section>
  );
}

function SocialRow({
  icon: Icon,
  label,
  url,
}: {
  icon: React.ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  label: string;
  url: string;
}) {
  const href = url.startsWith("http") ? url : `https://${url}`;
  return (
    <li className="contact-socials-row">
      <Icon size={12} aria-hidden />
      <strong>{label}:</strong>{" "}
      <a href={href} target="_blank" rel="noreferrer noopener">
        {url}
      </a>
    </li>
  );
}

function parseSocials(
  raw: string | null | undefined,
): Record<string, string> {
  if (!raw) return {};
  try {
    const decoded = JSON.parse(raw);
    if (decoded && typeof decoded === "object") {
      return Object.fromEntries(
        Object.entries(decoded).filter(([, v]) => typeof v === "string" && v),
      ) as Record<string, string>;
    }
  } catch {
    /* fall through */
  }
  return {};
}

function stringifyOthers(others: Record<string, string>): string {
  return Object.entries(others)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

function parseDraftOthers(raw: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf("=");
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim().toLowerCase();
    const value = trimmed.slice(eq + 1).trim();
    if (key && value) out[key] = value;
  }
  return out;
}

const NETWORK_ICONS: Record<
  string,
  React.ComponentType<{ size?: number; "aria-hidden"?: boolean }>
> = {
  github: Github,
  youtube: Youtube,
};

function iconForNetwork(key: string) {
  return NETWORK_ICONS[key.toLowerCase()] ?? Globe;
}

function prettifyKey(key: string): string {
  return key.charAt(0).toUpperCase() + key.slice(1);
}
