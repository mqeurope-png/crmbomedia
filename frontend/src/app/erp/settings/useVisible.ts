"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

/** El antepasado más cercano que hace scroll (`overflow-y: auto | scroll`), o
 *  `null` (la ventana) si no hay ninguno. En la aplicación el que hace scroll es
 *  `.app-shell-content` (el `body` no): sin él como `root`, el observador mira
 *  la ventana y el `rootMargin` no adelanta nada. */
function contenedorConScroll(el: Element): Element | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const { overflowY } = window.getComputedStyle(p);
    if (overflowY === "auto" || overflowY === "scroll") return p;
  }
  return null;
}

/** ¿Ha llegado ya el elemento a la ventana (o a `rootMargin` de ella)? Pasa a
 *  `true` la primera vez que se ve y ya no vuelve a `false`: sirve para pedir
 *  algo caro (p. ej. el ejemplo en vivo de una plantilla) solo cuando hace
 *  falta, y no todo de golpe al abrir la página.
 *
 *  Sin IntersectionObserver (jsdom en los tests, navegadores viejos) se da por
 *  visto desde el principio: se comporta como si no hubiera carga diferida. */
export function useVisible<T extends Element>(
  rootMargin = "200px",
): [RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [visto, setVisto] = useState(
    () => typeof window !== "undefined" && typeof window.IntersectionObserver === "undefined",
  );

  useEffect(() => {
    const el = ref.current;
    if (visto || !el || typeof IntersectionObserver === "undefined") return;
    const observador = new IntersectionObserver(
      (entradas) => {
        if (entradas.some((e) => e.isIntersecting)) {
          setVisto(true);
          observador.disconnect();
        }
      },
      { root: contenedorConScroll(el), rootMargin },
    );
    observador.observe(el);
    return () => observador.disconnect();
  }, [visto, rootMargin]);

  return [ref, visto];
}
