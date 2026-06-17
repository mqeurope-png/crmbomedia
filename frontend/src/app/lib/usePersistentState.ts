"use client";

/**
 * Hook de estado persistido en localStorage. PR-E3.
 *
 * Usado por los widgets del dashboard (selección de período) y por la
 * persistencia de vistas de las listas (/contacts, /companies, …).
 * SSR-safe: el primer render devuelve `initial`; en el efecto de
 * montaje restaura el valor guardado si existe, para no romper la
 * hidratación de Next.js.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export function usePersistentState<T>(
  key: string,
  initial: T,
): [T, (next: T) => void, boolean] {
  const [value, setValue] = useState<T>(initial);
  const [hydrated, setHydrated] = useState(false);
  const keyRef = useRef(key);
  keyRef.current = key;

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw !== null) {
        setValue(JSON.parse(raw) as T);
      }
    } catch {
      // localStorage no disponible o JSON corrupto → nos quedamos con
      // `initial`. No es crítico.
    } finally {
      setHydrated(true);
    }
    // Sólo en cambios de `key` (cambio de pantalla/widget).
  }, [key]);

  const update = useCallback((next: T) => {
    setValue(next);
    try {
      window.localStorage.setItem(keyRef.current, JSON.stringify(next));
    } catch {
      // persistencia best-effort
    }
  }, []);

  return [value, update, hydrated];
}
