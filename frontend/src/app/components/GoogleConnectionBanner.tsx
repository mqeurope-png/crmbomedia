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
 *  PR-Hotfix-OAuth-Banner-Caducidad. El aviso se basa EXCLUSIVAMENTE en
 *  la caducidad del REFRESH token (reconexión real, ~7 días mientras la
 *  app OAuth no esté verificada). El access token (1h) se refresca solo
 *  y NUNCA dispara el banner.
 *
 *  - `app_verified` → silencio total (el refresh token no caduca).
 *  - status=needs_reconnect, refresh caducado, o quedan <6h → rojo.
 *  - refresh caduca en <48h → ámbar.
 *  - resto → nada.
 *
 *  El ciclo de vida de la conexión es admin-only (post PR #257): solo
 *  los admins ven este banner. Los comerciales no pueden reconectar, así
 *  que mostrárselo solo genera ruido. */
const CRITICAL_HOURS = 6;

export function GoogleConnectionBanner() {
  const [variant, setVariant] = useState<"none" | "warn" | "danger">("none");
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);

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
        // App verificada → el refresh token no caduca, sin banner.
        if (s.app_verified) {
          setVariant("none");
          return;
        }
        const hoursLeft = s.refresh_token_expires_at
          ? (new Date(s.refresh_token_expires_at).getTime() - Date.now()) /
            3_600_000
          : null;
        const critical =
          s.status === "needs_reconnect" ||
          s.refresh_token_expired === true ||
          (hoursLeft !== null && hoursLeft <= CRITICAL_HOURS);
        if (critical) {
          setVariant("danger");
          setExpiresAt(s.refresh_token_expires_at ?? null);
        } else if (s.connected && s.refresh_token_expiring_soon) {
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

  // Solo admins: el reconnect es admin-only, mostrarlo a comerciales
  // genera falsas alarmas sin acción posible.
  if (isAdmin !== true) return null;
  if (variant === "none") return null;

  const expiresLabel = expiresAt
    ? new Date(expiresAt).toLocaleString("es-ES", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;

  const message =
    variant === "danger"
      ? `La conexión Google del CRM ha caducado o está a punto de caducar${
          expiresLabel ? ` (${expiresLabel})` : ""
        }. Reconecta desde /admin/integrations para no detener el sync de emails y calendario.`
      : `La conexión Google del CRM caduca ${
          expiresLabel ? `el ${expiresLabel}` : "pronto"
        }. El admin debe reconectar desde /admin/integrations.`;

  return (
    <div className={`google-banner google-banner-${variant}`}>
      <AlertTriangle size={16} aria-hidden />
      <span>{message}</span>
      <div style={{ flex: 1 }} />
      <Link className="button small" href="/admin/integrations">
        Reconectar ahora
      </Link>
    </div>
  );
}
