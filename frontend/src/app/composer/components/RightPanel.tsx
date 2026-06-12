"use client";

/**
 * Right panel — tabbed Biblioteca / Inspector pane that lives on
 * the right edge of the composer area (next to the canvas).
 *
 * The Fase-2.1 layout put Biblioteca on the left next to the CRM
 * sidebar — two sidebars stacked on the same side read as a UI bug
 * to the operator. This panel groups Biblioteca + Inspector on the
 * right so:
 *
 *   - the CRM sidebar (left) stays the only chrome there;
 *   - the canvas (center) gets the maximum horizontal real estate;
 *   - the editing surface (Biblioteca + Inspector) sits where the
 *     mouse already goes when editing a selected block.
 *
 * Auto-switch behavior matches the spec: when a block is selected,
 * the tab flips to Inspector. When the user deselects (clicks the
 * canvas backdrop), it flips back to Biblioteca. The user can
 * override manually at any time and the override sticks until the
 * next selection change.
 *
 * Inspector itself is a placeholder until Fase 2.2 — the per-type
 * editors (TextEditor, ProductEditor, HeroEditor, etc.) port there.
 * For now it shows the selected block's id / type / source label so
 * the operator can confirm the canvas → panel binding works.
 */

import { useEffect, useState } from "react";

import { useComposerStore } from "../lib/store";
import type { ComposerCatalog } from "../lib/types";
import { Sidebar } from "./Sidebar";

export interface RightPanelProps {
  brandFilter: string;
  setBrandFilter: (next: string) => void;
  catalog: ComposerCatalog;
}

type TabId = "library" | "inspector";

export function RightPanel({
  brandFilter,
  setBrandFilter,
  catalog,
}: RightPanelProps) {
  const selectedId = useComposerStore((s) => s.selectedId);
  const blocks = useComposerStore((s) => s.blocks);
  // Track the previous selection so manual tab overrides survive
  // re-renders that don't actually change the selection.
  const [activeTab, setActiveTab] = useState<TabId>(
    selectedId ? "inspector" : "library",
  );
  const [lastAutoSwitchTarget, setLastAutoSwitchTarget] = useState<
    string | null
  >(selectedId);

  useEffect(() => {
    // Auto-switch when the selection identity changes — not when
    // the same block is touched again. That keeps a user who tabbed
    // to Biblioteca while a block was selected from being yanked
    // back to Inspector on every store mutation.
    if (selectedId !== lastAutoSwitchTarget) {
      setActiveTab(selectedId ? "inspector" : "library");
      setLastAutoSwitchTarget(selectedId);
    }
  }, [selectedId, lastAutoSwitchTarget]);

  const selectedBlock = selectedId
    ? blocks.find((b) => b.id === selectedId) ?? null
    : null;

  return (
    <aside className="cmp-right-panel">
      <div className="cmp-right-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "library"}
          className={"cmp-right-tab" + (activeTab === "library" ? " active" : "")}
          onClick={() => setActiveTab("library")}
        >
          Biblioteca
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "inspector"}
          className={"cmp-right-tab" + (activeTab === "inspector" ? " active" : "")}
          onClick={() => setActiveTab("inspector")}
        >
          Inspector
        </button>
      </div>
      <div className="cmp-right-content">
        {activeTab === "library" ? (
          <Sidebar
            collapsed={false}
            onToggle={() => undefined}
            brandFilter={brandFilter}
            setBrandFilter={setBrandFilter}
          />
        ) : (
          <InspectorPlaceholder
            block={selectedBlock}
            catalog={catalog}
          />
        )}
      </div>
    </aside>
  );
}

interface InspectorPlaceholderProps {
  block: ReturnType<typeof useComposerStore.getState>["blocks"][number] | null;
  catalog: ComposerCatalog;
}

function InspectorPlaceholder({ block, catalog }: InspectorPlaceholderProps) {
  if (!block) {
    return (
      <div className="cmp-inspector-empty">
        <p style={{ fontSize: 13, color: "var(--text-muted)" }}>
          Selecciona un bloque en el canvas para editar sus propiedades.
        </p>
      </div>
    );
  }
  const product =
    block.type === "product_single"
      ? catalog.products.find((p) => p.id === block.product1)
      : null;
  return (
    <div className="cmp-inspector">
      <h4 style={{ margin: "0 0 8px", fontSize: 13 }}>{block.type}</h4>
      <dl className="cmp-inspector-meta">
        <dt>ID</dt>
        <dd className="mono">{block.id}</dd>
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
      <p style={{ fontSize: 11, color: "var(--text-subtle)", marginTop: 12 }}>
        Editor completo con campos por tipo (text, product, hero, brandstrip,
        cta, divider…) en Fase 2.2.
      </p>
    </div>
  );
}
