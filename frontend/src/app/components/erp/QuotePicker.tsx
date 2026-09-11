"use client";

import { useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  listFactusolQuotes,
  searchFactusolQuotes,
  type FactusolQuote,
} from "../../lib/erpApi";
import { QuotesTable } from "./QuotesTable";

const DEBOUNCE_MS = 300;
const DAYS_BACK = 365;

/** Buscador/listado de proformas FACTUSOL para el alta de pedido — el mismo
 *  listado de la ficha de empresa (`QuotesTable`) con el buscador de plantillas
 *  de C-4: sin texto, las proformas de la empresa elegida (si está vinculada);
 *  con texto, las de CUALQUIER cliente por nº, referencia o nombre. Elegir una
 *  la carga en el pedido (el padre decide qué hacer con ella). */
export function QuotePicker({
  companyId,
  onPick,
  pickLabel = "Cargar en el pedido",
  busy,
}: {
  companyId?: string | null;
  onPick: (q: FactusolQuote) => void;
  pickLabel?: string;
  busy?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [quotes, setQuotes] = useState<FactusolQuote[]>([]);
  const [loading, setLoading] = useState(false);
  const [unlinked, setUnlinked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const q = query.trim();
    const handle = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      const request: Promise<FactusolQuote[]> = q
        ? searchFactusolQuotes(q, { days_back: DAYS_BACK }).then((items) => {
          setUnlinked(false);
          return items;
        })
        : companyId
          ? listFactusolQuotes({ company_id: companyId, days_back: DAYS_BACK }).then((r) => {
            setUnlinked(r.unlinked);
            return r.unlinked ? [] : r.items;
          })
          : Promise.resolve([]);
      request
        .then((items) => { if (alive) setQuotes(items); })
        .catch((e) => {
          if (alive) setError(extractErrorMessage(e, "No se pudieron buscar las proformas."));
        })
        .finally(() => { if (alive) setLoading(false); });
    }, q ? DEBOUNCE_MS : 0);
    return () => { alive = false; window.clearTimeout(handle); };
  }, [query, companyId]);

  const emptyText = query.trim()
    ? "Ninguna proforma casa con la búsqueda."
    : companyId
      ? (unlinked
        ? "La empresa no está vinculada a FACTUSOL: busca por nº, referencia o cliente."
        : "La empresa no tiene proformas en el último año: busca por nº, referencia o cliente.")
      : "Elige una empresa, o busca por nº, referencia o cliente.";

  return (
    <div className="erp-quote-picker">
      <label className="field">
        <span>Buscar proforma</span>
        <input
          type="text"
          value={query}
          aria-label="Buscar proforma"
          placeholder="nº, referencia o cliente…"
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted small" role="status">Buscando proformas…</p>
      ) : (
        <QuotesTable
          quotes={quotes}
          showCliente
          emptyText={emptyText}
          actions={(q) => (
            <button type="button" className="button small" disabled={busy}
                    onClick={() => onPick(q)}>
              {pickLabel}
            </button>
          )}
        />
      )}
    </div>
  );
}
