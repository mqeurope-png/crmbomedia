"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { saveDraft } from "./composerApi";

export type AutosaveStatus = "idle" | "saving" | "saved" | "error";

/** Persist the canvas state to `/api/composer/drafts` after the user
 * stops mutating it for `delayMs` (default 5s).
 *
 * The hook is intentionally minimal in Fase 1: the canvas editor
 * doesn't exist yet, so the only call site is the placeholder canvas
 * page wiring its own draft + a save button. Fase 2 will replace the
 * manual button with a useEffect that triggers `flush` on every
 * state change.
 */
export function useDraftAutosave(delayMs = 5000) {
  const [status, setStatus] = useState<AutosaveStatus>("idle");
  const [lastError, setLastError] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);
  const pendingRef = useRef<Record<string, unknown> | null>(null);

  const flush = useCallback(async () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    const payload = pendingRef.current;
    if (payload === null) return;
    pendingRef.current = null;
    setStatus("saving");
    try {
      await saveDraft(payload);
      setStatus("saved");
      setLastError(null);
    } catch (err) {
      setStatus("error");
      setLastError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const schedule = useCallback(
    (state: Record<string, unknown>) => {
      pendingRef.current = state;
      setStatus("saving");
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        void flush();
      }, delayMs);
    },
    [delayMs, flush],
  );

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, []);

  return { status, lastError, schedule, flush };
}
