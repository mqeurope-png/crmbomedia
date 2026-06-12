"use client";

/**
 * EmbeddedPreview — preview panel rendered live inside the
 * RightPanel's "Preview" tab.
 *
 * Distinct from `PreviewPanel.tsx` (which still backs the
 * fullscreen modal opened by the TopBar eye button): this version
 * is sized for the panel column, defaults to Visual but keeps the
 * HTML and Desktop/Mobile toggles, and exposes a "Copiar HTML"
 * button in the meta strip. Email HTML rendering lives upstream
 * (page.tsx computes once via `useMemo` and passes the result down).
 */

import { useState } from "react";

import { useComposerStore } from "../lib/store";

export interface EmbeddedPreviewProps {
  emailHtml: string;
}

export function EmbeddedPreview({ emailHtml }: EmbeddedPreviewProps) {
  const lang = useComposerStore((s) => s.activeLang);
  const blocks = useComposerStore((s) => s.blocks);
  const [tab, setTab] = useState<"visual" | "html">("visual");
  const [device, setDevice] = useState<"desktop" | "mobile">("desktop");

  const handleCopy = () => {
    if (!emailHtml) return;
    void navigator.clipboard?.writeText(emailHtml).catch(() => undefined);
  };

  return (
    <div className="cmp-embedded-preview">
      <div className="cmp-embedded-preview-tabs">
        <button
          type="button"
          className={
            "cmp-embedded-preview-tab" + (tab === "visual" ? " active" : "")
          }
          onClick={() => setTab("visual")}
        >
          Visual
        </button>
        <button
          type="button"
          className={
            "cmp-embedded-preview-tab" + (tab === "html" ? " active" : "")
          }
          onClick={() => setTab("html")}
        >
          HTML
        </button>
        <div className="cmp-embedded-preview-spacer" />
        <button
          type="button"
          className={
            "cmp-embedded-preview-device" +
            (device === "desktop" ? " active" : "")
          }
          onClick={() => setDevice("desktop")}
          title="Vista escritorio (620 px)"
        >
          🖥
        </button>
        <button
          type="button"
          className={
            "cmp-embedded-preview-device" +
            (device === "mobile" ? " active" : "")
          }
          onClick={() => setDevice("mobile")}
          title="Vista móvil (375 px)"
        >
          📱
        </button>
      </div>
      <div className="cmp-embedded-preview-meta">
        <span>{lang.toUpperCase()}</span>
        <span>·</span>
        <span>{device === "mobile" ? "375 px" : "620 px"}</span>
        <span>·</span>
        <span>{blocks.length} bloques</span>
        <button
          type="button"
          className="cmp-embedded-preview-copy"
          onClick={handleCopy}
          title="Copiar HTML al portapapeles"
        >
          📋 Copiar HTML
        </button>
      </div>
      <div className={`cmp-embedded-preview-body device-${device}`}>
        {tab === "visual" ? (
          <iframe
            title="Email preview"
            srcDoc={emailHtml || "<html><body></body></html>"}
            sandbox=""
            className="cmp-embedded-preview-iframe"
          />
        ) : (
          <pre className="cmp-embedded-preview-html">{emailHtml}</pre>
        )}
      </div>
    </div>
  );
}
