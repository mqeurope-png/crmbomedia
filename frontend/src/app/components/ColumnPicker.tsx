"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";

export type PickerColumn = {
  key: string;
  label: string;
  /** No se puede ocultar (p. ej. el nº de pedido, que enlaza a la ficha). */
  locked?: boolean;
};

/** «Columnas»: qué columnas de una tabla se ven. El orden es el de la tabla
 *  (fijo); solo se elige mostrar / ocultar. Quien lo usa guarda la elección
 *  (por usuario, en el navegador). No afecta a exportaciones. */
export function ColumnPicker({
  columns,
  hidden,
  onChange,
  label = "Columnas",
}: {
  columns: PickerColumn[];
  hidden: ReadonlySet<string>;
  onChange: (hidden: Set<string>) => void;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Si a la derecha del botón no cabe (la página recorta lo que se sale por
  // los lados), el panel se alinea al borde derecho del botón. El estilo va
  // directo al nodo (React no lo controla), antes de pintar.
  useLayoutEffect(() => {
    const p = pop.current;
    if (!open || !p || !box.current) return;
    const page = box.current.closest("main");
    const limit = Math.min(window.innerWidth, page ? page.getBoundingClientRect().right : window.innerWidth) - 4;
    if (p.getBoundingClientRect().right > limit) {
      p.style.left = "auto";
      p.style.right = "0";
    }
  }, [open]);

  const visibles = columns.filter((c) => !hidden.has(c.key)).length;

  function toggle(key: string) {
    const next = new Set(hidden);
    if (next.has(key)) next.delete(key); else next.add(key);
    onChange(next);
  }

  return (
    <div className="column-picker" ref={box}>
      <button
        ref={trigger}
        type="button"
        className="button small secondary"
        aria-haspopup="true"
        aria-expanded={open}
        title="Elige qué columnas se ven en la tabla (no cambia el Excel ni la hoja de Drive)"
        onClick={() => setOpen((v) => !v)}
      >
        {label}{hidden.size > 0 ? ` (${visibles}/${columns.length})` : ""}
      </button>
      {open ? (
        <div ref={pop} className="column-picker-pop" role="group" aria-label="Columnas visibles">
          {columns.map((c) => (
            <label key={c.key} className="column-picker-item">
              <input
                type="checkbox"
                aria-label={`Mostrar columna ${c.label}`}
                checked={!hidden.has(c.key)}
                disabled={c.locked}
                onChange={() => toggle(c.key)}
              />{" "}
              {c.label}
            </label>
          ))}
          <button
            type="button"
            className="button small secondary"
            disabled={hidden.size === 0}
            onClick={() => onChange(new Set())}
          >
            Mostrar todas
          </button>
        </div>
      ) : null}
    </div>
  );
}

/** Columnas ocultas guardadas para `storageKey` (lista de claves). Tolera un
 *  navegador sin localStorage o un valor corrupto (todas visibles). */
export function readHiddenColumns(storageKey: string, known: string[]): Set<string> {
  try {
    const raw = window.localStorage.getItem(storageKey);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((k): k is string => typeof k === "string" && known.includes(k)));
  } catch {
    return new Set();
  }
}

export function storeHiddenColumns(storageKey: string, hidden: ReadonlySet<string>): void {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify([...hidden]));
  } catch {
    // Sin almacenamiento (modo privado estricto): la elección vale para esta visita.
  }
}
