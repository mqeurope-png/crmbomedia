"use client";

/**
 * Composer editor state — zustand store.
 *
 * Step 0 rebase: every action that mutates blocks routes through the
 * v5o-shape tree (sections via `columns[].blocks`, NOT a custom
 * `innerBlocks`). Block creation goes through `materialiseBlock` so
 * heroes / products / standalones land already populated, the same
 * way the original `addBlock` worked.
 */

import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";

import { materialiseBlock } from "./createBlock";
import {
  toAppState,
  type Block,
  type ComposerActions,
  type ComposerAppState,
  type ComposerCatalog,
  type ComposerEditorState,
  type Lang,
  type SaveStatus,
} from "./types";

const MAX_HISTORY = 30;

function pushHistory(
  history: Block[][],
  historyIdx: number,
  next: Block[],
): { history: Block[][]; historyIdx: number } {
  const trimmed = history.slice(0, historyIdx + 1);
  const appended = [...trimmed, next];
  const finalHistory = appended.slice(-MAX_HISTORY);
  return { history: finalHistory, historyIdx: finalHistory.length - 1 };
}

/** Recursively map every block in the tree, descending into
 * sections' `columns[].blocks`. */
function mapBlocksDeep(
  blocks: Block[],
  fn: (b: Block) => Block,
): Block[] {
  return blocks.map((b) => {
    const mapped = fn(b);
    if (
      mapped.type === "section" &&
      Array.isArray(mapped.columns) &&
      mapped.columns.length > 0
    ) {
      return {
        ...mapped,
        columns: mapped.columns.map((col) => ({
          ...col,
          blocks: mapBlocksDeep(col.blocks ?? [], fn),
        })),
      };
    }
    return mapped;
  });
}

/** Recursively remove a block by id from the tree, descending into
 * section columns. */
function deleteBlockFromTree(blocks: Block[], id: string): Block[] {
  const out: Block[] = [];
  for (const b of blocks) {
    if (b.id === id) continue;
    if (b.type === "section" && Array.isArray(b.columns)) {
      out.push({
        ...b,
        columns: b.columns.map((col) => ({
          ...col,
          blocks: deleteBlockFromTree(col.blocks ?? [], id),
        })),
      });
    } else {
      out.push(b);
    }
  }
  return out;
}

/** Walk the whole tree (top-level + section columns) and return the
 * first block matching `id`. */
function findBlockInTree(
  blocks: Block[],
  id: string,
): { block: Block | null } {
  for (const b of blocks) {
    if (b.id === id) return { block: b };
    if (b.type === "section" && Array.isArray(b.columns)) {
      for (const col of b.columns) {
        const found = findBlockInTree(col.blocks ?? [], id);
        if (found.block !== null) return found;
      }
    }
  }
  return { block: null };
}

/** Append a new block to a section's column. Mirrors `_addToSection`
 * in `app-main.jsx` lines 1057-1075. */
function addToSection(
  blocks: Block[],
  sectionId: string,
  columnIdx: number,
  newBlock: Block,
): Block[] {
  return blocks.map((b) => {
    if (b.id !== sectionId) {
      if (b.type === "section" && Array.isArray(b.columns)) {
        return {
          ...b,
          columns: b.columns.map((col) => ({
            ...col,
            blocks: addToSection(
              col.blocks ?? [],
              sectionId,
              columnIdx,
              newBlock,
            ),
          })),
        };
      }
      return b;
    }
    if (b.type !== "section" || !Array.isArray(b.columns)) return b;
    const cols = b.columns.map((col, idx) => {
      if (idx !== columnIdx) return col;
      return { ...col, blocks: [...(col.blocks ?? []), newBlock] };
    });
    return { ...b, columns: cols };
  });
}

