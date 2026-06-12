"use client";

/**
 * Composer editor — resizable 3-column layout inside the CRM
 * content area. CRM sidebar (always-on, on the left) is provided
 * by `AppShell`. The Composer arranges its own internal columns:
 *
 *   Canvas  | Inspector | Biblioteca
 *
 * Each separator persists user resizes via localStorage; the
 * layout key is `composer-layout-v1`.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Group,
  type Layout,
  Panel,
  Separator,
} from "react-resizable-panels";

import { Canvas } from "../components/Canvas";
import { Footer } from "../components/Footer";
import { PreviewPanel as PreviewModal } from "../components/PreviewPanel";
import { RightPanel } from "../components/RightPanel";
import { Sidebar } from "../components/Sidebar";
import { TopBar } from "../components/TopBar";
import { hydrateDraft, scheduleAutoSave } from "../lib/autoSave";
import { renderEmailHtml } from "../lib/emailGen";
import { useComposerStore } from "../lib/store";
import { toAppState } from "../lib/types";
import { useCatalog } from "../lib/useCatalog";

const LAYOUT_STORAGE_KEY = "composer-layout-v2";
const PANEL_LIBRARY = "p-library";
const PANEL_CANVAS = "p-canvas";
const PANEL_RIGHT = "p-right";

const DEFAULT_LAYOUT: Layout = {
  [PANEL_LIBRARY]: 22,
  [PANEL_CANVAS]: 45,
  [PANEL_RIGHT]: 33,
};

function loadLayout(): Layout | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const raw = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof parsed[PANEL_CANVAS] === "number"
    ) {
      return parsed;
    }
  } catch {
    /* corrupted entry — fall back to defaults */
  }
  return undefined;
}

function persistLayout(layout: Layout): void {
  try {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layout));
  } catch {
    /* quota / private mode — ignore */
  }
}

export default function ComposerEditorPage() {
  const { catalog, loading, error } = useCatalog();
  const blocks = useComposerStore((s) => s.blocks);
  const lang = useComposerStore((s) => s.activeLang);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [brandFilter, setBrandFilter] = useState<string>("all");
  // Resolve persisted layout client-side only so the SSR snapshot
  // matches the default values everyone gets on first paint.
  const initialLayoutRef = useRef<Layout | undefined>(undefined);
  if (initialLayoutRef.current === undefined) {
    initialLayoutRef.current = typeof window === "undefined" ? undefined : loadLayout();
  }
  const initialLayout = initialLayoutRef.current ?? DEFAULT_LAYOUT;

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
        <Group
          orientation="horizontal"
          defaultLayout={initialLayout}
          onLayoutChange={persistLayout}
          className="cmp-panel-group"
        >
          <Panel id={PANEL_LIBRARY} minSize={15}>
            <Sidebar
              collapsed={false}
              onToggle={() => undefined}
              brandFilter={brandFilter}
              setBrandFilter={setBrandFilter}
            />
          </Panel>
          <Separator className="cmp-resize-handle" />
          <Panel id={PANEL_CANVAS} minSize={30}>
            <Canvas catalog={catalog} />
          </Panel>
          <Separator className="cmp-resize-handle" />
          <Panel id={PANEL_RIGHT} minSize={20}>
            <RightPanel catalog={catalog} emailHtml={emailHtml} />
          </Panel>
        </Group>
      </div>
      <Footer />
      {previewOpen && (
        <div
          className="cmp-preview-modal"
          role="dialog"
          aria-label="Vista previa del email (modal ampliado)"
          onClick={() => setPreviewOpen(false)}
        >
          <div
            className="cmp-preview-modal-inner"
            onClick={(e) => e.stopPropagation()}
          >
            <PreviewModal emailHtml={emailHtml} />
          </div>
        </div>
      )}
    </div>
  );
}
