"use client";

/**
 * Composer canvas — wires Sidebar + Canvas under a single `DndContext`,
 * hydrates the draft from backend / localStorage on mount, and
 * subscribes the autosave routine to canvas-shape mutations.
 *
 * Fase 2.1: structural surface only — drag from palette to canvas,
 * reorder within canvas, autosave round-trip. The inspector,
 * preview panel, command palette and template-save modal ship in
 * Fase 2.2 alongside the per-type editors.
 */

import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { useEffect } from "react";

import { PageHeader } from "../../components/PageHeader";
import { ComposerCanvas, CANVAS_DROPPABLE_ID } from "../components/Canvas";
import { ComposerSidebar } from "../components/Sidebar";
import { hydrateDraft, scheduleAutoSave } from "../lib/autoSave";
import { useCatalog } from "../lib/useCatalog";
import { useComposerStore } from "../lib/store";
import type { AddBlockSpec } from "../lib/types";

interface PaletteDragData {
  kind: "palette";
  itemId: string;
  spec: AddBlockSpec;
  label: string;
}

function isPaletteDragData(data: unknown): data is PaletteDragData {
  return (
    typeof data === "object" &&
    data !== null &&
    (data as { kind?: unknown }).kind === "palette"
  );
}

export default function ComposerCanvasPage() {
  const { catalog, loading, error } = useCatalog();
  const blocks = useComposerStore((s) => s.blocks);
  const saveStatus = useComposerStore((s) => s.saveStatus);
  const lastSavedAt = useComposerStore((s) => s.lastSavedAt);

  // Hydrate draft once on mount.
  useEffect(() => {
    let cancelled = false;
    void hydrateDraft().then((draft) => {
      if (cancelled || !draft) return;
      const store = useComposerStore.getState();
      store.setBlocks(draft.blocks, { skipHistory: true });
      store.setLang(draft.activeLang);
      store.setEmailTitle(draft.emailTitle);
      store.setEditingTemplateId(draft.editingTemplateId);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Subscribe to canvas-shape changes → schedule autosave.
  useEffect(() => {
    const unsub = useComposerStore.subscribe(
      (s) => ({
        blocks: s.blocks,
        activeLang: s.activeLang,
        emailTitle: s.emailTitle,
        editingTemplateId: s.editingTemplateId,
      }),
      () => {
        scheduleAutoSave(useComposerStore.getState());
      },
      { equalityFn: (a, b) => a.blocks === b.blocks &&
        a.activeLang === b.activeLang &&
        a.emailTitle === b.emailTitle &&
        a.editingTemplateId === b.editingTemplateId },
    );
    return unsub;
  }, []);

  const sensors = useSensors(
    // 6-px distance threshold so click-to-select still wins over a
    // tiny accidental drag — matches the original Composer feel.
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  const handleDragEnd = (event: DragEndEvent): void => {
    const { active, over } = event;
    if (!over) return;
    const store = useComposerStore.getState();
    if (isPaletteDragData(active.data.current)) {
      if (over.id === CANVAS_DROPPABLE_ID) {
        const id = store.addBlock(active.data.current.spec);
        store.setSelected(id);
      }
      return;
    }
    // Sortable reorder — both ids are block ids.
    if (active.id !== over.id) {
      const fromIdx = store.blocks.findIndex((b) => b.id === active.id);
      const toIdx = store.blocks.findIndex((b) => b.id === over.id);
      if (fromIdx >= 0 && toIdx >= 0) store.reorderBlocks(fromIdx, toIdx);
    }
  };

  const saveLabel =
    saveStatus === "saving"
      ? "Guardando…"
      : saveStatus === "error"
        ? "Error al guardar"
        : lastSavedAt
          ? `Guardado ${new Date(lastSavedAt).toLocaleTimeString("es-ES")}`
          : "Sin cambios";
  const savePillClass =
    saveStatus === "saving"
      ? "is-saving"
      : saveStatus === "error"
        ? "is-error"
        : saveStatus === "saved"
          ? "is-saved"
          : "";

  return (
    <>
      <PageHeader
        title="Canvas"
        eyebrow="Composer"
        description="Editor del email. Arrastra elementos desde la biblioteca."
        actions={
          <span className={`composer-autosave-pill ${savePillClass}`}>
            {saveLabel}
          </span>
        }
      />
      {error ? (
        <div className="composer-placeholder" role="alert">
          <h2>No se pudo cargar el catálogo</h2>
          <p>{error}</p>
        </div>
      ) : loading || !catalog ? (
        <p>Cargando…</p>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <div className="composer-editor">
            <ComposerSidebar />
            <ComposerCanvas catalog={catalog} />
          </div>
          <p className="composer-canvas-meta">
            {blocks.length === 0
              ? "Sin bloques."
              : `${blocks.length} bloque${blocks.length === 1 ? "" : "s"} en el canvas.`}
          </p>
        </DndContext>
      )}
    </>
  );
}
