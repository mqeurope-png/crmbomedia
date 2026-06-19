"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { listTags, type TagDetail } from "../../lib/api";

type Props = {
  value: string[];
  onChange: (next: string[]) => void;
  /**
   * PR-Fixes-Pase-5 Bug 2.
   *
   * - `"names"` (default): values are tag NAMES. Used by
   *   `action_add_tag` / `action_remove_tag` because the workflow
   *   `Contact.tags` model is CSV by name. Supports inline creation
   *   of a new tag — typing a name that doesn't exist adds it.
   * - `"ids"`: values are tag IDs. Used by the FilterBuilder /
   *   condition rules where the backend evaluator joins against
   *   `tags` by id. No inline creation (filters shouldn't side-effect
   *   the catalog); chips show the looked-up NAME for the user.
   */
  mode?: "names" | "ids";
};

/**
 * Multi-select tag picker with searchable dropdown + chips. Same UI
 * across `action_add_tag` step config and FilterBuilder rules on
 * the `tags` field — the only difference is whether values are
 * stored as names or as ids (`mode` prop).
 *
 * UX:
 *  - Trigger button at top shows "Sin tags seleccionados" or
 *    "N tags seleccionados".
 *  - Dropdown stays open while toggling multi-select checkboxes;
 *    click-outside closes.
 *  - Chips below show the full tag NAME with × per chip.
 *  - "+ Crear tag «foo»" appears only in `mode="names"`.
 */
export function WorkflowTagsPicker({
  value,
  onChange,
  mode = "names",
}: Props) {
  const idMode = mode === "ids";
  const selected = useMemo(() => normalize(value), [value]);
  const [allTags, setAllTags] = useState<TagDetail[] | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);

  // Lazy-load the tag catalog on first open. In `ids` mode we also
  // need it eagerly to resolve the names for the chip labels — we
  // load on mount in that case so chips render correctly even before
  // the dropdown is opened.
  useEffect(() => {
    if (allTags !== null) return;
    if (!open && !idMode) return;
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
  }, [open, allTags, idMode]);

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
  const tagsByKey = useMemo(() => {
    const out = new Map<string, TagDetail>();
    if (!allTags) return out;
    for (const tag of allTags) {
      out.set(idMode ? tag.id : tag.name.toLowerCase(), tag);
    }
    return out;
  }, [allTags, idMode]);
  const selectedSet = useMemo(() => {
    if (idMode) return new Set(selected);
    return new Set(selected.map((t) => t.toLowerCase()));
  }, [selected, idMode]);

  const matches = (tag: TagDetail, value: string) =>
    idMode ? tag.id === value : tag.name.toLowerCase() === value;

  const filtered = useMemo(() => {
    if (!allTags) return [];
    if (!normalizedQuery) return allTags;
    return allTags.filter((t) =>
      t.name.toLowerCase().includes(normalizedQuery),
    );
  }, [allTags, normalizedQuery]);
  const exactMatch = filtered.find((t) =>
    t.name.toLowerCase() === normalizedQuery,
  );

  const valueFor = (tag: TagDetail) => (idMode ? tag.id : tag.name);

  const toggleTag = (tag: TagDetail) => {
    const v = valueFor(tag);
    const key = idMode ? v : v.toLowerCase();
    if (selectedSet.has(key)) {
      onChange(
        selected.filter(
          (s) => !matches(tag, idMode ? s : s.toLowerCase()),
        ),
      );
    } else {
      onChange([...selected, v]);
    }
  };

  const removeTagByValue = (val: string) => {
    if (idMode) {
      onChange(selected.filter((s) => s !== val));
    } else {
      const lower = val.toLowerCase();
      onChange(selected.filter((s) => s.toLowerCase() !== lower));
    }
  };

  const handleCreate = () => {
    if (idMode) return; // Filters don't side-effect the catalog.
    if (!trimmed) return;
    if (selectedSet.has(normalizedQuery)) return;
    onChange([...selected, trimmed]);
    setQuery("");
  };

  const triggerLabel =
    selected.length === 0
      ? "Sin tags seleccionados"
      : `${selected.length} tag${selected.length === 1 ? "" : "s"} seleccionado${selected.length === 1 ? "" : "s"}`;

  // Resolves a stored value (name or id) to its display name + color
  // for the chips. In `names` mode the value IS the display name.
  const resolveChip = (val: string): { name: string; color?: string | null } => {
    if (!idMode) {
      const tag = tagsByKey.get(val.toLowerCase());
      return { name: val, color: tag?.color };
    }
    const tag = tagsByKey.get(val);
    return { name: tag?.name ?? val, color: tag?.color };
  };

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
                  if (exactMatch) toggleTag(exactMatch);
                  else if (trimmed && !idMode) handleCreate();
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
                const key = idMode ? tag.id : tag.name.toLowerCase();
                const checked = selectedSet.has(key);
                return (
                  <li key={tag.id}>
                    <label className="workflow-tags-picker-row">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleTag(tag)}
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
              {trimmed && !exactMatch && !idMode ? (
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
          {selected.map((val) => {
            const chip = resolveChip(val);
            return (
              <span
                key={val}
                className="workflow-tags-picker-chip"
                style={
                  chip.color
                    ? { background: `${chip.color}22`, borderColor: chip.color }
                    : undefined
                }
              >
                {chip.name}
                <button
                  type="button"
                  aria-label={`Quitar ${chip.name}`}
                  onClick={() => removeTagByValue(val)}
                >
                  ×
                </button>
              </span>
            );
          })}
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
    if (seen.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  return out;
}
