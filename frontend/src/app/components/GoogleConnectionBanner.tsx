"use client";

import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";
import { getGoogleStatus, startGoogleConnect } from "../lib/googleApi";

/** PR-OAuth-Permisos-Admin Items 9 + 12. Banner persistente del estado
 *  de la conexión Gmail del current_user. Se monta en /dashboard y
 *  /account.
 *
 *  - status=needs_reconnect → banner rojo "Gmail desconectado".
 *  - token_expiring_soon (<48h) → banner amarillo "caduca pronto".
 *  - resto → no pinta nada.
 *
 *  El botón "Reconectar ahora" lanza el flow OAuth; tras volver, el
 *  status se relee y el banner desaparece. */
export function GoogleConnectionBanner() {
  const [variant, setVariant] = useState<"none" | "warn" | "danger">("none");
  const [expiresAt, setExpiresAt] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getGoogleStatus()
      .then((s) => {
        if (cancelled) return;
        if (s.status === "needs_reconnect") {
          setVariant("danger");
        } else if (s.connected && s.token_expiring_soon) {
          setVariant("warn");
          setExpiresAt(s.token_expires_at ?? null);
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
          ? "Tu Gmail está desconectado. El sync de emails está detenido."
          : `Tu conexión Gmail caduca el ${expiresLabel ?? "pronto"}. Reconecta para no perder el sync.`}
      </span>
      <div style={{ flex: 1 }} />
      <button
        type="button"
        className="button small"
        onClick={() => {
          void startGoogleConnect();
        }}
      >
        Reconectar ahora
      </button>
    </div>
  );
}
