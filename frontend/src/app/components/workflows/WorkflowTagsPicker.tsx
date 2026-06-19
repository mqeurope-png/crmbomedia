"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { listTags, type TagDetail } from "../../lib/api";

type Props = {
  value: string[];
  onChange: (next: string[]) => void;
};

/**
 * PR-Fixes-Pase-4 Bug 2.
 *
 * Multi-select tag picker for the workflow editor `action_add_tag` /
 * `action_remove_tag` panels. Differences from the global
 * `<TagMultiSelectFilter>`:
 *
 *  - Works with tag NAMES (`string[]`) instead of IDs because the
 *    workflow `Contact.tags` model is CSV by name, not relational.
 *  - Allows creating a new tag inline (typing a name that doesn't
 *    exist + "+ crear" affordance) — workflow operators add tags
 *    while designing the flow without bouncing to `/admin/tags`.
 *  - Chips below the dropdown show the full tag name with `×` per
 *    chip; the trigger of the closed dropdown shows
 *    `N tags seleccionados` or the placeholder `Sin tags
 *    seleccionados`.
 *  - The dropdown stays open while toggling multi-select checkboxes.
 *    Click-outside closes.
 */
export function WorkflowTagsPicker({ value, onChange }: Props) {
  const selected = useMemo(() => normalize(value), [value]);
  const [allTags, setAllTags] = useState<TagDetail[] | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);

  // Lazy-load the tag catalog on first open.
  useEffect(() => {
    if (!open || allTags !== null) return;
    let cancelled = false;
    setLoading(true);
    listTags()
      .then((page) => {
        if (!cancelled) {
          setAllTags(page.items);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) setError("No se pudo cargar la lista de tags.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, allTags]);

  // Click outside closes the panel.
  useEffect(() => {
    function handle(event: MouseEvent) {
      if (!wrapper.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    if (!open) return;
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [open]);

  const trimmed = query.trim();
  const normalizedQuery = trimmed.toLowerCase();
  const selectedSet = useMemo(
    () => new Set(selected.map((t) => t.toLowerCase())),
    [selected],
  );
  const filtered = useMemo(() => {
    if (!allTags) return [];
    if (!normalizedQuery) return allTags;
    return allTags.filter((t) =>
      t.name.toLowerCase().includes(normalizedQuery),
    );
  }, [allTags, normalizedQuery]);
  const exactMatch = filtered.find(
    (t) => t.name.toLowerCase() === normalizedQuery,
  );

  const toggleTag = (name: string) => {
    const lower = name.toLowerCase();
    if (selectedSet.has(lower)) {
      onChange(selected.filter((t) => t.toLowerCase() !== lower));
    } else {
      onChange([...selected, name]);
    }
  };

  const removeTag = (name: string) => {
    const lower = name.toLowerCase();
    onChange(selected.filter((t) => t.toLowerCase() !== lower));
  };

  const handleCreate = () => {
    if (!trimmed) return;
    if (selectedSet.has(normalizedQuery)) return;
    onChange([...selected, trimmed]);
    setQuery("");
  };

  const triggerLabel =
    selected.length === 0
      ? "Sin tags seleccionados"
      : `${selected.length} tag${selected.length === 1 ? "" : "s"} seleccionado${selected.length === 1 ? "" : "s"}`;

  return (
    <div ref={wrapper} className="workflow-tags-picker">
      <button
        type="button"
        className="workflow-tags-picker-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {triggerLabel}
        <span aria-hidden> ▾</span>
      </button>

      {open ? (
        <div className="workflow-tags-picker-panel" role="dialog">
          <div className="workflow-tags-picker-search">
            <input
              type="search"
              autoFocus
              placeholder="Buscar tag…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setQuery("");
                  setOpen(false);
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  if (exactMatch) toggleTag(exactMatch.name);
                  else if (trimmed) handleCreate();
                }
              }}
            />
          </div>
          {loading ? (
            <p className="muted small">Cargando…</p>
          ) : error ? (
            <p className="form-error small">{error}</p>
          ) : (
            <ul className="workflow-tags-picker-list" role="listbox">
              {filtered.length === 0 && !trimmed ? (
                <li className="muted small">No hay tags todavía.</li>
              ) : null}
              {filtered.map((tag) => {
                const checked = selectedSet.has(tag.name.toLowerCase());
                return (
                  <li key={tag.id}>
                    <label className="workflow-tags-picker-row">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleTag(tag.name)}
                      />
                      {tag.color ? (
                        <span
                          className="workflow-tags-picker-swatch"
                          style={{ background: tag.color }}
                          aria-hidden
                        />
                      ) : null}
                      <span>{tag.name}</span>
                    </label>
                  </li>
                );
              })}
              {trimmed && !exactMatch ? (
                <li>
                  <button
                    type="button"
                    className="workflow-tags-picker-create"
                    onClick={handleCreate}
                  >
                    + Crear tag «{trimmed}»
                  </button>
                </li>
              ) : null}
            </ul>
          )}
        </div>
      ) : null}

      {selected.length > 0 ? (
        <div className="workflow-tags-picker-chips">
          {selected.map((name) => (
            <span key={name} className="workflow-tags-picker-chip">
              {name}
              <button
                type="button"
                aria-label={`Quitar ${name}`}
                onClick={() => removeTag(name)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function normalize(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const v of value) {
    const s = String(v ?? "").trim();
    if (!s) continue;
    const lower = s.toLowerCase();
    if (seen.has(lower)) continue;
    seen.add(lower);
    out.push(s);
  }
  return out;
}
