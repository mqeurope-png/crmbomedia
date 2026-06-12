"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

// composer.css is loaded only on /composer/* routes, so the
// `.composer-editor` wrapper scoping inside the file stays
// segment-local and doesn't pollute the CRM tokens.
import "../../styles/composer.css";

const TABS = [
  { href: "/composer/canvas", label: "Canvas" },
  { href: "/composer/templates", label: "Plantillas" },
  { href: "/composer/backoffice", label: "Backoffice" },
] as const;

export default function ComposerLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "";

  // `/composer/canvas` renders the editor full-screen with its own
  // TopBar (literal port of `bomedia-v4`'s topbar). The shell-level
  // tab strip only makes sense on the secondary pages
  // (`/composer/templates`, `/composer/backoffice`) that still use
  // the CRM's chrome.
  if (pathname.startsWith("/composer/canvas")) {
    return <>{children}</>;
  }

  const isActive = (href: string) =>
    pathname === href || pathname.startsWith(`${href}/`);

  return (
    <div className="composer-shell">
      <nav className="composer-tabs" aria-label="Secciones del Composer">
        {TABS.map((tab) => (
          <Link
            key={tab.href}
            href={tab.href}
            className={`composer-tab${isActive(tab.href) ? " is-active" : ""}`}
            aria-current={isActive(tab.href) ? "page" : undefined}
          >
            {tab.label}
          </Link>
        ))}
      </nav>
      <div className="composer-body">{children}</div>
    </div>
  );
}
