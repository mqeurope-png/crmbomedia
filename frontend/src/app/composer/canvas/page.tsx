"use client";

/**
 * Composer editor — wires the literal Composer shell.
 *
 * Structure (mirrors `bomedia-v4/app-main.jsx` lines 1387-1614):
 *
 *   <div class="composer-editor app-shell">
 *     <header class="topbar">…</header>
 *     <div class="main sidebar-collapsed/preview-hidden">
 *       <Sidebar />
 *       <Canvas />
 *       <aside class="right-panel"><PreviewPanel/></aside>
 *     </div>
 *     <Footer />
 *   </div>
 *
 * Wraps everything in a `<DndContext>` so the Sidebar's draggable
 * palette items can drop on the canvas's `useDroppable` zone and
 * the sortable list inside the canvas reorders without losing its
 * own drag-end.
 */

import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { useEffect, useMemo, useState } from "react";

import { Canvas, CANVAS_DROPPABLE_ID } from "../components/Canvas";
import { Footer } from "../components/Footer";
import { PreviewPanel } from "../components/PreviewPanel";
import { Sidebar } from "../components/Sidebar";
import { TopBar } from "../components/TopBar";
import { hydrateDraft, scheduleAutoSave } from "../lib/autoSave";
import { renderEmailHtml } from "../lib/emailGen";
import { useComposerStore } from "../lib/store";
import { toAppState } from "../lib/types";
import type { AddBlockSpec } from "../lib/types";
import { useCatalog } from "../lib/useCatalog";

interface PaletteDragData {
  kind: "palette";
  spec: AddBlockSpec;
  label: string;
}

function isPaletteDragData(d: unknown): d is PaletteDragData {
  return (
    typeof d === "object" &&
    d !== null &&
    (d as { kind?: unknown }).kind === "palette"
  );
}

export default function ComposerEditorPage() {
  const { catalog, loading, error } = useCatalog();
  const blocks = useComposerStore((s) => s.blocks);
  const lang = useComposerStore((s) => s.activeLang);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [previewHidden, setPreviewHidden] = useState(false);
  const [brandFilter, setBrandFilter] = useState<string>("all");

  // Hydrate the draft once on mount.
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

  // Schedule autosave on any canvas-shape change.
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
      {
        equalityFn: (a, b) =>
          a.blocks === b.blocks &&
          a.activeLang === b.activeLang &&
          a.emailTitle === b.emailTitle &&
          a.editingTemplateId === b.editingTemplateId,
      },
    );
    return unsub;
  }, []);

  const sensors = useSensors(
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
    // Sortable reorder.
    if (active.id !== over.id) {
      const fromIdx = store.blocks.findIndex((b) => b.id === active.id);
      const toIdx = store.blocks.findIndex((b) => b.id === over.id);
      if (fromIdx >= 0 && toIdx >= 0) store.reorderBlocks(fromIdx, toIdx);
    }
  };

  // Live-render the email for the preview pane.
  const emailHtml = useMemo(() => {
    if (!catalog) return "";
    return renderEmailHtml(blocks, toAppState(catalog), lang);
  }, [blocks, catalog, lang]);

  const handleCopyHtml = () => {
    if (!emailHtml) return;
    void navigator.clipboard?.writeText(emailHtml).catch(() => undefined);
  };

  if (error) {
    return (
      <div
        className="composer-editor app-shell"
        style={{ display: "flex", alignItems: "center", justifyContent: "center" }}
      >
        <div style={{ textAlign: "center", padding: 40 }}>
          <h2>No se pudo cargar el catálogo</h2>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  if (loading || !catalog) {
    return (
      <div
        className="composer-editor app-shell"
        style={{ display: "flex", alignItems: "center", justifyContent: "center" }}
      >
        <p>Cargando editor…</p>
      </div>
    );
  }

  return (
    <div
      className="composer-editor app-shell"
      style={{ ["--right-panel-w" as string]: "420px" }}
    >
      <TopBar
        onCopyHtml={handleCopyHtml}
        onTogglePreview={() => setPreviewHidden((v) => !v)}
        previewHidden={previewHidden}
      />
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <div
          className={
            "main" +
            (sidebarCollapsed ? " sidebar-collapsed" : "") +
            (previewHidden ? " preview-hidden" : "")
          }
        >
          <Sidebar
            collapsed={sidebarCollapsed}
            onToggle={() => setSidebarCollapsed((v) => !v)}
            brandFilter={brandFilter}
            setBrandFilter={setBrandFilter}
          />
          <Canvas catalog={catalog} />
          {!previewHidden && (
            <aside
              className="right-panel"
              style={{
                display: "flex",
                flexDirection: "column",
                minHeight: 0,
                background: "var(--bg-sunken)",
                borderLeft: "1px solid var(--border)",
              }}
            >
              <PreviewPanel emailHtml={emailHtml} embedded />
            </aside>
          )}
        </div>
      </DndContext>
      <Footer />
    </div>
  );
}
