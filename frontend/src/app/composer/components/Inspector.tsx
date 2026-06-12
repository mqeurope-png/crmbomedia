"use client";

/**
 * Inspector — dispatches to the per-type editor for the currently
 * selected block.
 *
 * Fase 2c shipped this as a meta dump; Flujo A unlocks the first
 * per-type editor (`PimpamHeroEditor`). Subsequent flows add their
 * own editors here.
 */

import { useComposerStore } from "../lib/store";
import type { Block, ComposerCatalog } from "../lib/types";
import { PimpamHeroEditor } from "./editors/PimpamHeroEditor";

export interface InspectorProps {
  catalog: ComposerCatalog;
}

export function Inspector({ catalog }: InspectorProps) {
  const selectedId = useComposerStore((s) => s.selectedId);
  const blocks = useComposerStore((s) => s.blocks);
  const lang = useComposerStore((s) => s.activeLang);

  const block = selectedId ? findBlock(blocks, selectedId) : null;

  if (!block) {
    return (
      <div className="cmp-inspector-empty">
        <p style={{ fontSize: 13, color: "var(--text-muted)" }}>
          Selecciona un bloque en el canvas para editar sus propiedades.
        </p>
      </div>
    );
  }

  return (
    <div className="cmp-inspector-content">
      <header className="cmp-inspector-header">
        <h3>{block.type}</h3>
        <span
          className="mono"
          style={{ fontSize: 10, color: "var(--text-subtle)" }}
        >
          {block.id}
        </span>
      </header>
      <div className="cmp-inspector-body">
        {renderEditor(block, catalog, lang)}
      </div>
    </div>
  );
}

function renderEditor(
  block: Block,
  catalog: ComposerCatalog,
  lang: ReturnType<typeof useComposerStore.getState>["activeLang"],
) {
  switch (block.type) {
    case "pimpam_hero":
    case "product_hero":
    case "hero":
      return <PimpamHeroEditor block={block} lang={lang} catalog={catalog} />;
    default:
      return <PlaceholderEditor block={block} />;
  }
}

function PlaceholderEditor({ block }: { block: Block }) {
  return (
    <div style={{ padding: "12px 0", fontSize: 12, color: "var(--text-muted)" }}>
      <p>Editor para <code className="mono">{block.type}</code> llega en un flujo siguiente.</p>
      <p style={{ marginTop: 8, fontSize: 11 }}>
        Por ahora solo está disponible PimpamHero. El resto (texto,
        producto, brandstrip, cta, imagen, divisor, sección, pasos,
        freebird, compuesto) ports uno por uno en los siguientes PRs.
      </p>
    </div>
  );
}

type BlocksTree = ReturnType<typeof useComposerStore.getState>["blocks"];

function findBlock(blocks: BlocksTree, id: string): Block | null {
  for (const b of blocks) {
    if (b.id === id) return b;
    if (b.type === "section" && Array.isArray(b.columns)) {
      for (const col of b.columns) {
        const found = findBlock(col.blocks ?? [], id);
        if (found) return found;
      }
    }
  }
  return null;
}
