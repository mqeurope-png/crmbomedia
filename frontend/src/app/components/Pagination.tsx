"use client";

import { useState } from "react";

type Props = {
  total: number;
  currentPage: number;
  pageSize: number;
  /** How many items are actually on the current page — used for
   *  the "X-Y de Z" range when the last page is short. */
  visibleCount: number;
  onChange: (page: number) => void;
};

/** Generic pagination footer used by every paginated list in the
 *  CRM. Shows "X-Y de Z" + a page-of-pages indicator + Anterior /
 *  Siguiente buttons + a small "Ir a página" jump input.
 *
 *  Pages are 1-indexed so the URL / state matches what the
 *  operator sees in the UI.
 */
export function Pagination({
  total,
  currentPage,
  pageSize,
  visibleCount,
  onChange,
}: Props) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(Math.max(1, currentPage), totalPages);
  const firstIdx = total === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const lastIdx = (safePage - 1) * pageSize + visibleCount;
  const [jump, setJump] = useState("");

  const submitJump = (e: React.FormEvent) => {
    e.preventDefault();
    const target = Number.parseInt(jump, 10);
    if (Number.isFinite(target) && target >= 1 && target <= totalPages) {
      onChange(target);
    }
    setJump("");
  };

  return (
    <div className="pagination">
      <span className="muted">
        {total === 0 ? (
          "Sin resultados"
        ) : (
          <>
            {firstIdx}-{lastIdx} de {total} · Página {safePage} / {totalPages}
          </>
        )}
      </span>
      <div className="pagination-buttons">
        <button
          type="button"
          className="button secondary small"
          onClick={() => onChange(safePage - 1)}
          disabled={safePage <= 1}
          aria-label="Página anterior"
        >
          « Anterior
        </button>
        <button
          type="button"
          className="button secondary small"
          onClick={() => onChange(safePage + 1)}
          disabled={safePage >= totalPages}
          aria-label="Página siguiente"
        >
          Siguiente »
        </button>
        {totalPages > 5 ? (
          <form
            className="pagination-jump"
            onSubmit={submitJump}
            aria-label="Ir a página"
          >
            <label className="muted small">
              Ir a:
              <input
                type="number"
                min={1}
                max={totalPages}
                value={jump}
                onChange={(e) => setJump(e.target.value)}
                placeholder={String(safePage)}
              />
            </label>
            <button type="submit" className="button secondary small">
              Ir
            </button>
          </form>
        ) : null}
      </div>
    </div>
  );
}
