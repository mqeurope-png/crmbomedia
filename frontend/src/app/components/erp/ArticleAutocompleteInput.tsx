"use client";

import { useEffect, useRef, useState } from "react";
import { searchFactusolArticles, type FactusolArticle } from "../../lib/erpApi";

const DEBOUNCE_MS = 300;
const MIN_CHARS = 2;
/** En el área de texto (descripción de proforma) solo se busca artículo
 *  mientras parece un nombre de artículo: un texto de varias líneas o largo es
 *  una descripción escrita, no una búsqueda (y elegir una sugerencia la
 *  sustituiría entera). */
const MULTILINE_SEARCH_MAX = 80;
/** Caracteres por renglón para estimar el alto del área de texto. */
const CHARS_PER_ROW = 60;

/** Input de texto con autocomplete contra el catálogo F_ART (C-4-fix2).
 *
 *  Se usa en el modal de proforma y en las líneas del pedido manual, que antes
 *  eran campos libres sin acceso al catálogo. Escribir sigue siendo libre: el
 *  autocomplete sugiere, no obliga — hay líneas que no son artículos (mano de
 *  obra, portes, reparaciones).
 *
 *  `enabled=false` lo deja como un input normal: en el pedido manual no hay
 *  contexto para buscar hasta que se elige una empresa vinculada a FACTUSOL. */
export function ArticleAutocompleteInput({
  value,
  onChange,
  onPick,
  enabled = true,
  ariaLabel,
  placeholder,
  multiline = false,
}: {
  value: string;
  onChange: (v: string) => void;
  onPick: (article: FactusolArticle) => void;
  enabled?: boolean;
  ariaLabel: string;
  placeholder?: string;
  /** Área de texto en vez de input: la descripción de una línea de proforma
   *  admite saltos de línea (FACTUSOL los guarda en `DESLPS`) y un `<input>`
   *  los borra en cuanto se toca el campo. */
  multiline?: boolean;
}) {
  const [hits, setHits] = useState<FactusolArticle[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  // Evita que la escritura provocada por elegir un artículo relance la búsqueda.
  const skipNext = useRef(false);

  const searchable = !multiline
    || (!value.includes("\n") && value.length <= MULTILINE_SEARCH_MAX);
  useEffect(() => {
    if (!enabled || !searchable || value.trim().length < MIN_CHARS) {
      setHits([]);
      setOpen(false);
      return;
    }
    if (skipNext.current) {
      skipNext.current = false;
      return;
    }
    let alive = true;
    setLoading(true);
    const handle = window.setTimeout(() => {
      searchFactusolArticles(value.trim())
        .then((items) => {
          if (!alive) return;
          setHits(items);
          setOpen(items.length > 0);
        })
        .catch(() => { if (alive) setHits([]); })
        .finally(() => { if (alive) setLoading(false); });
    }, DEBOUNCE_MS);
    return () => { alive = false; window.clearTimeout(handle); };
  }, [enabled, searchable, value]);

  function pick(article: FactusolArticle) {
    skipNext.current = true;
    setOpen(false);
    setHits([]);
    onPick(article);
  }

  return (
    <div className="erp-article-ac">
      {multiline ? (
        <textarea
          value={value}
          aria-label={ariaLabel}
          placeholder={placeholder}
          autoComplete="off"
          // Crece con el texto (hasta 8 renglones; luego, barra de desplazamiento).
          rows={Math.min(8, Math.max(1, value.split("\n").reduce(
            (n, line) => n + Math.max(1, Math.ceil(line.length / CHARS_PER_ROW)), 0,
          )))}
          onChange={(e) => onChange(e.target.value)}
          onBlur={() => window.setTimeout(() => setOpen(false), 150)}
          onFocus={() => setOpen(hits.length > 0)}
        />
      ) : (
        <input
          type="text"
          value={value}
          aria-label={ariaLabel}
          placeholder={placeholder}
          autoComplete="off"
          onChange={(e) => onChange(e.target.value)}
          // El blur se retrasa: si no, el input se cierra antes de que el clic
          // en la sugerencia llegue a dispararse.
          onBlur={() => window.setTimeout(() => setOpen(false), 150)}
          onFocus={() => setOpen(hits.length > 0)}
        />
      )}
      {enabled && open ? (
        <ul className="erp-article-ac-list" role="listbox"
            aria-label={`Artículos para ${ariaLabel}`}>
          {loading ? <li className="muted small">Buscando…</li> : null}
          {hits.map((a) => (
            <li key={a.codart ?? a.sku ?? a.descripcion}>
              <button type="button" className="erp-article-hit"
                      // onMouseDown, no onClick: se dispara antes del blur.
                      onMouseDown={(e) => { e.preventDefault(); pick(a); }}>
                <span className="erp-article-sku">{a.sku ?? a.codart}</span>
                <span className="erp-article-desc">{a.descripcion}</span>
                <span className="erp-article-price">
                  {a.precio_venta ? `${a.precio_venta.toFixed(2)} €` : "—"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
