"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Side = "right" | "left";

type Options = {
  /** localStorage key for persistence. */
  storageKey: string;
  /** Default width in px when there is no stored value. */
  defaultWidth: number;
  /** Min width in px. */
  minWidth?: number;
  /** Max width in px. */
  maxWidth?: number;
  /** Which edge the handle attaches to:
   *  - `"right"` for a left-side panel (handle on its right border,
   *    drag right = grow).
   *  - `"left"` for a right-side panel (handle on its left border,
   *    drag left = grow).
   */
  side: Side;
};

/**
 * PR-Fixes-Pase-4 Bug 3. Resizable side panels with localStorage
 * persistence. Returns the current width plus an object the caller
 * spreads onto the resize handle element to wire up mousedown.
 *
 * We avoid `react-resizable-panels` because the spec asks for
 * pixel widths with explicit min/max + per-user storage keys; the
 * library works in percentages and treats the whole layout as a
 * single unit. A pair of independent panels is simpler with raw
 * mouse events.
 */
export function useResizablePanel(opts: Options) {
  const {
    storageKey,
    defaultWidth,
    minWidth = 200,
    maxWidth = 600,
    side,
  } = opts;

  const [width, setWidth] = useState<number>(defaultWidth);
  const widthRef = useRef(defaultWidth);
  const draggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(defaultWidth);

  // Read stored width on mount (SSR-safe).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(storageKey);
    if (stored) {
      const parsed = parseInt(stored, 10);
      if (Number.isFinite(parsed)) {
        const next = clamp(parsed, minWidth, maxWidth);
        widthRef.current = next;
        setWidth(next);
      }
    }
  }, [storageKey, minWidth, maxWidth]);

  const onMouseDown = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      event.preventDefault();
      draggingRef.current = true;
      startXRef.current = event.clientX;
      startWidthRef.current = widthRef.current;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [],
  );

  // Attach global listeners once (refs carry state across renders).
  useEffect(() => {
    function onMove(event: MouseEvent) {
      if (!draggingRef.current) return;
      const dx = event.clientX - startXRef.current;
      const next = clamp(
        startWidthRef.current + (side === "right" ? dx : -dx),
        minWidth,
        maxWidth,
      );
      widthRef.current = next;
      setWidth(next);
    }
    function onUp() {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      if (typeof window !== "undefined") {
        window.localStorage.setItem(
          storageKey,
          String(Math.round(widthRef.current)),
        );
      }
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [storageKey, minWidth, maxWidth, side]);

  return {
    width,
    handleProps: {
      onMouseDown,
      role: "separator" as const,
      "aria-orientation": "vertical" as const,
      tabIndex: 0,
    },
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
