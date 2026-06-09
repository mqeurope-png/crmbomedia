"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { listTags, type TagDetail } from "../lib/api";

type Props = {
  /** Tags already attached so the picker can grey-out duplicates. */
  excludeTagIds?: string[];
  /** Caller decides what to do with the picked tag — assign by id, by name. */
  onPick: (choice: { tag_id?: string; tag_name?: string }) => void;
};

/**
 * Tiny autocomplete: lazy-loads the tag list on focus, filters as the
 * operator types, and offers a "Crear tag nueva: '…'" option when no
 * existing tag matches. Avoids pulling in a combobox library to keep
 * the bundle slim — there are at most a few hundred tags per tenant.
 */
export function TagPicker({ excludeTagIds, onPick }: Props) {
  const [allTags, setAllTags] = useState<TagDetail[] | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || allTags !== null) return;
    listTags()
      .then((page) => setAllTags(page.items))
      .catch(() => setError("No se pudo cargar la lista de tags."));
  }, [open, allTags]);

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const excluded = useMemo(() => new Set(excludeTagIds ?? []), [excludeTagIds]);
  const trimmed = query.trim();
  const normalized = trimmed.toLowerCase();
  const filtered = useMemo(() => {
    if (!allTags) return [];
    if (!normalized) return allTags.slice(0, 12);
    return allTags
      .filter((tag) => tag.name.toLowerCase().includes(normalized))
      .slice(0, 12);
  }, [allTags, normalized]);
  const exactMatch = filtered.find((t) => t.name.toLowerCase() === normalized);

  function handlePick(tag: TagDetail) {
    if (excluded.has(tag.id)) return;
    onPick({ tag_id: tag.id });
    setQuery("");
    setOpen(false);
  }

  function handleCreate() {
    if (!trimmed) return;
    onPick({ tag_name: trimmed });
    setQuery("");
    setOpen(false);
  }

  return (
    <div ref={wrapper} className="tag-picker">
      <input
        type="text"
        placeholder="+ Añadir tag"
        value={query}
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            if (exactMatch) handlePick(exactMatch);
            else if (trimmed) handleCreate();
          } else if (event.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      {open ? (
        <ul className="tag-picker-list" role="listbox">
          {error ? <li className="tag-picker-empty">{error}</li> : null}
          {!error && allTags === null ? (
            <li className="tag-picker-empty">Cargando…</li>
          ) : null}
          {filtered.map((tag) => (
            <li
              key={tag.id}
              role="option"
              aria-selected="false"
              aria-disabled={excluded.has(tag.id)}
              className={`tag-picker-row${
                excluded.has(tag.id) ? " is-disabled" : ""
              }`}
              onMouseDown={(event) => {
                event.preventDefault();
                handlePick(tag);
              }}
            >
              {tag.color ? (
                <span
                  className="tag-picker-swatch"
                  style={{ background: tag.color }}
                  aria-hidden
                />
              ) : null}
              <span>{tag.name}</span>
              {excluded.has(tag.id) ? (
                <span className="muted small">ya asignada</span>
              ) : null}
            </li>
          ))}
          {trimmed && !exactMatch ? (
            <li
              role="option"
              aria-selected="false"
              className="tag-picker-row tag-picker-create"
              onMouseDown={(event) => {
                event.preventDefault();
                handleCreate();
              }}
            >
              Crear tag nueva: <strong>&ldquo;{trimmed}&rdquo;</strong>
            </li>
          ) : null}
          {!filtered.length && !trimmed && allTags !== null ? (
            <li className="tag-picker-empty">No hay tags aún. Escribe para crear.</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
