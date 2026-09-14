"use client";

import { useEffect, useId, useRef, useState } from "react";
import { listCompanies, type Company } from "../lib/companiesApi";

const DEBOUNCE_MS = 250;
const LIMIT = 8;

/** Buscador de empresa UNIFICADO (rediseño de flujo, Fase 2): el mismo en el
 *  alta de contacto, en la ficha de contacto («Asignar empresa») y donde haga
 *  falta elegir una empresa.
 *
 *  - Filtra en vivo por nombre / CIF / NIF-IVA / dominio (`GET /api/companies?q=`).
 *  - Cada resultado dice si la empresa está en FACTUSOL (con su nº de
 *    cliente) o es solo CRM.
 *  - Una opción persistente «＋ Crear empresa nueva «…»» al final, para
 *    crearla sin salir de donde estés.
 *
 *  Sustituye al desplegable con TODAS las empresas del alta de contacto y al
 *  buscador del modal de la ficha. Combobox accesible (teclado: ↑ ↓ ⏎ Esc). */
export function CompanySearch({
  onPick,
  onCreate,
  label = "Empresa",
  placeholder = "Nombre, CIF, NIF-IVA o dominio…",
  autoFocus = false,
  initialQuery = "",
}: {
  onPick: (company: Company) => void;
  /** Con el texto escrito: quien lo use abre «Crear empresa» prerrellenada. */
  onCreate?: (name: string) => void;
  label?: string;
  placeholder?: string;
  autoFocus?: boolean;
  initialQuery?: string;
}) {
  const id = useId();
  const [query, setQuery] = useState(initialQuery);
  const [items, setItems] = useState<Company[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setItems([]);
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    const handle = window.setTimeout(() => {
      listCompanies({ q, limit: LIMIT })
        .then((page) => {
          if (!alive) return;
          setItems(page.items);
          setError(null);
        })
        .catch(() => {
          if (!alive) return;
          setItems([]);
          setError("No se pudo buscar empresas.");
        })
        .finally(() => { if (alive) setLoading(false); });
    }, DEBOUNCE_MS);
    return () => { alive = false; window.clearTimeout(handle); };
  }, [query]);

  useEffect(() => { setActive(0); }, [items.length, query]);

  // Cerrar al pulsar fuera.
  useEffect(() => {
    if (!open) return;
    function onDocDown(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, [open]);

  const q = query.trim();
  // Opciones = resultados + «crear nueva» (siempre, si hay texto y quien lo maneje).
  const canCreate = !!onCreate && q.length > 0;
  const total = items.length + (canCreate ? 1 : 0);
  const showList = open && q.length > 0;

  function pick(index: number) {
    if (index < items.length) {
      onPick(items[index]);
      setQuery("");
      setOpen(false);
      return;
    }
    if (canCreate) {
      onCreate?.(q);
      setOpen(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!showList || total === 0) {
      if (e.key === "Enter" && canCreate && items.length === 0) {
        e.preventDefault();
        onCreate?.(q);
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % total);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a - 1 + total) % total);
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(active);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="company-search" ref={box}>
      <label className="field" htmlFor={`${id}-input`}>
        <span>{label}</span>
        <input
          id={`${id}-input`}
          type="text"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={showList}
          aria-controls={`${id}-list`}
          aria-activedescendant={showList && total > 0 ? `${id}-opt-${active}` : undefined}
          autoComplete="off"
          autoFocus={autoFocus}
          value={query}
          placeholder={placeholder}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
      </label>
      {showList ? (
        <ul className="company-search-list" role="listbox" id={`${id}-list`}>
          {loading && items.length === 0 ? (
            <li className="company-search-hint" role="presentation">Buscando…</li>
          ) : null}
          {error ? <li className="company-search-hint form-error" role="presentation">{error}</li> : null}
          {items.map((c, i) => (
            <li
              key={c.id}
              id={`${id}-opt-${i}`}
              role="option"
              aria-selected={active === i}
              className={`company-search-item${active === i ? " is-active" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(i)}
              onMouseEnter={() => setActive(i)}
            >
              <span className="company-search-main">
                <strong>{c.name}</strong>
                <span className="company-search-meta">
                  {[c.tax_id || c.vat, c.country, c.domain].filter(Boolean).join(" · ") || "sin NIF"}
                  {" · "}
                  {c.factusol_company_id ? (
                    <span className="badge ok">en FACTUSOL nº {c.factusol_company_id}</span>
                  ) : (
                    <span className="badge muted">solo CRM</span>
                  )}
                </span>
              </span>
              <span className="muted small">Elegir</span>
            </li>
          ))}
          {!loading && items.length === 0 && !error ? (
            <li className="company-search-hint" role="presentation">Ninguna empresa coincide.</li>
          ) : null}
          {canCreate ? (
            <li
              id={`${id}-opt-${items.length}`}
              role="option"
              aria-selected={active === items.length}
              className={`company-search-item is-create${active === items.length ? " is-active" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(items.length)}
              onMouseEnter={() => setActive(items.length)}
            >
              ＋ Crear empresa nueva «<strong>{q}</strong>»
            </li>
          ) : null}
        </ul>
      ) : null}
      <p className="muted small company-search-help">
        Escribe para buscar por nombre, CIF, NIF-IVA o dominio.
        {onCreate ? " Si no existe, créala sin salir de aquí." : ""}
      </p>
    </div>
  );
}
