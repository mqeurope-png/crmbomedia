"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** Menú «⋯»: ahí viven las acciones que no son la principal, sin llenar la
 *  pantalla de botones. Lo usan la tarjeta de pedido de la bandeja y la
 *  cabecera de la ficha. Se cierra al elegir, al pulsar fuera y con Escape.
 *  Los hijos son botones / enlaces / campos tal cual.
 *
 *  `floating`: dentro de una tabla con scroll propio (ScrollTable) un menú
 *  absoluto quedaría recortado por el contenedor; flotante se coloca fijo
 *  junto a su botón (debajo, o encima si no cabe) y se cierra al desplazar o
 *  redimensionar, para no quedarse lejos de su fila. */
export function ActionsMenu({
  label,
  children,
  floating = false,
}: {
  label: string;
  children: ReactNode;
  floating?: boolean;
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

  // Flotante: posición fija medida del botón antes de pintar (sin estado: el
  // estilo va directo al nodo, que React no controla).
  useLayoutEffect(() => {
    if (!open || !floating) return;
    const t = trigger.current;
    const p = pop.current;
    if (!t || !p) return;
    const r = t.getBoundingClientRect();
    const h = p.offsetHeight;
    const below = r.bottom + 4;
    const above = r.top - 4 - h;
    const top = below + h > window.innerHeight - 8 && above >= 8 ? above : below;
    p.style.top = `${Math.round(top)}px`;
    p.style.right = `${Math.max(8, Math.round(window.innerWidth - r.right))}px`;
  }, [open, floating]);

  useEffect(() => {
    if (!open || !floating) return;
    function close(e: Event) {
      // Desplazar DENTRO del menú (lista larga) no lo cierra.
      if (e.type === "scroll" && pop.current && e.target instanceof Node && pop.current.contains(e.target)) return;
      setOpen(false);
    }
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open, floating]);

  return (
    <div className={`erp-flow-menu${open ? " is-open" : ""}`} ref={box}>
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
          ref={pop}
          className={`erp-flow-menu-pop${floating ? " is-floating" : ""}`}
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
