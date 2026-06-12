"use client";

/**
 * Canvas — the scrollable center column where blocks live.
 *
 * Wires `@dnd-kit/sortable` so the dropzone accepts new blocks dragged
 * from the Sidebar AND reorders existing blocks vertically. The page
 * mounts a single `DndContext` covering both Sidebar and Canvas;
 * Canvas declares the sortable strategy and the drop target.
 *
 * Fase 2.1 covers the structural surface (drop zone, sortable list,
 * BlockCard rendering, empty state with hint). Inline editing UI for
 * each block type lands in 2.2 alongside the inspector.
 */

import {
  SortableContext,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { useDroppable } from "@dnd-kit/core";
import { Plus } from "lucide-react";

import { useComposerStore } from "../lib/store";
import { toAppState } from "../lib/types";
import type { Block, ComposerCatalog } from "../lib/types";
import { BlockCard } from "./BlockCard";

export interface CanvasProps {
  catalog: ComposerCatalog | null;
}

export const CANVAS_DROPPABLE_ID = "composer-canvas-dropzone";

export function ComposerCanvas({ catalog }: CanvasProps) {
  const blocks = useComposerStore((s) => s.blocks);
  const selectedId = useComposerStore((s) => s.selectedId);
  const lang = useComposerStore((s) => s.activeLang);
  const setSelected = useComposerStore((s) => s.setSelected);
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
      className={`composer-canvas${isOver ? " is-droppable" : ""}`}
      aria-label="Lienzo del email"
      onClick={() => setSelected(null)}
    >
      <SortableContext
        items={blocks.map((b: Block) => b.id)}
        strategy={verticalListSortingStrategy}
      >
        {blocks.length === 0 ? (
          <div className="composer-canvas-empty">
            <div className="composer-canvas-empty-inner">
              <Plus size={20} aria-hidden />
              <p>Arrastra un elemento de la biblioteca o pulsa ⌘K para añadirlo.</p>
            </div>
          </div>
        ) : (
          <ol className="composer-canvas-list">
            {blocks.map((block, index) => (
              <li key={block.id} className="composer-canvas-item">
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
                />
              </li>
            ))}
          </ol>
        )}
      </SortableContext>
    </main>
  );
}
