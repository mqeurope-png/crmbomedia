"use client";

/**
 * Footer / status bar — literal port of the `<div className="footer">`
 * block in `bomedia-v4/app-main.jsx` (lines 1600-1613).
 *
 * Left side: "● Sincronizado · CRM · local · N bloques · M productos
 * · K plantillas". The original showed Supabase as the cloud backend;
 * here it's the CRM API.
 *
 * Right side: "v0 · By Edu & Claude Code · ⌘K para comandos". Version
 * tracks the CRM package, not the original Composer.
 */

import { useEffect, useState } from "react";

import { useComposerStore } from "../lib/store";
import { useCatalog } from "../lib/useCatalog";
import { listTemplates } from "../lib/composerApi";

export function Footer() {
  const blocks = useComposerStore((s) => s.blocks);
  const saveStatus = useComposerStore((s) => s.saveStatus);
  const lastSavedAt = useComposerStore((s) => s.lastSavedAt);
  const { catalog } = useCatalog();
  const [templateCount, setTemplateCount] = useState(0);

  useEffect(() => {
    void listTemplates()
      .then((rows) => setTemplateCount(rows.length))
      .catch(() => setTemplateCount(0));
  }, []);

  const syncLabel =
    saveStatus === "saving"
      ? "Guardando…"
      : saveStatus === "error"
        ? "⚠ Error al guardar"
        : lastSavedAt
          ? `Sincronizado · ${new Date(lastSavedAt).toLocaleTimeString("es-ES")}`
          : "Sincronizado";

  const productCount = catalog?.products.length ?? 0;

  return (
    <div className="footer">
      <span className="cmp-status">
        <span className="status-dot" /> {syncLabel}
      </span>
      <span className="dot" />
      <span>CRM · backend</span>
      <span className="dot" />
      <span>{blocks.length} bloques</span>
      <span className="dot" />
      <span>
        {productCount} productos · {templateCount} plantillas
      </span>
      <div
        style={{
          marginLeft: "auto",
          display: "flex",
          gap: 16,
          alignItems: "center",
        }}
      >
        <span>v0 · By Edu &amp; Claude Code</span>
        <span className="dot" />
        <span>⌘K para comandos</span>
      </div>
    </div>
  );
}
