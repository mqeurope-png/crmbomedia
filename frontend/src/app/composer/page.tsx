"use client";

import { useEffect } from "react";

/**
 * Loader / redirect to the embedded original Composer.
 *
 * The Bomedia Composer lives at `/public/composer/index.html` and
 * boots its own React/Babel/JSX bundle from `/public/composer/src/`.
 * This Next.js page does three things:
 *
 *   1. Reads the CRM JWT from `localStorage["crmbomedia_access_token"]`
 *      and stashes it into a sessionStorage slot the embedded
 *      `app-supabase.jsx` adapter reads (so its `fetch()` calls
 *      include `Authorization: Bearer <jwt>` without any inter-page
 *      cookie tricks).
 *   2. Redirects to the embed via `window.location.replace` so the
 *      Composer takes over the full document — no iframe, no
 *      Next.js client overhead, native scrolling.
 *   3. If no JWT is present (user navigated directly without
 *      logging in first), routes back to `/login`.
 */
export default function ComposerEmbedLoader() {
  useEffect(() => {
    try {
      const token =
        typeof window !== "undefined"
          ? window.localStorage.getItem("crmbomedia_access_token")
          : null;
      if (!token) {
        window.location.replace("/login");
        return;
      }
      // The embed's bootstrap reads this slot on first paint.
      window.sessionStorage.setItem("composer_crm_jwt", token);
    } catch {
      // localStorage / sessionStorage blocked — fall through to the
      // embed which will surface its own error if it can't read auth.
    }
    window.location.replace("/composer/index.html");
  }, []);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100vh",
        color: "#6b6960",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      Cargando Composer…
    </div>
  );
}
