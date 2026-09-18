"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { getCurrentUser, getStoredToken, type User } from "../../lib/api";

const SAT_ROLES = ["admin", "manager", "pedidos", "sat"];

/** Layout ligero de la Cola SAT (full-bleed en AppShell): topbar mínima +
 *  botón Volver, sin sidebar. Guarda la sesión por su cuenta (AppShell no
 *  envuelve las rutas full-bleed). Optimizado para tablet vertical/móvil. */
export default function SatLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  // En el modo trabajo de un pedido (`/erp/sat/[id]`) se ofrece volver a la
  // lista de la Cola SAT, además de «Volver al CRM».
  const onOrderView = pathname !== "/erp/sat" && pathname.startsWith("/erp/sat/");
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getStoredToken()) {
      router.replace("/welcome");
      return;
    }
    getCurrentUser()
      .then((u) => {
        if (!u || !SAT_ROLES.includes(u.role)) {
          router.replace("/");
          return;
        }
        setUser(u);
      })
      .catch(() => router.replace("/welcome"))
      .finally(() => setReady(true));
  }, [router]);

  if (!ready || !user) return null;

  return (
    <div className="sat-shell">
      <header className="sat-topbar">
        <span className="sat-topbar-title">🔧 Cola SAT</span>
        <span className="sat-topbar-actions">
          {onOrderView ? (
            <Link href="/erp/sat" className="sat-topbar-back">← Volver a la Cola SAT</Link>
          ) : null}
          <Link href="/erp/orders" className="sat-topbar-back">Volver al CRM</Link>
        </span>
      </header>
      <main className="sat-main">{children}</main>
    </div>
  );
}
