"use client";

/**
 * PR-Consolidado — Star Rating.
 *
 * Componente reusable de 5 estrellas. Réplica visual del "Star Value"
 * nativo de AgileCRM. Se usa en:
 *   - Lista de contactos (columna "Estrellas" toggleable).
 *   - Ficha del contacto (cabecera).
 *   - Modal Editar contacto (fila dedicada).
 *
 * Modo editable: click sobre estrella N llama `onChange(N)`. Click
 * sobre la estrella ya marcada (== value) llama `onChange(0)` —
 * desmarca todo. Hover muestra preview sin commit.
 *
 * Modo read-only: solo renderiza, sin handlers.
 */
import { useState } from "react";

export type StarRatingSize = "sm" | "md" | "lg";

type Props = {
  value: number;
  editable?: boolean;
  onChange?: (value: number) => void;
  size?: StarRatingSize;
  /** ARIA label para accesibilidad. */
  ariaLabel?: string;
  /** Desactiva interacciones (loading state durante un PATCH). */
  disabled?: boolean;
};

const SIZE_PX: Record<StarRatingSize, number> = {
  sm: 14,
  md: 18,
  lg: 24,
};

export function StarRating({
  value,
  editable = false,
  onChange,
  size = "md",
  ariaLabel,
  disabled = false,
}: Readonly<Props>) {
  const [hover, setHover] = useState<number | null>(null);
  const normalized = Math.max(0, Math.min(5, Math.round(value || 0)));
  const px = SIZE_PX[size];
  const display = hover ?? normalized;
  const interactive = editable && !disabled && onChange != null;

  function handleClick(starIndex: number) {
    if (!interactive) return;
    // Click sobre la estrella ya marcada == desmarcar todo.
    const next = starIndex === normalized ? 0 : starIndex;
    onChange!(next);
  }

  return (
    <span
      className="star-rating"
      role={editable ? "radiogroup" : "img"}
      aria-label={ariaLabel ?? `Valoración: ${normalized} de 5 estrellas`}
      onMouseLeave={() => setHover(null)}
      data-testid="star-rating"
      data-value={normalized}
      data-editable={editable ? "true" : "false"}
    >
      {[1, 2, 3, 4, 5].map((i) => {
        const filled = i <= display;
        return (
          <button
            key={i}
            type="button"
            disabled={!interactive}
            className={`star-rating-icon${filled ? " is-filled" : ""}`}
            data-testid={`star-${i}`}
            data-filled={filled ? "true" : "false"}
            onMouseEnter={() => interactive && setHover(i)}
            onClick={(e) => {
              e.stopPropagation();
              handleClick(i);
            }}
            aria-label={`${i} ${i === 1 ? "estrella" : "estrellas"}`}
            aria-pressed={interactive ? i <= normalized : undefined}
            style={{
              cursor: interactive ? "pointer" : "default",
              background: "none",
              border: 0,
              padding: 0,
              lineHeight: 0,
              color: filled ? "#f5b301" : "#cfcfcf",
              fontSize: `${px}px`,
            }}
          >
            {/* SVG star — relleno depende de `filled`. */}
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              width={px}
              height={px}
              fill={filled ? "currentColor" : "none"}
              stroke="currentColor"
              strokeWidth={1.5}
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <polygon points="12 2 15 9 22 9.3 17 14 18.5 21.5 12 17.7 5.5 21.5 7 14 2 9.3 9 9 12 2" />
            </svg>
          </button>
        );
      })}
    </span>
  );
}
