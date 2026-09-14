"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/** Menú «⋯»: ahí viven las acciones que no son la principal, sin llenar la
 *  pantalla de botones. Lo usan la tarjeta de pedido de la bandeja y la
 *  cabecera de la ficha. Se cierra al elegir, al pulsar fuera y con Escape.
 *  Los hijos son botones / enlaces / campos tal cual. */
export function ActionsMenu({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      // Escape sobre un campo del menú (select, input) cierra el campo, no el menú.
      const el = e.target as HTMLElement | null;
      if (el && box.current?.contains(el) && el.closest("select, input, textarea")) return;
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

  return (
    <div className="erp-flow-menu" ref={box}>
      <button
        ref={trigger}
        type="button"
        className="button small secondary"
        aria-label={label}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        ⋯
      </button>
      {open ? (
        <div
          className="erp-flow-menu-pop"
          // Un botón o enlace cierra el menú; un campo (select, input) no.
          onClick={(e) => {
            const el = e.target as HTMLElement;
            if (el.closest("button, a")) setOpen(false);
          }}
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}
