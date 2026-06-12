"use client";

/**
 * Composer draft autosave — 4 anti-wipe layers.
 *
 * Ported from the v5o autosave routine of the original Bomedia
 * Composer. Real-world incident motivation: an empty store load
 * (race between hydrate and first render) replaced a persisted
 * draft with `{ blocks: [] }`. The layers below stop that from
 * happening again:
 *
 *   1. localStorage write happens unconditionally — even if the
 *      backend is offline / slow, the user's work survives a
 *      page refresh.
 *   2. Backend push is debounced 1500ms; rapid edits only fire
 *      one PUT.
 *   3. Concurrent saves are short-circuited — if a previous push
 *      is in flight (`saveStatus === "saving"`), the current
 *      attempt is dropped and the debounce will retry.
 *   4. "Pristine" state (no blocks, no title, no template under
 *      edit) is checked against the backend snapshot before
 *      pushing — refusing the push if it would replace a
 *      non-empty server draft. Loud console.warn on refusal so
 *      regressions surface in dev tools.
 */

import { useComposerStore } from "./store";
import type { ComposerEditorState } from "./types";
import { saveDraft, getDraft } from "./composerApi";

const SAVE_DEBOUNCE_MS = 1500;
const STORAGE_KEY = "composer-draft-v1";

let saveTimer: ReturnType<typeof setTimeout> | null = null;

interface SerializedDraft {
  blocks: ComposerEditorState["blocks"];
  activeLang: ComposerEditorState["activeLang"];
  emailTitle: ComposerEditorState["emailTitle"];
  editingTemplateId: ComposerEditorState["editingTemplateId"];
  savedAt: number;
}

function serializeState(state: ComposerEditorState): SerializedDraft {
  return {
    blocks: state.blocks,
    activeLang: state.activeLang,
    emailTitle: state.emailTitle,
    editingTemplateId: state.editingTemplateId,
    savedAt: Date.now(),
  };
}

export function scheduleAutoSave(state: ComposerEditorState): void {
  // ───────── Layer 1: localStorage always ─────────
  // Belt + braces in case the user pulls the plug between debounce
  // and the backend PUT landing.
  try {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(serializeState(state)));
    }
  } catch (err) {
    // Quota exceeded / private mode — log and keep going.
    console.error("[composer.autoSave] localStorage failed", err);
  }

  // ───────── Layer 2: debounce backend push ─────────
  if (saveTimer !== null) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    void pushToBackend(useComposerStore.getState());
  }, SAVE_DEBOUNCE_MS);
}

async function pushToBackend(state: ComposerEditorState): Promise<void> {
  // ───────── Layer 3: skip if already saving ─────────
  if (state.saveStatus === "saving") {
    console.warn("[composer.autoSave] save already in flight, skipping");
    return;
  }

  // ───────── Layer 4: refuse to wipe non-empty server draft ─────────
  const isPristine =
    state.blocks.length === 0 &&
    !state.emailTitle &&
    state.editingTemplateId === null;
  if (isPristine) {
    try {
      const remote = await getDraft();
      const remoteBlocks = (remote.state as { blocks?: unknown }).blocks;
      if (Array.isArray(remoteBlocks) && remoteBlocks.length > 0) {
        console.warn(
          "[composer.autoSave] pristine local state would overwrite a non-empty server draft; refusing push. Use clearCanvas() to confirm wipe explicitly.",
        );
        return;
      }
    } catch (err) {
      // Network blip; safer to skip than to overwrite blindly.
      console.warn(
        "[composer.autoSave] pristine check could not reach server; skipping",
        err,
      );
      return;
    }
  }

  const store = useComposerStore.getState();
  store.setSaveStatus("saving");
  try {
    await saveDraft({
      blocks: state.blocks,
      activeLang: state.activeLang,
      emailTitle: state.emailTitle,
      editingTemplateId: state.editingTemplateId,
    });
    store.setSaveStatus("saved");
    store.setLastSavedAt(Date.now());
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[composer.autoSave] backend push failed", err);
    store.setSaveStatus("error", message);
  }
}

/** Restore a draft on mount. Priority: backend > localStorage > none. */
export async function hydrateDraft(): Promise<SerializedDraft | null> {
  try {
    const remote = await getDraft();
    const blob = remote.state as Partial<SerializedDraft>;
    if (
      blob &&
      typeof blob === "object" &&
      Array.isArray(blob.blocks)
    ) {
      return {
        blocks: blob.blocks,
        activeLang: blob.activeLang ?? "es",
        emailTitle: blob.emailTitle ?? "",
        editingTemplateId: blob.editingTemplateId ?? null,
        savedAt: blob.savedAt ?? Date.now(),
      };
    }
  } catch (err) {
    console.warn(
      "[composer.autoSave] backend draft unavailable, falling back to localStorage",
      err,
    );
  }

  try {
    if (typeof window !== "undefined") {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed: SerializedDraft = JSON.parse(raw);
        if (Array.isArray(parsed.blocks)) return parsed;
      }
    }
  } catch (err) {
    console.error("[composer.autoSave] localStorage parse failed", err);
  }

  return null;
}

/** Test hook — cancels the in-flight debounce so a unit test can
 * await a clean state. Not for production code. */
export function __cancelPendingSave(): void {
  if (saveTimer !== null) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
}

export const __internal__ = { STORAGE_KEY, SAVE_DEBOUNCE_MS };
