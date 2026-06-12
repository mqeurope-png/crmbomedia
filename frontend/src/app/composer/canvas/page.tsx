"use client";

/**
 * Composer editor — fits inside the CRM main content area.
 *
 * Layout (post-PR #82 + the right-panel switch):
 *
 *   <div class="cmp-app-shell">              ← height: 100%
 *     <header class="cmp-topbar"/>           ← search + lang + actions
 *     <div class="cmp-body">                 ← flex: 1; row
 *       <Canvas />                           ← flex: 1
 *       <RightPanel />                       ← width: 320; Biblioteca/Inspector tabs
 *     </div>
 *     <Footer />                             ← cmp-statusbar
 *   </div>
 *
 * The CRM sidebar (left of the whole thing) stays visible because
 * `/composer/canvas` is no longer in `FULL_BLEED_ROUTES` (since #82).
 * Biblioteca + Inspector were merged into a single right panel with
 * tabs so the operator doesn't see two sidebars stacked on the
 * left.
 */

import { useEffect, useMemo, useState } from "react";

import { Canvas } from "../components/Canvas";
import { Footer } from "../components/Footer";
import { PreviewPanel } from "../components/PreviewPanel";
import { RightPanel } from "../components/RightPanel";
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
        <Canvas catalog={catalog} />
        <RightPanel
          brandFilter={brandFilter}
          setBrandFilter={setBrandFilter}
          catalog={catalog}
        />
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