export const useComposerStore = create<ComposerEditorState & ComposerActions>()(
  subscribeWithSelector((set, get) => {
    let catalogRef: ComposerCatalog | null = null;
    const appState = (): ComposerAppState =>
      catalogRef
        ? toAppState(catalogRef)
        : {
            brands: [],
            products: [],
            prewrittenTexts: [],
            composedBlocks: [],
            standaloneBlocks: [],
          };

    return {
      blocks: [],
      selectedId: null,
      activeLang: "es",
      emailTitle: "",
      editingTemplateId: null,
      history: [[]],
      historyIdx: 0,
      saveStatus: "idle",
      lastSavedAt: null,
      lastError: null,

      setBlocks: (blocks, opts) => {
        const state = get();
        if (opts?.skipHistory) {
          set({ blocks });
          return;
        }
        const { history, historyIdx } = pushHistory(
          state.history,
          state.historyIdx,
          blocks,
        );
        set({ blocks, history, historyIdx });
      },

      addBlock: (spec, opts) => {
        const block = materialiseBlock(spec, appState());
        if (!block) return null;
        const state = get();
        if (opts?.into) {
          const next = addToSection(
            state.blocks,
            opts.into.sectionId,
            opts.into.columnIdx,
            block,
          );
          state.setBlocks(next);
        } else {
          state.setBlocks([...state.blocks, block]);
        }
        return block.id;
      },

      addBlockToColumn: (sectionId, columnIndex, spec) => {
        const block = materialiseBlock(spec, appState());
        if (!block) return null;
        const state = get();
        const next = addToSection(state.blocks, sectionId, columnIndex, block);
        state.setBlocks(next);
        return block.id;
      },

      updateBlock: (id, patch) => {
        const state = get();
        const next = mapBlocksDeep(state.blocks, (b) =>
          b.id === id ? { ...b, ...patch } : b,
        );
        state.setBlocks(next);
      },

      deleteBlock: (id) => {
        const state = get();
        const next = deleteBlockFromTree(state.blocks, id);
        state.setBlocks(next);
        if (state.selectedId === id) set({ selectedId: null });
      },

      reorderBlocks: (fromIdx, toIdx) => {
        const state = get();
        if (
          fromIdx < 0 ||
          toIdx < 0 ||
          fromIdx >= state.blocks.length ||
          toIdx >= state.blocks.length ||
          fromIdx === toIdx
        ) {
          return;
        }
        const next = [...state.blocks];
        const [moved] = next.splice(fromIdx, 1);
        next.splice(toIdx, 0, moved);
        state.setBlocks(next);
      },

      duplicateBlock: (id) => {
        const state = get();
        const idx = state.blocks.findIndex((b) => b.id === id);
        if (idx < 0) return;
        const source = state.blocks[idx];
        const copyId = `b-dup-${Date.now().toString(36)}-${Math.random()
          .toString(36)
          .slice(2, 6)}`;
        const copy: Block = {
          ...source,
          id: copyId,
          columns: source.columns?.map((col) => ({
            ...col,
            blocks: (col.blocks ?? []).map((c) => ({
              ...c,
              id: `${copyId}-${c.id}`,
            })),
          })),
        };
        const next = [...state.blocks];
        next.splice(idx + 1, 0, copy);
        state.setBlocks(next);
      },

      ungroupBlock: (id) => {
        const state = get();
        const idx = state.blocks.findIndex((b) => b.id === id);
        if (idx < 0) return;
        const target = state.blocks[idx];
        // For sections, flatten the columns' contents.
        if (target.type === "section" && Array.isArray(target.columns)) {
          const flat = target.columns.flatMap((col) => col.blocks ?? []);
          if (flat.length === 0) return;
          const next = [...state.blocks];
          next.splice(idx, 1, ...flat);
          state.setBlocks(next);
        }
      },

      clearCanvas: () => {
        const state = get();
        state.setBlocks([]);
        set({ selectedId: null });
      },

      setSelected: (id) => set({ selectedId: id }),
      setLang: (lang: Lang) => set({ activeLang: lang }),
      setEmailTitle: (title) => set({ emailTitle: title }),
      setEditingTemplateId: (id) => set({ editingTemplateId: id }),

      undo: () => {
        const state = get();
        if (state.historyIdx <= 0) return;
        const prevIdx = state.historyIdx - 1;
        set({
          historyIdx: prevIdx,
          blocks: state.history[prevIdx] ?? [],
        });
      },

      redo: () => {
        const state = get();
        if (state.historyIdx >= state.history.length - 1) return;
        const nextIdx = state.historyIdx + 1;
        set({
          historyIdx: nextIdx,
          blocks: state.history[nextIdx] ?? [],
        });
      },

      setSaveStatus: (status: SaveStatus, error: string | null = null) =>
        set({ saveStatus: status, lastError: error }),
      setLastSavedAt: (ts) => set({ lastSavedAt: ts }),
      setCatalog: (catalog) => {
        catalogRef = catalog;
      },
    };
  }),
);

export const __internal__ = {
  mapBlocksDeep,
  deleteBlockFromTree,
  findBlockInTree,
  addToSection,
  pushHistory,
  MAX_HISTORY,
};
