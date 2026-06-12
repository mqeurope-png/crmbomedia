"use client";

/**
 * BlockCard — one card per block on the canvas.
 *
 * Distilled from the 760-LOC `BlockCard` in `bomedia-v4/app-compositor.jsx`.
 * The Fase-2.1 version handles the structural surface (drag handle, type
 * label, source-derived preview text, select / duplicate / delete /
 * move-up / move-down buttons). The inline editing surface (rich text
 * popovers, hero image picker, product selectors, column-add picker)
 * lives in the per-type editors that ship in 2.2 + the Inspector.
 *
 * Drag is wired via `useSortable` from `@dnd-kit/sortable` so the
 * canvas's `SortableContext` can reorder blocks vertically. The same
 * handle behaviour as the original: the entire card is the drag
 * target, click-to-select fires on `onClick` (so it doesn't compete
 * with the drag start, which is gated by a small distance threshold
 * configured on the DndContext).
 */

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  ArrowDown,
  ArrowUp,
  Copy,
  GripVertical,
  Trash2,
  Ungroup,
} from "lucide-react";

import { getTextInLanguage } from "../lib/i18n";
import type { Block, ComposerAppState, Lang } from "../lib/types";

const BLOCK_TYPE_LABELS: Record<string, string> = {
  text: "Texto",
  text_from_library: "Texto biblioteca",
  product_single: "Producto",
  product_pair: "Par de productos",
  product_trio: "Trío de productos",
  brand_strip: "Strip de marca",
  cta: "CTA",
  saved_cta: "CTA guardado",
  image: "Imagen",
  video: "Vídeo",
  freebird: "Vídeo (Freebird)",
  pimpam_hero: "Hero Pimpam",
  pimpam_steps: "Pasos Pimpam",
  composed: "Bloque compuesto",
  section_2col: "Sección 2 columnas",
  section_3col: "Sección 3 columnas",
  divider_line: "Divisor — línea",
  divider_short: "Divisor — corto",
  divider_dots: "Divisor — puntos",
};

export interface BlockCardProps {
  block: Block;
  index: number;
  total: number;
  selected: boolean;
  onSelect: (id: string | null) => void;
  onDelete: (id: string) => void;
  onDuplicate: (id: string) => void;
  onUngroup: (id: string) => void;
  onMoveUp: (id: string) => void;
  onMoveDown: (id: string) => void;
  lang: Lang;
  appState: ComposerAppState | null;
}

function previewLabel(block: Block, lang: Lang, appState: ComposerAppState | null): string {
  if (!appState) return BLOCK_TYPE_LABELS[block.type] ?? block.type;
  switch (block.type) {
    case "text":
    case "text_from_library": {
      const text = getTextInLanguage(block, lang, appState);
      const stripped = (text ?? "").replace(/<[^>]+>/g, "").trim();
      return stripped.length > 80 ? stripped.slice(0, 80) + "…" : stripped;
    }
    case "product_single": {
      const p = appState.products.find((pp) => pp.id === block.product1);
      return p?.name ?? "Producto sin definir";
    }
    case "product_pair":
      return [block.product1, block.product2]
        .map((id) => appState.products.find((p) => p.id === id)?.name ?? "?")
        .join(" + ");
    case "product_trio":
      return [block.product1, block.product2, block.product3]
        .map((id) => appState.products.find((p) => p.id === id)?.name ?? "?")
        .join(" + ");
    case "brand_strip": {
      const b = appState.brands.find((bb) => bb.id === block.brand);
      return b?.label ?? block.brand ?? "Marca sin definir";
    }
    case "composed": {
      const c = appState.composedBlocks.find(
        (cc) => cc.id === block._sourceId,
      );
      return c?.title ?? "Bloque compuesto";
    }
    case "pimpam_hero":
    case "pimpam_steps":
    case "freebird":
    case "video": {
      const s = appState.standaloneBlocks.find((ss) => ss.id === block._sourceId);
      return s?.title ?? BLOCK_TYPE_LABELS[block.type] ?? block.type;
    }
    default:
      return BLOCK_TYPE_LABELS[block.type] ?? block.type;
  }
}

export function BlockCard({
  block,
  index,
  total,
  selected,
  onSelect,
  onDelete,
  onDuplicate,
  onUngroup,
  onMoveUp,
  onMoveDown,
  lang,
  appState,
}: BlockCardProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: block.id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const label = BLOCK_TYPE_LABELS[block.type] ?? block.type;
  const preview = previewLabel(block, lang, appState);
  const canUngroup =
    block.type === "composed" &&
    Array.isArray(block.innerBlocks) &&
    block.innerBlocks.length > 0;

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`block-card${selected ? " is-selected" : ""}`}
      data-block-type={block.type}
      onClick={() => onSelect(block.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect(block.id);
      }}
      role="button"
      tabIndex={0}
    >
      <button
        type="button"
        className="block-card-grip"
        aria-label="Arrastrar para reordenar"
        {...attributes}
        {...listeners}
        onClick={(e) => e.stopPropagation()}
      >
        <GripVertical size={14} aria-hidden />
      </button>
      <div className="block-card-body">
        <span className="block-card-type">{label}</span>
        <span className="block-card-preview">{preview}</span>
      </div>
      <div className="block-card-actions" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          className="icon-btn"
          onClick={() => onMoveUp(block.id)}
          disabled={index === 0}
          title="Subir"
          aria-label="Subir bloque"
        >
          <ArrowUp size={12} aria-hidden />
        </button>
        <button
          type="button"
          className="icon-btn"
          onClick={() => onMoveDown(block.id)}
          disabled={index === total - 1}
          title="Bajar"
          aria-label="Bajar bloque"
        >
          <ArrowDown size={12} aria-hidden />
        </button>
        <button
          type="button"
          className="icon-btn"
          onClick={() => onDuplicate(block.id)}
          title="Duplicar"
          aria-label="Duplicar bloque"
        >
          <Copy size={12} aria-hidden />
        </button>
        {canUngroup ? (
          <button
            type="button"
            className="icon-btn"
            onClick={() => onUngroup(block.id)}
            title="Desagrupar"
            aria-label="Desagrupar bloque"
          >
            <Ungroup size={12} aria-hidden />
          </button>
        ) : null}
        <button
          type="button"
          className="icon-btn danger"
          onClick={() => onDelete(block.id)}
          title="Borrar"
          aria-label="Borrar bloque"
        >
          <Trash2 size={12} aria-hidden />
        </button>
      </div>
    </div>
  );
}
