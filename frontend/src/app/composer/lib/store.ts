"use client";

/**
 * Composer editor state — zustand store.
 *
 * Keeps the canvas state (`blocks` + `selectedId` + `activeLang` + ...)
 * plus the undo/redo history stack. Mutations that change the canvas
 * shape (add / update / delete / reorder / duplicate / ungroup / clear)
 * push onto the history; pure metadata mutations (selection, lang,
 * save status) don't, so undo doesn't bounce the user out of their
 * editing context.
 *
 * The store is intentionally fat (one giant slice). Splitting it into
 * actions / computed / state slices is the standard zustand pattern
 * for big stores but adds 4 files of plumbing for ~600 LOC of state,
 * which isn't a tradeoff that pays off until selectors get expensive.
 */

import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";

import type {
  AddBlockSpec,
  Block,
  ComposerActions,
  ComposerEditorState,
  Lang,
  SaveStatus,
} from "./types";

const MAX_HISTORY = 30;

function generateBlockId(type: string): string {
  return `${type}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function createBlockFromSpec(id: string, spec: AddBlockSpec): Block {
  return {
    id,
    type: spec.type,
    ...spec.params,
  };
}

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

function findBlockInTree(
  blocks: Block[],
  id: string,
): { parent: Block[] | null; index: number; block: Block | null } {
  for (let i = 0; i < blocks.length; i += 1) {
    const b = blocks[i];
    if (b.id === id) return { parent: blocks, index: i, block: b };
    if (Array.isArray(b.innerBlocks)) {
      const found = findBlockInTree(b.innerBlocks, id);
      if (found.block !== null) return found;
    }
  }
  return { parent: null, index: -1, block: null };
}

function mapBlocksDeep(
  blocks: Block[],
  fn: (b: Block) => Block,
): Block[] {
  return blocks.map((b) => {
    const mapped = fn(b);
    if (Array.isArray(mapped.innerBlocks) && mapped.innerBlocks.length > 0) {
      return { ...mapped, innerBlocks: mapBlocksDeep(mapped.innerBlocks, fn) };
    }
    return mapped;
  });
}

function deleteBlockFromTree(blocks: Block[], id: string): Block[] {
  const filtered: Block[] = [];
  for (const b of blocks) {
    if (b.id === id) continue;
    if (Array.isArray(b.innerBlocks)) {
      filtered.push({ ...b, innerBlocks: deleteBlockFromTree(b.innerBlocks, id) });
    } else {
      filtered.push(b);
    }
  }
  return filtered;
}

export const useComposerStore = create<ComposerEditorState & ComposerActions>()(
  subscribeWithSelector((set, get) => ({
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

    addBlock: (spec) => {
      const id = generateBlockId(spec.type);
      const block = createBlockFromSpec(id, spec);
      const state = get();
      state.setBlocks([...state.blocks, block]);
      return id;
    },

    addBlockToColumn: (sectionId, columnIndex, spec) => {
      const id = generateBlockId(spec.type);
      const child = createBlockFromSpec(id, spec);
      const state = get();
      const nextBlocks = mapBlocksDeep(state.blocks, (b) => {
        if (b.id !== sectionId) return b;
        const cols: Block[] = Array.isArray(b.innerBlocks) ? [...b.innerBlocks] : [];
        // section_2col / section_3col store one Block per column slot.
        // We treat each `Block` in innerBlocks as a column placeholder
        // wrapper whose `innerBlocks` holds the column's contents.
        while (cols.length <= columnIndex) {
          cols.push({ id: generateBlockId("section_col"), type: "text", innerBlocks: [] });
        }
        const target = cols[columnIndex];
        cols[columnIndex] = {
          ...target,
          innerBlocks: [...(target.innerBlocks ?? []), child],
        };
        return { ...b, innerBlocks: cols };
      });
      state.setBlocks(nextBlocks);
      return id;
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
      const copy: Block = {
        ...source,
        id: generateBlockId(source.type),
        innerBlocks: source.innerBlocks
          ? source.innerBlocks.map((c) => ({
              ...c,
              id: generateBlockId(c.type),
            }))
          : undefined,
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
      if (!Array.isArray(target.innerBlocks) || target.innerBlocks.length === 0) {
        return;
      }
      const next = [...state.blocks];
      next.splice(
        idx,
        1,
        ...target.innerBlocks.map((c) => ({
          ...c,
          id: generateBlockId(c.type),
        })),
      );
      state.setBlocks(next);
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
  })),
);

export const __internal__ = {
  generateBlockId,
  createBlockFromSpec,
  pushHistory,
  mapBlocksDeep,
  deleteBlockFromTree,
  findBlockInTree,
  MAX_HISTORY,
};
