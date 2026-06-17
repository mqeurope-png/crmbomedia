"use client";

/**
 * Auto-logout silencioso tras `timeoutMs` sin interacción del user
 * (mousemove / mousedown / keydown / scroll / touchstart). PR-F idle
 * timeout — Bart spec 4h.
 *
 * - Reset del temporizador en cada evento (throttle a 1/seg para no
 *   spam-rerender el componente padre con setTimeout-clear ciclos).
 * - Al vencer: dispara `onTimeout` que el caller usa para borrar
 *   estado local + redirigir a /welcome.
 * - Cleanup completo en unmount: quita listeners y cancela el timer.
 */
import { useCallback, useEffect, useRef } from "react";

const ACTIVITY_EVENTS = [
  "mousemove",
  "mousedown",
  "keydown",
  "scroll",
  "touchstart",
] as const;

const THROTTLE_MS = 1000;

export function useIdleTimeout(
  timeoutMs: number,
  onTimeout: () => void,
  // Si `false` (caller no autenticado o ruta anónima) no instalamos
  // listeners — evita disparar logout en /login y /welcome.
  enabled: boolean = true,
): void {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastResetRef = useRef(0);
  const callbackRef = useRef(onTimeout);
  callbackRef.current = onTimeout;

  const reset = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      callbackRef.current();
    }, timeoutMs);
  }, [timeoutMs]);

  useEffect(() => {
    if (!enabled) {
      if (timerRef.current) clearTimeout(timerRef.current);
      return;
    }
    reset();
    function onActivity() {
      const now = Date.now();
      if (now - lastResetRef.current < THROTTLE_MS) return;
      lastResetRef.current = now;
      reset();
    }
    for (const ev of ACTIVITY_EVENTS) {
      window.addEventListener(ev, onActivity, { passive: true });
    }
    return () => {
      for (const ev of ACTIVITY_EVENTS) {
        window.removeEventListener(ev, onActivity);
      }
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [enabled, reset]);
}
