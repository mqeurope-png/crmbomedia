"use client";

/**
 * Composer editor — fits inside the CRM main content area.
 *
 * Layout:
 *
 *   <div class="cmp-app-shell">              ← height: 100%
 *     <header class="cmp-topbar"/>
 *     <div class="cmp-body">                 ← flex: 1; row
 *       <Sidebar />                          ← width: 280
 *       <Canvas />                           ← flex: 1
 *     </div>
 *     <Footer />                             ← cmp-statusbar
 *   </div>
 *
 * No `DndContext` wrapper at this level — the page-level dnd context
 * in earlier revisions intercepted pointer events through the CRM
 * AppShell and silently killed clicks on the sidebar palette in some
 * browsers. The canvas BlockCard still uses `useSortable` via its
 * own dnd context inside `Canvas`. Drag from sidebar → canvas was
 * never actually wired in the original Composer either (the original
 * uses click-to-add, with `draggable` only as a visual affordance).
 */

import { useEffect, useMemo, useState } from "react";

import { Canvas } from "../components/Canvas";
import { Footer } from "../components/Footer";
import { PreviewPanel } from "../components/PreviewPanel";
import { Sidebar } from "../components/Sidebar";
import { TopBar } from "../components/TopBar";
import { hydrateDraft, scheduleAutoSave } from "../lib/autoSave";
import { renderEmailHtml } from "../lib/emailGen";
import { useComposerStore } from "../lib/store";
import { toAppState } from "../lib/types";
import { useCatalog } from "../lib/useCatalog";

export default function ComposerEditorPage() {
  const { catalog, loading, error } = useCatalog();
  const blocks = useComposerStore((s) => s.blocks);
  const lang = useComposerStore((s) => s.activeLang);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [brandFilter, setBrandFilter] = useState<string>("all");

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
        className="composer-editor cmp-app-shell"
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
        className="composer-editor cmp-app-shell"
        style={{ display: "flex", alignItems: "center", justifyContent: "center" }}
      >
        <p>Cargando editor…</p>
      </div>
    );
  }

  return (
    <div className="composer-editor cmp-app-shell">
      <TopBar
        onCopyHtml={handleCopyHtml}
        onTogglePreview={() => setPreviewOpen((v) => !v)}
        previewHidden={!previewOpen}
      />
      <div className="cmp-body">
        <Sidebar
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed((v) => !v)}
          brandFilter={brandFilter}
          setBrandFilter={setBrandFilter}
        />
        <Canvas catalog={catalog} />
      </div>
      <Footer />
      {previewOpen && (
        <div
          className="cmp-preview-modal"
          role="dialog"
          aria-label="Vista previa del email"
          onClick={() => setPreviewOpen(false)}
        >
          <div
            className="cmp-preview-modal-inner"
            onClick={(e) => e.stopPropagation()}
          >
            <PreviewPanel emailHtml={emailHtml} />
          </div>
        </div>
      )}
    </div>
  );
}
