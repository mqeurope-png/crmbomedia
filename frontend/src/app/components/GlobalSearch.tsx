"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { listContacts, type Contact } from "../lib/api";

const DEBOUNCE_MS = 250;

/**
 * Contact-only autocomplete: typing 2+ characters fires
 * `/api/contacts?q=…&limit=10` debounced; clicking a result navigates
 * to `/contacts/[id]`. Companies and other entities aren't searchable
 * here yet — contacts are the high-traffic case, the rest stay
 * accessible via their own pages.
 */
export function GlobalSearch() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Contact[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (!wrapper.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setResults([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    const handle = window.setTimeout(() => {
      listContacts({ q: trimmed, limit: 10 })
        .then((page) => setResults(page.items))
        .catch(() => setResults([]))
        .finally(() => setLoading(false));
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query]);

  function pick(contact: Contact) {
    router.push(`/contacts/${contact.id}`);
    setQuery("");
    setResults([]);
    setOpen(false);
  }

  return (
    <div ref={wrapper} className="global-search">
      <Search size={16} aria-hidden className="global-search-icon" />
      <input
        type="search"
        className="global-search-input"
        placeholder="Buscar contactos por nombre o email…"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        aria-label="Buscar contactos"
      />
      {open && query.trim().length >= 2 ? (
        <div className="global-search-panel" role="listbox">
          {loading ? (
            <p className="global-search-empty">Buscando…</p>
          ) : results.length === 0 ? (
            <p className="global-search-empty">Sin resultados.</p>
          ) : (
            <ul>
              {results.map((contact) => (
                <li key={contact.id}>
                  <button
                    type="button"
                    className="global-search-row"
                    onClick={() => pick(contact)}
                  >
                    <span className="global-search-row-name">
                      {[contact.first_name, contact.last_name]
                        .filter(Boolean)
                        .join(" ") || "(Sin nombre)"}
                    </span>
                    <span className="muted small">{contact.email ?? ""}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
