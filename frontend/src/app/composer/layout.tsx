"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const TABS = [
  { href: "/composer/canvas", label: "Canvas" },
  { href: "/composer/templates", label: "Plantillas" },
  { href: "/composer/backoffice", label: "Backoffice" },
] as const;

export default function ComposerLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "";

  function isActive(href: string): boolean {
    return pathname === href || pathname.startsWith(`${href}/`);
  }

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
