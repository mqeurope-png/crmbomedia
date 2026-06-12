"use client";

/**
 * Canvas — distilled from `function Canvas` in
 * `bomedia-v4/app-compositor.jsx` (lines 1629-1730).
 *
 * Fase 2.1 scope: the canvas chrome (canvas / canvas-inner / canvas-header)
 * + the sortable block list + drop target + empty-state hint. The
 * IA button, HTML dropdown menu, Word/PDF exports, undo/redo and the
 * SaveAsTemplate modal port in Fase 2.2 alongside the inspector.
 *
 * Keeps the original CSS class names so `composer.css` lights it up:
 *   .canvas.scroll · .canvas-inner · .canvas-header · .canvas-meta
 *   .sync-ok · .dot
 */

import {
  SortableContext,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { useDroppable } from "@dnd-kit/core";

import { useComposerStore } from "../lib/store";
import { toAppState } from "../lib/types";
import type { Block, ComposerCatalog } from "../lib/types";
import { BlockCard } from "./BlockCard";
import { Icon } from "./Icon";

export interface CanvasProps {
  catalog: ComposerCatalog | null;
}

export const CANVAS_DROPPABLE_ID = "composer-canvas-dropzone";

export function Canvas({ catalog }: CanvasProps) {
  const blocks = useComposerStore((s) => s.blocks);
  const selectedId = useComposerStore((s) => s.selectedId);
  const lang = useComposerStore((s) => s.activeLang);
  const emailTitle = useComposerStore((s) => s.emailTitle);
  const saveStatus = useComposerStore((s) => s.saveStatus);
  const setSelected = useComposerStore((s) => s.setSelected);
  const setEmailTitle = useComposerStore((s) => s.setEmailTitle);
  const deleteBlock = useComposerStore((s) => s.deleteBlock);
  const duplicateBlock = useComposerStore((s) => s.duplicateBlock);
  const ungroupBlock = useComposerStore((s) => s.ungroupBlock);
  const reorderBlocks = useComposerStore((s) => s.reorderBlocks);

  const { setNodeRef, isOver } = useDroppable({ id: CANVAS_DROPPABLE_ID });
  const appState = catalog ? toAppState(catalog) : null;

  const moveUp = (id: string) => {
    const idx = blocks.findIndex((b) => b.id === id);
    if (idx <= 0) return;
    reorderBlocks(idx, idx - 1);
  };
  const moveDown = (id: string) => {
    const idx = blocks.findIndex((b) => b.id === id);
    if (idx < 0 || idx >= blocks.length - 1) return;
    reorderBlocks(idx, idx + 1);
  };

  return (
    <main
      ref={setNodeRef}
      className={`canvas scroll${isOver ? " is-droppable" : ""}`}
      onClick={() => setSelected(null)}
    >
      <div className="canvas-inner">
        <div className="canvas-header">
          <div>
            <input
              className="canvas-title-plain"
              value={emailTitle}
              onChange={(e) => setEmailTitle(e.target.value)}
              placeholder="Email sin título"
              onClick={(e) => e.stopPropagation()}
            />
            <div className="canvas-meta" style={{ marginTop: 6 }}>
              <span className={saveStatus === "error" ? "sync-err" : "sync-ok"}>
                ●
              </span>
              <span>
                {saveStatus === "saving"
                  ? "Guardando…"
                  : saveStatus === "error"
                    ? "Error al guardar"
                    : "Sincronizado"}
              </span>
              <span className="dot" />
              <span>{blocks.length} bloques</span>
              <span className="dot" />
              <span>{lang.toUpperCase()}</span>
            </div>
          </div>
        </div>

        <SortableContext
          items={blocks.map((b: Block) => b.id)}
          strategy={verticalListSortingStrategy}
        >
          {blocks.length === 0 ? (
            <div
              style={{
                padding: 60,
                textAlign: "center",
                color: "var(--text-muted)",
              }}
            >
              <div
                style={{
                  display: "inline-flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 12,
                }}
              >
                <Icon name="layers" size={24} />
                <p className="serif" style={{ fontSize: 18, margin: 0 }}>
                  Empieza arrastrando un bloque desde la biblioteca
                </p>
                <p style={{ fontSize: 13, margin: 0 }}>
                  …o pulsa <kbd>⌘K</kbd> para abrir la paleta rápida.
                </p>
              </div>
            </div>
          ) : (
            <ol
              className="canvas-blocks"
              style={{ listStyle: "none", padding: 0, margin: 0 }}
            >
              {blocks.map((block, index) => (
                <li key={block.id} style={{ marginBottom: 12 }}>
                  <BlockCard
                    block={block}
                    index={index}
                    total={blocks.length}
                    selected={selectedId === block.id}
                    onSelect={setSelected}
                    onDelete={deleteBlock}
                    onDuplicate={duplicateBlock}
                    onUngroup={ungroupBlock}
                    onMoveUp={moveUp}
                    onMoveDown={moveDown}
                    lang={lang}
                    appState={appState}
                    catalog={catalog}
                  />
                </li>
              ))}
            </ol>
          )}
        </SortableContext>
      </div>
    </main>
  );
}
