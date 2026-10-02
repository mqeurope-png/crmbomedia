"use client";

import { useEffect, useState } from "react";
import { searchCrmContacts, type CrmContactHit } from "../../lib/erpApi";
import type { ContactChannel } from "./CompanyContactsPicker";

const DEBOUNCE_MS = 300;
const MIN_CHARS = 2;

/** Remates · punto 3 — buscador de CUALQUIER contacto del CRM (no solo los de
 *  la empresa del documento) para añadirlo a «Para» o «CC» en los envíos por
 *  email. Busca por nombre, email o empresa, como el buscador de la app. */
export function CrmContactSearch({
  onAdd,
  disabled = false,
  exclude = [],
}: {
  onAdd: (contact: CrmContactHit, channel: ContactChannel) => void;
  disabled?: boolean;
  /** Emails ya elegidos: no se vuelven a ofrecer. */
  exclude?: string[];
}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<CrmContactHit[]>([]);
  const [searched, setSearched] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const q = query.trim();
    if (q.length < MIN_CHARS) return undefined;
    let alive = true;
    const handle = window.setTimeout(() => {
      searchCrmContacts(q)
        .then((items) => {
          if (!alive) return;
          setHits(items);
          setSearched(q);
          setError(null);
        })
        .catch(() => {
          if (!alive) return;
          setHits([]);
          setSearched(q);
          setError("No se pudo buscar en el CRM.");
        });
    }, DEBOUNCE_MS);
    return () => { alive = false; window.clearTimeout(handle); };
  }, [query]);

  const excluded = new Set(exclude.map((e) => e.trim().toLowerCase()));
  const visible = query.trim().length >= MIN_CHARS && searched === query.trim()
    ? hits.filter((h) => !excluded.has(h.email.toLowerCase()))
    : [];

  function add(contact: CrmContactHit, channel: ContactChannel) {
    onAdd(contact, channel);
    setQuery("");
    setHits([]);
    setSearched(null);
  }

  return (
    <div className="erp-crm-search">
      <label className="field">
        <span>Buscar contacto del CRM</span>
        <input type="search" value={query} disabled={disabled}
               aria-label="Buscar contacto del CRM"
               placeholder="Nombre, email o empresa…"
               onChange={(e) => setQuery(e.target.value)} />
      </label>
      {error ? <span className="muted small form-error">{error}</span> : null}
      {visible.length > 0 ? (
        <ul className="erp-contacts-list erp-crm-search-hits" aria-label="Contactos del CRM encontrados">
          {visible.map((h) => (
            <li key={h.id} className="erp-contact-row">
              <span className="erp-contact-info">
                <span className="erp-contact-name">
                  {h.name}
                  {h.company_name ? <span className="muted small"> · {h.company_name}</span> : null}
                </span>
                <span className="erp-contact-email muted small">{h.email}</span>
              </span>
              <span className="erp-contact-channel" role="group" aria-label={`Añadir a ${h.name}`}>
                <button type="button" className="button small" disabled={disabled}
                        aria-label={`Añadir a ${h.name} en Para`}
                        onClick={() => add(h, "to")}>
                  Para
                </button>
                <button type="button" className="button small secondary" disabled={disabled}
                        aria-label={`Añadir a ${h.name} en CC`}
                        onClick={() => add(h, "cc")}>
                  CC
                </button>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {searched !== null && searched === query.trim() && visible.length === 0 && !error ? (
        <span className="muted small">Ningún contacto del CRM con email coincide.</span>
      ) : null}
    </div>
  );
}
