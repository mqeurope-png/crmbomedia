"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getCurrentUser, getStoredToken, logout, type User } from "../lib/api";
import { useIdleTimeout } from "../lib/useIdleTimeout";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

const ANONYMOUS_ROUTES = ["/login", "/password-reset", "/welcome"];
// PR-F: 4h sin interacción → logout silencioso.
const IDLE_TIMEOUT_MS = 4 * 60 * 60 * 1000;
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
  const router = useRouter();
  const isAnonymous = ANONYMOUS_ROUTES.some((path) =>
    pathname === path || pathname.startsWith(`${path}/`),
  );

  const [user, setUser] = useState<User | null>(null);
  const [userLoaded, setUserLoaded] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // PR-F: 4h idle → logout silencioso + redirect a /welcome. Solo en
  // rutas autenticadas con usuario cargado para no disparar en /login.
  useIdleTimeout(
    IDLE_TIMEOUT_MS,
    () => {
      logout().finally(() => router.replace("/welcome"));
    },
    !isAnonymous && user !== null,
  );

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
    // PR-F fix: si no hay token/cookie no llamamos al backend — redirect
    // inmediato a /welcome. Esto evita el destello de la shell con
    // sidebar/topbar antes de que el page guard reaccione, y mata el
    // mensaje "Invalid authentication credentials" que confundía al
    // visitante no autenticado.
    const token = getStoredToken();
    if (!token) {
      router.replace("/welcome");
      return;
    }
    let cancelled = false;
    getCurrentUser()
      .then((u) => {
        if (!cancelled) {
          if (u) setUser(u);
          else router.replace("/welcome");
        }
      })
      .catch(() => {
        // Cualquier fallo (incluido 401 por cookie inválida) — splash.
        if (!cancelled) router.replace("/welcome");
      })
      .finally(() => {
        if (!cancelled) setUserLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [isAnonymous, router]);

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

  // PR-F fix: hasta que el `getCurrentUser` confirme la sesión NO
  // pintamos topbar/sidebar/main. El user no autenticado verá nada
  // (en cuanto el redirect en el effect dispare) en lugar del CRM
  // con un banner de error.
  if (!userLoaded || !user) {
    return null;
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
