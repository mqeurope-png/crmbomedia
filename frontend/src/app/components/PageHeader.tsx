"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

type Crumb = {
  label: string;
  href?: string;
};

type Props = {
  title: string;
  eyebrow?: string;
  description?: string;
  crumbs?: Crumb[];
  actions?: ReactNode;
};

/**
 * Sticky header reused by every page that lives inside `<AppShell>`.
 * Title left, actions right; an optional breadcrumb row goes above
 * the title. The sticky behaviour is CSS-only (`.page-header`); the
 * shell's content area is the scroll container so the header stays
 * pinned without `position: fixed`.
 */
export function PageHeader({
  title,
  eyebrow,
  description,
  crumbs,
  actions,
}: Props) {
  return (
    <header className="page-header">
      <div className="page-header-main">
        {crumbs && crumbs.length > 0 ? (
          <nav className="page-header-crumbs" aria-label="Breadcrumb">
            {crumbs.map((crumb, index) => (
              <span key={`${crumb.label}-${index}`}>
                {crumb.href ? (
                  <Link href={crumb.href}>{crumb.label}</Link>
                ) : (
                  <span>{crumb.label}</span>
                )}
                {index < crumbs.length - 1 ? (
                  <ChevronRight size={12} aria-hidden />
                ) : null}
              </span>
            ))}
          </nav>
        ) : null}
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {description ? <p className="lead">{description}</p> : null}
      </div>
      {actions ? <div className="page-header-actions">{actions}</div> : null}
    </header>
  );
}
