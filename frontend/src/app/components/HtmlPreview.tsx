"use client";

import { useEffect, useRef } from "react";

/**
 * Sandboxed iframe that renders raw HTML. Used by the template editor
 * and the campaign wizard for the "preview on the right" pane.
 * `sandbox` without `allow-scripts` keeps pasted HTML inert — emails
 * shouldn't run JS anyway.
 */
export function HtmlPreview({ html }: { html: string }) {
  const frame = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const node = frame.current;
    if (!node) return;
    node.srcdoc = html || "<p style='color:#888;font-family:sans-serif'>Sin contenido…</p>";
  }, [html]);

  return (
    <iframe
      ref={frame}
      title="Vista previa HTML"
      className="html-preview-frame"
      sandbox=""
    />
  );
}
