"use client";

/**
 * TopBar — literal port of the header section in
 * `bomedia-v4/app-main.jsx` (lines 1389-1454).
 *
 * Layout (left to right):
 *   [Bomedia logo] [bomedia · email composer]
 *   [Compositor / Backoffice crumbs]
 *   [Buscar bloques, productos… (⌘K)]
 *   [ES FR DE EN NL] [eye] [copy-html] [share] [user avatar · X logout]
 *
 * Adapted for the CRM:
 *   - "Backoffice" tab routes to `/composer/backoffice` (Fase 3
 *     destination); the Compositor tab routes to `/composer/canvas`.
 *   - The IA button (purple ✨) is rendered but disabled with a
 *     "Próximamente — Fase 3" tooltip.
 *   - The user logout button is dropped; the CRM has its own session
 *     logout under the user menu of the CRM topbar (still reachable
 *     from `/composer/templates`).
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

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
  const pathname = usePathname() ?? "";
  const lang = useComposerStore((s) => s.activeLang);
  const setLang = useComposerStore((s) => s.setLang);

  const isCompositor = pathname.startsWith("/composer/canvas");
  const isBackoffice = pathname.startsWith("/composer/backoffice");

  return (
    <header className="cmp-topbar">
      <div className="topbar-brand">
        <div
          className="topbar-logo"
          style={{
            background:
              "linear-gradient(135deg, #8b5cf6 0%, #ec4899 60%, #3b82f6 100%)",
            color: "#fff",
          }}
        >
          B
        </div>
        <div>
          <div className="topbar-title">
            bomedia<span className="topbar-title-sub">email composer</span>
          </div>
        </div>
      </div>

      <div className="topbar-crumbs">
        <Link
          href="/composer/canvas"
          className={`topbar-crumb${isCompositor ? " active" : ""}`}
        >
          <Icon name="layers" size={14} /> Compositor
        </Link>
        <span className="topbar-sep">/</span>
        <Link
          href="/composer/backoffice"
          className={`topbar-crumb${isBackoffice ? " active" : ""}`}
        >
          <Icon name="database" size={14} />
          Backoffice
        </Link>
      </div>

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
        {isCompositor && (
          <>
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
          </>
        )}
      </div>
    </header>
  );
}
