"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { listTags, type TagDetail } from "../lib/api";
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
 */
export function TagMultiSelectFilter({
  selectedIds,
  onChange,
  footer,
  placeholder = "Buscar tag…",
}: Props) {
  const [tags, setTags] = useState<TagDetail[] | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open || tags !== null) return;
    listTags()
      .then((page) => setTags(page.items))
      .catch(() => setError("Tags no disponibles"));
  }, [open, tags]);

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

  const selected = useMemo(() => {
    if (!tags) return [] as TagDetail[];
    const idSet = new Set(selectedIds);
    return tags.filter((tag) => idSet.has(tag.id));
  }, [tags, selectedIds]);

  const filtered = useMemo(() => {
    if (!tags) return [] as TagDetail[];
    const normalized = query.trim().toLowerCase();
    if (!normalized) return tags.slice(0, 80);
    return tags.filter((tag) =>
      tag.name.toLowerCase().includes(normalized),
    );
  }, [tags, query]);

  function toggle(tagId: string) {
    if (selectedIds.includes(tagId)) {
      onChange(selectedIds.filter((id) => id !== tagId));
    } else {
      onChange([...selectedIds, tagId]);
    }
  }

  return (
    <div ref={wrapper} className="tag-multiselect">
      <button
        type="button"
        className={`tag-multiselect-trigger${open ? " is-open" : ""}`}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        {selected.length === 0 ? (
          <span className="muted">Filtrar por tags</span>
        ) : (
          <TagChips
            tags={selected}
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
          ) : tags === null ? (
            <p className="tag-multiselect-empty">Cargando…</p>
          ) : filtered.length === 0 ? (
            <p className="tag-multiselect-empty">Sin resultados.</p>
          ) : (
            <ul className="tag-multiselect-list">
              {filtered.map((tag) => {
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
