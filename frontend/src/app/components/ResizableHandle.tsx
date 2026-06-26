"use client";

/**
 * PR-Backlog-3-5-7 item 7. Handle de redimensionado horizontal con
 * persistencia per-user en localStorage. Diseñado para insertarse
 * entre dos columnas de un grid CSS: el componente padre
 * lee `width` y lo aplica a la columna izquierda (vía
 * `gridTemplateColumns`); el handle dispara el drag.
 *
 * Pragmatic — sin librerías externas, sin throttle. Para grids con
 * 2-3 columnas y movimientos manuales del operador el coste de
 * dispatcher en cada mousemove es despreciable.
 *
 * Min/max evitan que la columna quede inutilizable. Si el operador
 * arrastra fuera del rango, se clampa.
 */
import { useCallback, useEffect, useRef, useState } from "react";

type UsePanelWidthOptions = {
  /** Clave única de localStorage. Recomendado prefijar con feature. */
  key: string;
  /** Ancho por defecto en px si no hay valor persistido. */
  defaultPx: number;
  /** Mínimo razonable (para que la columna no quede inutilizable). */
  minPx?: number;
  /** Máximo razonable. */
  maxPx?: number;
};

export function usePanelWidth({
  key,
  defaultPx,
  minPx = 200,
  maxPx = 800,
}: UsePanelWidthOptions): {
  width: number;
  setWidth: (next: number) => void;
  startDrag: (event: React.MouseEvent) => void;
  isDragging: boolean;
} {
  const [width, setWidthState] = useState<number>(defaultPx);
  const [isDragging, setIsDragging] = useState(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  // Hidratar desde localStorage al montar.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(key);
    if (stored) {
      const parsed = Number(stored);
      if (!Number.isNaN(parsed)) {
        setWidthState(clamp(parsed, minPx, maxPx));
      }
    }
  }, [key, minPx, maxPx]);

  const setWidth = useCallback(
    (next: number) => {
      const clamped = clamp(next, minPx, maxPx);
      setWidthState(clamped);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(key, String(clamped));
      }
    },
    [key, minPx, maxPx],
  );

  const startDrag = useCallback(
    (event: React.MouseEvent) => {
      event.preventDefault();
      startXRef.current = event.clientX;
      startWidthRef.current = width;
      setIsDragging(true);
    },
    [width],
  );

  // Mouse-move global mientras se arrastra. listeners en window
  // para que el handle no pierda focus si el cursor sale del
  // elemento.
  useEffect(() => {
    if (!isDragging) return;
    const onMove = (event: MouseEvent) => {
      const delta = event.clientX - startXRef.current;
      setWidth(startWidthRef.current + delta);
    };
    const onUp = () => setIsDragging(false);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [isDragging, setWidth]);

  return { width, setWidth, startDrag, isDragging };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * Handle visual para arrastrar entre paneles. Renderízalo entre las
 * dos columnas del grid y conéctalo al `startDrag` que devuelve
 * `usePanelWidth`. El cursor cambia a "col-resize" en hover; el
 * propio elemento ocupa 4 px de ancho para que sea agarrable.
 */
type ResizableHandleProps = {
  onMouseDown: (event: React.MouseEvent) => void;
  isDragging?: boolean;
  ariaLabel?: string;
};

export function ResizableHandle({
  onMouseDown,
  isDragging,
  ariaLabel = "Redimensionar panel",
}: ResizableHandleProps) {
  return (
    <button
      type="button"
      className={`resizable-handle${isDragging ? " is-dragging" : ""}`}
      onMouseDown={onMouseDown}
      aria-label={ariaLabel}
      title={ariaLabel}
    />
  );
}
