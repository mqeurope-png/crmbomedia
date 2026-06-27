"use client";

import { AlertTriangle } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getCurrentUser } from "../lib/api";
import { getGoogleStatus } from "../lib/googleApi";

/** PR-OAuth-Google-Unificado. Banner persistente del estado de la
 *  conexión Google ORG (compartida por todo el equipo). Se monta en
 *  /dashboard y /account.
 *
 *  - status=needs_reconnect → banner rojo "Google desconectado".
 *  - token_expiring_soon (<48h) → banner amarillo "caduca pronto".
 *  - resto → no pinta nada.
 *
 *  El ciclo de vida de la conexión es admin-only: a los admins les
 *  ofrecemos el botón "Reconectar" (lleva a /admin/integrations); al
 *  resto del equipo, un aviso para que avisen al admin. */
export function GoogleConnectionBanner() {
  const [variant, setVariant] = useState<"none" | "warn" | "danger">("none");
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser()
      .then((u) => {
        if (!cancelled) setIsAdmin(u.role === "admin");
      })
      .catch(() => {
        if (!cancelled) setIsAdmin(false);
      });
    getGoogleStatus()
      .then((s) => {
        if (cancelled) return;
        if (s.status === "needs_reconnect") {
          setVariant("danger");
        } else if (s.connected && s.refresh_token_expiring_soon) {
          // PR-Hotfix-OAuth-Banner Bug 14. El aviso amarillo solo aplica a
          // la caducidad del REFRESH token (reconexión real). El access
          // token (token_expiring_soon) se refresca solo — no avisamos.
          setVariant("warn");
          setExpiresAt(s.refresh_token_expires_at ?? null);
        } else {
          setVariant("none");
        }
      })
      .catch(() => {
        if (!cancelled) setVariant("none");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (variant === "none") return null;

  const expiresLabel = expiresAt
    ? new Date(expiresAt).toLocaleString("es-ES", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;

  return (
    <div className={`google-banner google-banner-${variant}`}>
      <AlertTriangle size={16} aria-hidden />
      <span>
        {variant === "danger"
          ? "La conexión Google del equipo está caída. El sync de emails y calendario está detenido."
          : `La conexión Google del equipo caduca el ${expiresLabel ?? "pronto"}. Reconecta para no perder el sync.`}
      </span>
      <div style={{ flex: 1 }} />
      {isAdmin ? (
        <Link className="button small" href="/admin/integrations">
          Reconectar ahora
        </Link>
      ) : (
        <span className="small muted">Avisa a un administrador.</span>
      )}
    </div>
  );
}
