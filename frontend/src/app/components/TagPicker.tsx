"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
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
  const inputRef = useRef<HTMLInputElement>(null);
  // PR-Fix-Regresiones-PR237 Bug 8. La V1 subió z-index a 5000 pero
  // un ancestor del wrapper tenía `overflow:hidden` que clipaba el
  // dropdown — z-index no afecta clipping. Fix correcto: portal a
  // `document.body` con `position:fixed` y geometría calculada vs el
  // input via `getBoundingClientRect`. El dropdown sale de cualquier
  // contenedor con overflow:hidden y queda siempre visible.
  //
  // Si el input está en la mitad inferior del viewport, el dropdown
  // abre hacia arriba para no salirse de pantalla.
  const [pos, setPos] = useState<{
    top: number;
    left: number;
    width: number;
    openUpwards: boolean;
  } | null>(null);

  const recomputePos = useCallback(() => {
    const input = inputRef.current;
    if (!input) return;
    const rect = input.getBoundingClientRect();
    const viewportHeight = window.innerHeight;
    const dropdownMaxHeight = 300;
    const spaceBelow = viewportHeight - rect.bottom;
    const openUpwards =
      spaceBelow < dropdownMaxHeight && rect.top > spaceBelow;
    setPos({
      top: openUpwards ? rect.top - 4 : rect.bottom + 4,
      left: rect.left,
      width: rect.width,
      openUpwards,
    });
  }, []);

  useEffect(() => {
    if (!open || allTags !== null) return;
    listTags()
      .then((page) => setAllTags(page.items))
      .catch(() => setError("No se pudo cargar la lista de tags."));
  }, [open, allTags]);

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      const target = event.target as Node;
      // Clicks dentro del wrapper O del dropdown portal: no cerrar.
      // El dropdown vive fuera del wrapper (portal), así que también
      // miramos su data-attribute.
      if (wrapper.current?.contains(target)) return;
      const portalRoot = document.getElementById("tag-picker-portal");
      if (portalRoot?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  // Re-calcular posición al abrir y en cada scroll/resize mientras
  // está abierto. Sin esto el dropdown se queda flotando en una
  // posición vieja si el operador hace scroll de la página.
  useEffect(() => {
    if (!open) return;
    recomputePos();
    const handler = () => recomputePos();
    window.addEventListener("scroll", handler, true);
    window.addEventListener("resize", handler);
    return () => {
      window.removeEventListener("scroll", handler, true);
      window.removeEventListener("resize", handler);
    };
  }, [open, recomputePos]);

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

  const dropdown = open && pos ? (
    <ul
      className="tag-picker-list tag-picker-list--portal"
      role="listbox"
      style={{
        position: "fixed",
        top: pos.openUpwards ? undefined : pos.top,
        bottom: pos.openUpwards ? window.innerHeight - pos.top : undefined,
        left: pos.left,
        width: pos.width,
        maxHeight: 300,
        zIndex: 9999,
      }}
    >
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
  ) : null;

  return (
    <div ref={wrapper} className="tag-picker">
      <input
        ref={inputRef}
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
      {dropdown && typeof window !== "undefined"
        ? createPortal(
            <div id="tag-picker-portal">{dropdown}</div>,
            document.body,
          )
        : null}
    </div>
  );
}
