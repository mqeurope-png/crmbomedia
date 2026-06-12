"use client";

/**
 * Inspector — always-visible column to the right of the canvas.
 *
 * Fase 2c ships the structural surface (selection-aware header, scroll
 * container, empty + raw-meta states). The per-type editors
 * (TextEditor / ProductSingleEditor / HeroEditor / etc.) port into
 * this component in sub-PR 2d alongside the Inspector primitives
 * (Field / Toggle / Section) from `bomedia-v4/app-inspector.jsx`.
 */

import { useComposerStore } from "../lib/store";
import type { ComposerCatalog } from "../lib/types";

export interface InspectorProps {
  catalog: ComposerCatalog;
}

export function Inspector({ catalog }: InspectorProps) {
  const selectedId = useComposerStore((s) => s.selectedId);
  const blocks = useComposerStore((s) => s.blocks);

  const block = selectedId
    ? findBlock(blocks, selectedId)
    : null;

  if (!block) {
    return (
      <aside className="cmp-inspector-panel">
        <header className="cmp-inspector-header">
          <h3>Inspector</h3>
        </header>
        <div className="cmp-inspector-empty">
          <p style={{ fontSize: 13, color: "var(--text-muted)" }}>
            Selecciona un bloque en el canvas para editar sus propiedades.
          </p>
        </div>
      </aside>
    );
  }

  const product =
    block.type === "product_single"
      ? catalog.products.find((p) => p.id === block.product1)
      : null;

  return (
    <aside className="cmp-inspector-panel">
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
        <dl className="cmp-inspector-meta">
          {block._sourceType && (
            <>
              <dt>Fuente</dt>
              <dd>{block._sourceType}</dd>
            </>
          )}
          {block._sourceId && (
            <>
              <dt>Source ID</dt>
              <dd className="mono">{block._sourceId}</dd>
            </>
          )}
          {block.text && (
            <>
              <dt>Texto</dt>
              <dd>{block.text}</dd>
            </>
          )}
          {product && (
            <>
              <dt>Producto</dt>
              <dd>
                {product.name} · {product.price}
              </dd>
            </>
          )}
        </dl>
        <p
          style={{
            fontSize: 11,
            color: "var(--text-subtle)",
            marginTop: 16,
            padding: "12px 0",
            borderTop: "1px dashed var(--border)",
          }}
        >
          Editor completo (text / product / hero / brandstrip / cta /
          divider / sections / steps / freebird / composed) en sub-PR
          2d.
        </p>
      </div>
    </aside>
  );
}

type BlocksTree = ReturnType<typeof useComposerStore.getState>["blocks"];

/** Recursive find that walks into innerBlocks / column wrappers — the
 * inspector should resolve a selection from anywhere in the tree. */
function findBlock(
  blocks: BlocksTree,
  id: string,
): BlocksTree[number] | null {
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
