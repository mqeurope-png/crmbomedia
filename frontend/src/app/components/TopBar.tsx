"use client";

import { Bell, Menu } from "lucide-react";
import Link from "next/link";
import type { User } from "../lib/api";
import { BoHubLogo } from "./branding/BoHubLogo";
import { GlobalSearch } from "./GlobalSearch";
import { UserMenu } from "./UserMenu";

type Props = {
  user: User | null;
  userLoaded: boolean;
  onToggleDrawer: () => void;
};

/**
 * Sticky top bar: logo on the left, contact-search in the centre,
 * notification placeholder + user dropdown on the right. The
 * hamburger button shows only on small viewports; CSS toggles
 * visibility so the markup stays the same regardless of screen size.
 */
export function TopBar({ user, userLoaded, onToggleDrawer }: Props) {
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
        href="/"
        className="app-topbar-brand"
        aria-label="BoHub CRM — Inicio"
      >
        {/* En desktop lockup horizontal (isotipo + "BoHub CRM"); en
            mobile (< 768px) sólo el isotipo. CSS en .app-topbar-brand
            alterna .is-desktop / .is-mobile. */}
        <span className="app-topbar-brand-logo is-desktop">
          <BoHubLogo variant="horizontal" size={28} />
        </span>
        <span className="app-topbar-brand-logo is-mobile">
          <BoHubLogo variant="icon" size={32} />
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
        {userLoaded ? <UserMenu user={user} /> : <span className="muted small">…</span>}
      </div>
    </header>
  );
}
