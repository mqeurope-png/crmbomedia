"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getCurrentUser, type User } from "../lib/api";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

const ANONYMOUS_ROUTES = ["/login", "/password-reset"];
// The composer module renders inside the CRM main content area —
// the CRM sidebar stays visible on the left. The composer brings
// its own topbar / sidebar-of-area / canvas / inspector /
// statusbar inside that content area.
const FULL_BLEED_ROUTES: string[] = [];
const SIDEBAR_STORAGE_KEY = "crmbo:sidebar:collapsed";

/**
 * Layout-level wrapper applied to every page in the app. Two
 * responsibilities:
 *
 * 1. Skip rendering the shell on anonymous routes (`/login`,
 *    `/password-reset`) so those pages keep their full-bleed layout.
 * 2. Fetch the current user once for the children that need it (the
 *    sidebar + topbar need name/role; pages keep doing their own
 *    fetches because they need the user for permission gates too).
 *
 * The actual scroll model lives in CSS: `body` is `overflow: hidden`
 * so the topbar and sidebar stay fixed, and only the content area
 * scrolls. See `.app-shell` rules in `styles.css`.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "";
  const isAnonymous = ANONYMOUS_ROUTES.some((path) =>
    pathname === path || pathname.startsWith(`${path}/`),
  );

  const [user, setUser] = useState<User | null>(null);
  const [userLoaded, setUserLoaded] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Restore the persisted collapse preference on first paint. Done in
  // useEffect (not useState init) so the SSR pass doesn't leak the
  // stored value into the static markup — Next.js's hydration would
  // otherwise complain about a mismatch on browsers with the key set.
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(SIDEBAR_STORAGE_KEY);
      if (stored === "true") setCollapsed(true);
    } catch {
      // ignore — sidebar just stays expanded if storage is unavailable
    }
  }, []);

  useEffect(() => {
    if (isAnonymous) {
      setUserLoaded(true);
      return;
    }
    let cancelled = false;
    getCurrentUser()
      .then((u) => {
        if (!cancelled) setUser(u);
      })
      .catch(() => {
        // 401 → page-level guards already redirect to /login; the
        // shell stays user-less in the meantime.
      })
      .finally(() => {
        if (!cancelled) setUserLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [isAnonymous]);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
      } catch {
        // ignore
      }
      return next;
    });
  }

  const isFullBleed = FULL_BLEED_ROUTES.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );

  if (isAnonymous || isFullBleed) {
    return <>{children}</>;
  }

  return (
    <div
      className={`app-shell${collapsed ? " is-collapsed" : ""}${
        drawerOpen ? " drawer-open" : ""
      }`}
    >
      <TopBar
        user={user}
        userLoaded={userLoaded}
        onToggleDrawer={() => setDrawerOpen((value) => !value)}
      />
      <Sidebar
        user={user}
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
        onCloseDrawer={() => setDrawerOpen(false)}
      />
      {drawerOpen ? (
        <button
          type="button"
          className="app-shell-scrim"
          aria-label="Cerrar menú"
          onClick={() => setDrawerOpen(false)}
        />
      ) : null}
      <div className="app-shell-content">{children}</div>
    </div>
  );
}
