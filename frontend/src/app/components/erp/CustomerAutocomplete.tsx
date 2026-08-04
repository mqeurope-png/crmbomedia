"use client";

import { useEffect, useState } from "react";
import { listCompanies, type Company } from "../../lib/companiesApi";
import {
  searchFactusolCustomers,
  type FactusolCustomer,
} from "../../lib/erpApi";

const DEBOUNCE_MS = 300;

/** Cómo se eligió el cliente: uno de FACTUSOL (con o sin vínculo CRM) o una
 *  empresa del CRM que aún no está en FACTUSOL. */
export type CustomerChoice =
  | { kind: "factusol"; customer: FactusolCustomer }
  | { kind: "crm"; company: Company };

/** Busca el cliente en FACTUSOL y en el CRM a la vez (C-3).
 *
 *  El orden importa: **primero FACTUSOL**, porque es la fuente contable. Un
 *  resultado de FACTUSOL ya vinculado al CRM se puede usar tal cual; uno sin
 *  vincular se vincula al elegirlo. Las empresas que solo están en el CRM se
 *  muestran aparte, marcadas para crearlas en FACTUSOL. */
export function CustomerAutocomplete({
  onPick,
  autoFocus,
}: {
  onPick: (choice: CustomerChoice) => void;
  autoFocus?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [factusol, setFactusol] = useState<FactusolCustomer[]>([]);
  const [crmOnly, setCrmOnly] = useState<Company[]>([]);
  const [loading, setLoading] = useState(false);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setFactusol([]);
      setCrmOnly([]);
      return;
    }
    let alive = true;
    setLoading(true);
    const handle = window.setTimeout(() => {
      // Un NIF se busca exacto; cualquier otra cosa, por nombre.
      const by = /^[A-Za-z]?\d{7,8}[A-Za-z]?$/.test(q) ? "nif" : "name";
      Promise.allSettled([
        searchFactusolCustomers(q, by),
        listCompanies({ q, limit: 10 }),
      ]).then(([fac, crm]) => {
        if (!alive) return;
        const facItems = fac.status === "fulfilled" ? fac.value : [];
        const crmItems = crm.status === "fulfilled" ? crm.value.items : [];
        setFactusol(facItems);
        // Solo las que aún no tienen código FACTUSOL (las demás ya salen arriba).
        setCrmOnly(crmItems.filter((c) => !c.factusol_company_id));
        setLoading(false);
        setTouched(true);
      });
    }, DEBOUNCE_MS);
    return () => { alive = false; window.clearTimeout(handle); };
  }, [query]);

  const empty = touched && !loading && factusol.length === 0 && crmOnly.length === 0;

  return (
    <div className="erp-customer-autocomplete">
      <label className="field">
        <span>Cliente (NIF o nombre)</span>
        <input
          type="text"
          value={query}
          autoFocus={autoFocus}
          placeholder="B64113590 · Laboratorios Porta…"
          aria-label="Buscar cliente"
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>

      {loading ? <p className="muted small">Buscando…</p> : null}

      {factusol.length > 0 ? (
        <section aria-label="En FACTUSOL">
          <h4 className="erp-ac-heading">En FACTUSOL</h4>
          <ul className="erp-ac-list">
            {factusol.map((c) => (
              <li key={c.codcli ?? c.cifcli}>
                <button type="button" className="erp-ac-item"
                        onClick={() => onPick({ kind: "factusol", customer: c })}>
                  <span className="erp-ac-name">{c.nomcli}</span>
                  <span className="muted small">
                    {c.cifcli} · nº {c.codcli}
                  </span>
                  {c.crm_link ? (
                    <span className="badge ok">✓ En CRM</span>
                  ) : (
                    <span className="badge warn">Vincular a CRM</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {crmOnly.length > 0 ? (
        <section aria-label="Solo en CRM">
          <h4 className="erp-ac-heading">Solo en CRM (sin código FACTUSOL)</h4>
          <ul className="erp-ac-list">
            {crmOnly.map((c) => (
              <li key={c.id}>
                <button type="button" className="erp-ac-item"
                        onClick={() => onPick({ kind: "crm", company: c })}>
                  <span className="erp-ac-name">{c.name}</span>
                  <span className="muted small">{c.tax_id ?? "sin NIF"}</span>
                  <span className="badge active">Crear en FACTUSOL</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {empty ? (
        <p className="muted small">
          Sin resultados.{" "}
          <a href="/contacts/new" target="_blank" rel="noreferrer">
            ¿No existe? Créalo primero en Contactos
          </a>
        </p>
      ) : null}
    </div>
  );
}
