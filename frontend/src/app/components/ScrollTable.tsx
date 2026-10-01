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
 *  tabla sigue siendo UNA (sin clonar filas ni columnas). Si las fijas no
 *  caben (pantalla estrecha: ocuparían más de PIN_MAX_SHARE del ancho), se
 *  sueltan (`no-pin`) para que el resto de columnas se pueda ver. */
const PIN_MAX_SHARE = 0.6;
/** Alto mínimo útil de la tabla (cabecera + unas filas). */
const MIN_HEIGHT = 320;

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
  const [edges, setEdges] = useState({ left: false, right: false, pinned: true });

  const measure = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    // 1) Offsets de las columnas fijas a la izquierda, acumulados en orden,
    //    y si caben (izquierda + derecha) en el ancho visible.
    const table = el.querySelector("table");
    let leftWidth = 0;
    let rightWidth = 0;
    let headHeight = 0;
    if (table) {
      for (let i = 0; i < 6; i += 1) {
        const th = table.querySelector<HTMLElement>(`thead th.sticky-l-${i}`);
        if (!th) break;
        const value = `${leftWidth}px`;
        if (table.style.getPropertyValue(`--sl-${i}`) !== value) {
          table.style.setProperty(`--sl-${i}`, value);
        }
        leftWidth += th.offsetWidth;
      }
      rightWidth = table.querySelector<HTMLElement>("thead th.sticky-r")?.offsetWidth ?? 0;
      headHeight = table.querySelector<HTMLElement>("thead")?.offsetHeight ?? 0;
    }
    const pinned = el.clientWidth === 0 || leftWidth + rightWidth <= el.clientWidth * PIN_MAX_SHARE;
    // 2) Sombreado de los bordes: ¿hay columnas a la izquierda / derecha?
    const left = el.scrollLeft > 1;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    setEdges((prev) => (
      prev.left === left && prev.right === right && prev.pinned === pinned ? prev : { left, right, pinned }
    ));
    // 3) Con el teclado, lo enfocado no queda tapado por la cabecera ni por
    //    las columnas fijas (el navegador respeta el scroll-padding).
    const pad = (v: number) => `${v}px`;
    // Ancho visible, para lo que va a lo ancho de la tabla (avisos de fila
    // con colSpan) y se queda a la vista al desplazar.
    if (table && table.style.getPropertyValue("--scroll-table-w") !== pad(el.clientWidth)) {
      table.style.setProperty("--scroll-table-w", pad(el.clientWidth));
    }
    el.style.scrollPaddingTop = pad(headHeight);
    el.style.scrollPaddingLeft = pad(pinned ? leftWidth : 0);
    el.style.scrollPaddingRight = pad(pinned ? rightWidth : 0);
    // 4) Altura: hasta el borde inferior de la pantalla (medido como si la
    //    página estuviera arriba del todo), descontando lo que la página
    //    tiene debajo de la tabla (rellenos), con un mínimo usable. Y nunca
    //    más alta que la pantalla bajo la cabecera fija de la página: si hay
    //    que bajar la página, se baja una vez y la tabla entera (con su
    //    barra) queda a la vista.
    if (fitViewport && typeof window !== "undefined") {
      const page = el.closest(".app-shell-content, .sat-main") as HTMLElement | null;
      const rect = el.getBoundingClientRect();
      const scrollTop = page?.scrollTop ?? 0;
      const pageBottom = page ? page.getBoundingClientRect().bottom : window.innerHeight;
      const top = rect.top + scrollTop;
      // Lo que va debajo de la tabla hasta el final del contenido (no el
      // hueco vacío: con pocas filas la tabla tiene que poder crecer luego).
      const main = el.closest("main");
      const below = main && page
        ? Math.max(0, main.getBoundingClientRect().bottom - rect.bottom)
          + (parseFloat(getComputedStyle(page).paddingBottom) || 0)
        : 16;
      const header = page?.querySelector<HTMLElement>(":scope > .page-header, :scope > main > .page-header");
      const cap = Math.max(160, (page?.clientHeight ?? window.innerHeight) - (header?.offsetHeight ?? 0) - below - 8);
      const available = Math.round(pageBottom - top - below);
      const value = `${Math.max(Math.min(MIN_HEIGHT, cap), Math.min(available, cap))}px`;
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
    edges.pinned ? "" : "no-pin",
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
