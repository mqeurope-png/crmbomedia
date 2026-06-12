"use client";

/**
 * EmailIframe + PreviewPanel — literal ports of the same-named
 * functions in `bomedia-v4/app-compositor.jsx` (lines 1814-1920).
 *
 * EmailIframe renders the generated email at its native design width
 * (620 px desktop / 380 px mobile) and CSS-scales it down when the
 * containing right panel is narrower. Stops the email's own
 * `@media (max-width:600px)` from firing prematurely and collapsing
 * pair/trio columns when the preview panel is narrow.
 *
 * The iframe is sandbox="" + srcDoc — no scripts can run, no
 * navigation, no localStorage access, no opaque-origin escape. If a
 * `<script>` slips past `sanitizeHtml` it stays contained.
 *
 * PreviewPanel has Visual / HTML tabs and Desktop / Mobile device
 * toggle, plus the meta strip "ES · 620 px · 5 bloques · Copiar HTML".
 */

import { useEffect, useRef, useState } from "react";

import { useComposerStore } from "../lib/store";
import { Icon } from "./Icon";

export interface EmailIframeProps {
  html: string;
  device: "desktop" | "mobile";
}

export function EmailIframe({ html, device }: EmailIframeProps) {
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);

  const baseWidth = device === "mobile" ? 380 : 620;
  const srcDoc = html || "<html><body></body></html>";

  useEffect(() => {
    const w = wrapperRef.current;
    if (!w || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0].contentRect.width;
      const next = cw < baseWidth ? Math.max(0.4, cw / baseWidth) : 1;
      setScale(next);
    });
    ro.observe(w);
    return () => ro.disconnect();
  }, [baseWidth]);

  return (
    <div
      ref={wrapperRef}
      style={{
        width: "100%",
        height: "75vh",
        minHeight: 500,
        overflow: "hidden",
        display: "flex",
        justifyContent: "center",
      }}
    >
      <iframe
        title="Email preview"
        srcDoc={srcDoc}
        sandbox=""
        style={{
          width: baseWidth + "px",
          height: 100 / scale + "%",
          flexShrink: 0,
          border: "none",
          background: "#fff",
          borderRadius: "var(--r-sm)",
          boxShadow: "var(--sh-md)",
          display: "block",
          transform: "scale(" + scale + ")",
          transformOrigin: "top center",
        }}
      />
    </div>
  );
}

export interface PreviewPanelProps {
  emailHtml: string;
  embedded?: boolean;
  onExpand?: () => void;
}

export function PreviewPanel({
  emailHtml,
  embedded,
  onExpand,
}: PreviewPanelProps) {
  const blocks = useComposerStore((s) => s.blocks);
  const lang = useComposerStore((s) => s.activeLang);
  const [tab, setTab] = useState<"visual" | "html">("visual");
  const [device, setDevice] = useState<"desktop" | "mobile">("desktop");

  const handleCopy = () => {
    if (!emailHtml) return;
    void navigator.clipboard?.writeText(emailHtml).catch(() => {
      /* ignore */
    });
  };

  return (
    <section
      className="preview"
      style={embedded ? { borderLeft: "none", flex: 1, minHeight: 0 } : undefined}
    >
      <div className="preview-header">
        <div className="preview-tabs">
          <button
            type="button"
            className={"preview-tab" + (tab === "visual" ? " active" : "")}
            onClick={() => setTab("visual")}
          >
            Visual
          </button>
          <button
            type="button"
            className={"preview-tab" + (tab === "html" ? " active" : "")}
            onClick={() => setTab("html")}
          >
            HTML
          </button>
        </div>
        <div className="device-toggle">
          <button
            type="button"
            className={"icon-btn" + (device === "desktop" ? " active" : "")}
            onClick={() => setDevice("desktop")}
            title="Desktop (620 px)"
          >
            <Icon name="monitor" size={14} />
          </button>
          <button
            type="button"
            className={"icon-btn" + (device === "mobile" ? " active" : "")}
            onClick={() => setDevice("mobile")}
            title="Móvil (380 px)"
          >
            <Icon name="smartphone" size={14} />
          </button>
          {onExpand && (
            <button
              type="button"
              className="icon-btn"
              onClick={onExpand}
              title="Vista ampliada"
              style={{ marginLeft: 6 }}
            >
              <Icon name="panel" size={14} />
            </button>
          )}
        </div>
      </div>
      <div className="preview-meta">
        <span>{lang.toUpperCase()}</span>
        <span>·</span>
        <span>{device === "mobile" ? "380 px" : "620 px"}</span>
        <span>·</span>
        <span>{blocks.length} bloques</span>
        <span>·</span>
        <span
          style={{ cursor: "pointer" }}
          onClick={handleCopy}
          title="Copiar email — pégalo en Gmail/Outlook y verás el render, no el código"
        >
          <Icon name="copy" size={11} /> Copiar HTML
        </span>
      </div>
      <div
        className="preview-body"
        style={{ padding: embedded ? 12 : 16, overflow: "auto" }}
      >
        {tab === "visual" ? (
          <EmailIframe html={emailHtml} device={device} />
        ) : (
          <div
            className="preview-frame"
            style={{
              padding: 16,
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--text-muted)",
              lineHeight: 1.55,
              background: "var(--bg-panel)",
              overflow: "auto",
              maxHeight: "75vh",
            }}
          >
            <pre
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                margin: 0,
              }}
            >
              {emailHtml}
            </pre>
          </div>
        )}
      </div>
    </section>
  );
}
