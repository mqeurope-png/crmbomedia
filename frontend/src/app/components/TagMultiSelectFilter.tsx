"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { listTags, type TagDetail } from "../lib/api";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { TagChips } from "./TagChips";

type Props = {
  selectedIds: string[];
  onChange: (next: string[]) => void;
  /** Optional slot below the chips — typically the any/all toggle. */
  footer?: React.ReactNode;
  placeholder?: string;
};

/**
 * Filter-mode tag picker. Unlike `<TagPicker>`:
 *
 *  - Multi-select with the current selections shown as chips above
 *    the dropdown.
 *  - No "Create new tag" affordance — the filter shouldn't side-effect
 *    the tag catalog. An operator wanting a brand-new tag goes to
 *    `/admin/tags`.
 *  - Click-outside closes the panel; Escape clears the search.
 *  - Re-clicking a selected option (or its "x" chip) deselects it.
 *
 * PR-Cg: pasa de "fetch lista completa + filter cliente" a "fetch
 * debounced 300ms + server-side q". El cliente NO descarga la base
 * completa de tags — sólo el subset que matchea la búsqueda. Las
 * selecciones ya elegidas (`selectedIds`) se renderizan como chips
 * desde un fetch separado por id para que el operador vea sus
 * elecciones aunque salgan del subset filtrado.
 */
export function TagMultiSelectFilter({
  selectedIds,
  onChange,
  footer,
  placeholder = "Buscar tag…",
}: Props) {
  const [tags, setTags] = useState<TagDetail[] | null>(null);
  // Cache local para los labels de tags ya seleccionados — el subset
  // server-side puede no contenerlos. Se hidrata bajo demanda.
  const [selectedTags, setSelectedTags] = useState<Record<string, TagDetail>>({});
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const debouncedQuery = useDebouncedValue(query, 300);

  // Fetch server-side cada vez que cambia el debouncedQuery (mientras
  // el panel está abierto). Si está cerrado, no consume rate-limit.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    listTags(debouncedQuery || undefined)
      .then((page) => {
        if (!cancelled) {
          setTags(page.items);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) setError("Tags no disponibles");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, debouncedQuery]);

  // Hidrata los labels de tags seleccionados que no aparezcan en el
  // subset actual (e.g. el operador re-abre un filtro guardado con
  // ids que ya no están en el top-N). Un único listTags() sin q hasta
  // 200 cubre el caso real; queda en cache.
  useEffect(() => {
    const missing = selectedIds.filter((id) => !(id in selectedTags));
    if (missing.length === 0) return;
    let cancelled = false;
    listTags()
      .then((page) => {
        if (cancelled) return;
        setSelectedTags((cur) => {
          const next = { ...cur };
          for (const tag of page.items) next[tag.id] = tag;
          return next;
        });
      })
      .catch(() => {
        /* swallow; chips quedan sin label hasta el próximo intento */
      });
    return () => {
      cancelled = true;
    };
  }, [selectedIds, selectedTags]);

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (!wrapper.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  useEffect(() => {
    if (open) searchInput.current?.focus();
  }, [open]);

  const selectedAsTags = useMemo(() => {
    return selectedIds
      .map((id) => selectedTags[id])
      .filter((t): t is TagDetail => Boolean(t));
  }, [selectedIds, selectedTags]);

  function toggle(tagId: string) {
    if (selectedIds.includes(tagId)) {
      onChange(selectedIds.filter((id) => id !== tagId));
    } else {
      onChange([...selectedIds, tagId]);
    }
  }

  const items = tags ?? [];

  return (
    <div ref={wrapper} className="tag-multiselect">
      <button
        type="button"
        className={`tag-multiselect-trigger${open ? " is-open" : ""}`}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        {selectedAsTags.length === 0 ? (
          <span className="muted">Filtrar por tags</span>
        ) : (
          <TagChips
            tags={selectedAsTags}
            size="dense"
            onRemove={(id) => toggle(id)}
          />
        )}
        <span className="tag-multiselect-caret" aria-hidden>
          ▾
        </span>
      </button>

      {open ? (
        <div className="tag-multiselect-panel" role="listbox">
          <input
            ref={searchInput}
            type="search"
            className="tag-multiselect-search"
            value={query}
            placeholder={placeholder}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setQuery("");
              }
            }}
          />

          {error ? (
            <p className="tag-multiselect-empty">{error}</p>
          ) : tags === null || loading ? (
            <p className="tag-multiselect-empty">Cargando…</p>
          ) : items.length === 0 ? (
            <p className="tag-multiselect-empty">Sin resultados.</p>
          ) : (
            <ul className="tag-multiselect-list">
              {items.map((tag) => {
                const isSelected = selectedIds.includes(tag.id);
                return (
                  <li
                    key={tag.id}
                    role="option"
                    aria-selected={isSelected}
                    className={`tag-multiselect-row${isSelected ? " is-selected" : ""}`}
                    onMouseDown={(event) => {
                      event.preventDefault();
                      toggle(tag.id);
                    }}
                  >
                    <span
                      className="tag-picker-swatch"
                      style={{ background: tag.color || "#cdd5e1" }}
                      aria-hidden
                    />
                    <span className="tag-multiselect-name">{tag.name}</span>
                    {isSelected ? (
                      <span className="tag-multiselect-check" aria-hidden>
                        ✓
                      </span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}

          {footer ? <div className="tag-multiselect-footer">{footer}</div> : null}
        </div>
      ) : null}
    </div>
  );
}
