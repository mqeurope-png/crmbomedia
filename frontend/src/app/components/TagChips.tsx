"use client";

import type { Tag } from "../lib/api";

type Props = {
  tags: Tag[];
  onRemove?: (tagId: string) => void;
  /** Visual density: `dense` for table cells, `regular` for cards. */
  size?: "dense" | "regular";
};

const FALLBACK_BG = "#eef4ff";
const FALLBACK_FG = "#1f4ed8";

function _contrastColor(hex: string | null | undefined): string {
  if (!hex || !/^#[0-9a-f]{6}$/i.test(hex)) return FALLBACK_FG;
  const r = parseInt(hex.substring(1, 3), 16);
  const g = parseInt(hex.substring(3, 5), 16);
  const b = parseInt(hex.substring(5, 7), 16);
  // Luma per ITU-R BT.601. Bright backgrounds → black text, dark → white.
  return (0.299 * r + 0.587 * g + 0.114 * b) > 160 ? "#111" : "#fff";
}

export function TagChips({ tags, onRemove, size = "regular" }: Props) {
  if (!tags.length) return null;
  return (
    <ul className={`tag-chip-list tag-chip-list--${size}`}>
      {tags.map((tag) => {
        const bg = tag.color || FALLBACK_BG;
        const fg = _contrastColor(tag.color);
        return (
          <li key={tag.id} className="tag-chip" style={{ background: bg, color: fg }}>
            <span>{tag.name}</span>
            {onRemove ? (
              <button
                type="button"
                aria-label={`Quitar tag ${tag.name}`}
                onClick={() => onRemove(tag.id)}
                className="tag-chip-remove"
                style={{ color: fg }}
              >
                ×
              </button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
