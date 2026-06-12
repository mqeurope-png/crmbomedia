"use client";

/**
 * RightPanel — tabbed Preview / Inspector pane on the far right of
 * the composer area.
 *
 * Default tab = Preview (embedded live render). Inspector tab is
 * disabled until the user selects a block in the canvas; once a
 * block is selected, the tab auto-switches to Inspector.
 */

import { useEffect, useState } from "react";

import { useComposerStore } from "../lib/store";
import type { ComposerCatalog } from "../lib/types";
import { EmbeddedPreview } from "./EmbeddedPreview";
import { Inspector } from "./Inspector";

export interface RightPanelProps {
  catalog: ComposerCatalog;
  emailHtml: string;
}

type TabId = "preview" | "inspector";

export function RightPanel({ catalog, emailHtml }: RightPanelProps) {
  const selectedId = useComposerStore((s) => s.selectedId);
  const [activeTab, setActiveTab] = useState<TabId>("preview");
  const [lastAutoSwitchTarget, setLastAutoSwitchTarget] = useState<
    string | null
  >(null);

  // Auto-switch to Inspector when the selection changes to a NEW
  // block (not the same one being touched again). If the user
  // manually switched to Preview while a block was selected, we
  // don't yank them back on every store mutation.
  useEffect(() => {
    if (selectedId && selectedId !== lastAutoSwitchTarget) {
      setActiveTab("inspector");
      setLastAutoSwitchTarget(selectedId);
    } else if (!selectedId) {
      setLastAutoSwitchTarget(null);
    }
  }, [selectedId, lastAutoSwitchTarget]);

  return (
    <aside className="cmp-right-panel">
      <div className="cmp-right-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "preview"}
          className={
            "cmp-right-tab" + (activeTab === "preview" ? " active" : "")
          }
          onClick={() => setActiveTab("preview")}
        >
          👁 Preview
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "inspector"}
          className={
            "cmp-right-tab" +
            (activeTab === "inspector" ? " active" : "") +
            (selectedId ? "" : " disabled")
          }
          onClick={() => selectedId && setActiveTab("inspector")}
          disabled={!selectedId}
          title={
            selectedId
              ? "Editar bloque seleccionado"
              : "Selecciona un bloque para editar"
          }
        >
          ✎ Inspector
        </button>
      </div>
      <div className="cmp-right-content">
        {activeTab === "preview" ? (
          <EmbeddedPreview emailHtml={emailHtml} />
        ) : (
          <Inspector catalog={catalog} />
        )}
      </div>
    </aside>
  );
}
