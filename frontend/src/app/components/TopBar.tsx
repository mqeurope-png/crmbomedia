"use client";

import { Bell, Menu } from "lucide-react";
import Link from "next/link";
import type { User } from "../lib/api";
import type { AppMode } from "../lib/appMode";
import { homeForMode } from "../lib/appMode";
import { BoHubLogo } from "./branding/BoHubLogo";
import { GlobalSearch } from "./GlobalSearch";
import { UserMenu } from "./UserMenu";

type Props = {
  user: User | null;
  userLoaded: boolean;
  /** ERP-F2 — en modo ERP la marca dice «BoHub ERP» (mismo diseño). */
  mode: AppMode;
  onToggleDrawer: () => void;
};

/**
 * Sticky top bar: logo on the left, contact-search in the centre,
 * notification placeholder + user dropdown on the right. The
 * hamburger button shows only on small viewports; CSS toggles
 * visibility so the markup stays the same regardless of screen size.
 */
export function TopBar({ user, userLoaded, mode, onToggleDrawer }: Props) {
  // ERP-F2 — misma marca, misma casa: solo cambia la palabra («CRM»/«ERP») y
  // el destino del logo (dashboard del CRM vs inicio del ERP).
  const wordmark = mode === "erp" ? "ERP" : "CRM";
  return (
    <header className="app-topbar" role="banner">
      <button
        type="button"
        className="app-topbar-hamburger"
        aria-label="Abrir menú"
        onClick={onToggleDrawer}
      >
        <Menu size={20} aria-hidden />
      </button>
      <Link
        href={homeForMode(mode)}
        className="app-topbar-brand"
        aria-label={`BoHub ${wordmark} — Inicio`}
      >
        {/* En desktop lockup horizontal (isotipo + "BoHub CRM/ERP"); en
            mobile (< 768px) sólo el isotipo. CSS en .app-topbar-brand
            alterna .is-desktop / .is-mobile. */}
        <span className="app-topbar-brand-logo is-desktop">
          <BoHubLogo variant="horizontal" size={28} wordmark={wordmark} />
        </span>
        <span className="app-topbar-brand-logo is-mobile">
          <BoHubLogo variant="icon" size={32} wordmark={wordmark} />
        </span>
      </Link>
      <div className="app-topbar-search">
        <GlobalSearch />
      </div>
      <div className="app-topbar-actions">
        <button
          type="button"
          className="app-topbar-icon-button"
          aria-label="Notificaciones (sin novedades)"
          // Placeholder for now — real notifications land in a future
          // sprint. Kept visible so the layout doesn't reflow when it
          // becomes active.
        >
          <Bell size={18} aria-hidden />
          <span className="app-topbar-badge" aria-hidden>
            0
          </span>
        </button>
        {userLoaded ? <UserMenu user={user} mode={mode} /> : <span className="muted small">…</span>}
      </div>
    </header>
  );
}
