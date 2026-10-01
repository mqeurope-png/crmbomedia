"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/** Tabla ancha con desplazamiento PROPIO (no de la página): el contenedor
 *  scrollea en los dos ejes, con la barra siempre visible y la altura
 *  ajustada a lo que queda de pantalla (así la barra horizontal se ve sin
 *  bajar). Dentro, la cabecera queda fija arriba y las celdas marcadas
 *  `sticky-l sticky-l-<n>` (columnas fijas a la izquierda, en orden) y
 *  `sticky-r` (columna fija a la derecha) no se mueven al desplazar; un
 *  sombreado en los bordes avisa de que hay más columnas.
 *
 *  Las posiciones `left` de las columnas fijas se miden de su cabecera
 *  (`th.sticky-l-<n>`) y se pasan como variables CSS a la tabla, así que la
 *  tabla sigue siendo UNA (sin clonar filas ni columnas). */
export function ScrollTable({
  label,
  className,
  fitViewport = true,
  children,
}: {
  /** Nombre accesible de la región desplazable. */
  label: string;
  /** Clases extra del marco (variantes por pantalla). */
  className?: string;
  /** Ajustar la altura máxima a lo que queda de pantalla (por defecto sí). */
  fitViewport?: boolean;
  children: ReactNode;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  const measure = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    // 1) Sombreado de los bordes: ¿hay columnas a la izquierda / derecha?
    const left = el.scrollLeft > 1;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    setEdges((prev) => (prev.left === left && prev.right === right ? prev : { left, right }));
    // 2) Offsets de las columnas fijas a la izquierda, acumulados en orden.
    const table = el.querySelector("table");
    if (table) {
      let acc = 0;
      for (let i = 0; i < 6; i += 1) {
        const th = table.querySelector<HTMLElement>(`thead th.sticky-l-${i}`);
        if (!th) break;
        const value = `${acc}px`;
        if (table.style.getPropertyValue(`--sl-${i}`) !== value) {
          table.style.setProperty(`--sl-${i}`, value);
        }
        acc += th.offsetWidth;
      }
    }
    // 3) Altura: hasta el borde inferior de la pantalla (medido como si la
    //    página estuviera arriba del todo), con un mínimo usable.
    if (fitViewport && typeof window !== "undefined") {
      const page = el.closest(".app-shell-content, .sat-main") as HTMLElement | null;
      const top = el.getBoundingClientRect().top + (page?.scrollTop ?? 0);
      const available = Math.round(window.innerHeight - top - 16);
      const value = `${Math.max(320, available)}px`;
      if (el.style.getPropertyValue("--scroll-table-max-h") !== value) {
        el.style.setProperty("--scroll-table-max-h", value);
      }
    }
  }, [fitViewport]);

  // Tras cada render (cambian filas / columnas visibles) y al redimensionar.
  useEffect(() => {
    measure();
  });
  useEffect(() => {
    const el = scroller.current;
    if (!el || typeof window === "undefined") return;
    window.addEventListener("resize", measure);
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => measure());
      ro.observe(el);
      const table = el.querySelector("table");
      if (table) ro.observe(table);
    }
    return () => {
      window.removeEventListener("resize", measure);
      ro?.disconnect();
    };
  }, [measure]);

  const frame = [
    "scroll-table-frame",
    edges.left ? "is-scrolled-left" : "",
    edges.right ? "has-more-right" : "",
    className ?? "",
  ].filter(Boolean).join(" ");
  return (
    <div className={frame}>
      {/* Región enfocable: con el teclado (flechas) también se desplaza. */}
      <div
        ref={scroller}
        className="scroll-table"
        role="region"
        aria-label={label}
        tabIndex={0}
        onScroll={measure}
      >
        {children}
      </div>
    </div>
  );
}
