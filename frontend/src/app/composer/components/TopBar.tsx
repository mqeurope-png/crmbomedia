"use client";

/**
 * TopBar — trimmed for the CRM integration.
 *
 * The original `bomedia-v4` topbar carried four sections:
 *   [brand logo + "bomedia email composer"]
 *   [Compositor / Backoffice crumbs]
 *   [⌘K search]
 *   [lang pill + actions + user avatar]
 *
 * The CRM owns its own topbar + sidebar, which already carry the
 * branding ("CRMBO Media CRM") and the route to Composer / Backoffice.
 * Repeating either inside the composer is visual noise. We keep only
 * the bits unique to the composer — the ⌘K search, the lang pill,
 * and the per-mode action icons.
 */

import { useComposerStore } from "../lib/store";
import type { Lang } from "../lib/types";
import { Icon } from "./Icon";

const LANGS: ReadonlyArray<Lang> = ["es", "fr", "de", "en", "nl"];

export interface TopBarProps {
  onOpenCmdk?: () => void;
  onCopyHtml?: () => void;
  onTogglePreview?: () => void;
  previewHidden?: boolean;
}

export function TopBar({
  onOpenCmdk,
  onCopyHtml,
  onTogglePreview,
  previewHidden,
}: TopBarProps) {
  const lang = useComposerStore((s) => s.activeLang);
  const setLang = useComposerStore((s) => s.setLang);

  return (
    <header className="cmp-topbar">
      <button
        type="button"
        className="topbar-search"
        onClick={onOpenCmdk}
        disabled={!onOpenCmdk}
      >
        <Icon name="search" size={14} />
        <span>Buscar bloques, productos…</span>
        <span className="topbar-search-kbd">⌘K</span>
      </button>

      <div className="topbar-actions">
        <div className="lang-pill">
          {LANGS.map((l) => (
            <button
              key={l}
              type="button"
              className={lang === l ? "active" : ""}
              onClick={() => setLang(l)}
            >
              {l.toUpperCase()}
            </button>
          ))}
        </div>
        <button
          type="button"
          className={"icon-btn" + (previewHidden ? "" : " active")}
          onClick={onTogglePreview}
          title="Preview"
        >
          <Icon name="eye" size={16} />
        </button>
        <button
          type="button"
          className="icon-btn"
          title="Copiar HTML — pégalo en Gmail/Outlook con formato (con UTM tracking)"
          onClick={onCopyHtml}
          disabled={!onCopyHtml}
        >
          <Icon name="code" size={16} />
        </button>
        <button
          type="button"
          className="icon-btn"
          title="Compartir (próximamente)"
          disabled
        >
          <Icon name="share" size={16} />
        </button>
        <button
          type="button"
          className="icon-btn"
          title="Asistente IA — disponible en Fase 3"
          disabled
          style={{ color: "#8b5cf6" }}
        >
          <Icon name="sparkles" size={16} />
        </button>
      </div>
    </header>
  );
}
