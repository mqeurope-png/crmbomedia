"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { getCurrentUser, type User } from "../lib/api";
import { resolveVisibleNav } from "../lib/appNav";
import { getSatQueue, listPendingApproval } from "../lib/erpApi";

/** ERP-F2 — INICIO del ERP (`/erp`). Es la pantalla de entrada del perfil de
 *  ERP: accesos a lo que usa a diario (pedidos pendientes, cola de taller,
 *  documentos) en vez del dashboard comercial del CRM. Misma casa, mismo
 *  diseño. */
export default function ErpHome() {
  const [user, setUser] = useState<User | null>(null);
  const [pending, setPending] = useState<number | null>(null);
  const [satCount, setSatCount] = useState<number | null>(null);
  // Aviso cuando se ha redirigido aquí desde una URL del CRM (AppShell añade
  // `?desde=`), para que quede claro por qué no ha aterrizado donde tecleó.
  const [blockedFrom, setBlockedFrom] = useState<string | null>(null);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
    // Contadores best-effort: si el rol no puede o falla, se ocultan.
    listPendingApproval()
      .then((rows) => setPending(rows.length))
      .catch(() => setPending(null));
    getSatQueue()
      .then((q) => setSatCount(q.preparing.length + q.ready_for_pickup.length))
      .catch(() => setSatCount(null));
    try {
      const params = new URLSearchParams(window.location.search);
      const desde = params.get("desde");
      if (desde) {
        setBlockedFrom(desde);
        window.history.replaceState({}, "", "/erp");
      }
    } catch {
      // sin acceso a location: sin aviso, no pasa nada.
    }
  }, []);

  const firstName =
    user?.full_name?.split(" ")[0] || user?.full_name || "operador";
  // Las tarjetas de acceso salen de la MISMA definición de menú (ámbito ERP),
  // filtradas por el rol — así el inicio y el menú nunca se desincronizan.
  const cards = resolveVisibleNav(user, "erp");

  return (
    <main className="shell shell-wide">
      <PageHeader
        title={`Hola, ${firstName}`}
        eyebrow="BoHub ERP"
        description="Pedidos, documentos y taller — tu día a día en el ERP."
      />

      {blockedFrom ? (
        <p className="form-info" role="status">
          Esa sección (<code>{blockedFrom}</code>) es del CRM. Tu perfil trabaja
          en el ERP; te hemos traído al inicio del ERP.
        </p>
      ) : null}

      <section className="erp-home-widgets">
        {pending !== null ? (
          <Link href="/erp/orders/pending-approval" className="erp-home-stat">
            <span className="erp-home-stat-value">{pending}</span>
            <span className="erp-home-stat-label">Pedidos pendientes de aprobación</span>
          </Link>
        ) : null}
        {satCount !== null ? (
          <Link href="/erp/sat" className="erp-home-stat">
            <span className="erp-home-stat-value">{satCount}</span>
            <span className="erp-home-stat-label">En cola de taller (SAT)</span>
          </Link>
        ) : null}
      </section>

      <section className="erp-home-grid">
        {cards.map((item) => {
          const Icon = item.icon;
          return (
            <Link key={item.href} href={item.href} className="erp-home-card">
              <Icon size={22} aria-hidden />
              <span className="erp-home-card-label">{item.label}</span>
            </Link>
          );
        })}
      </section>
    </main>
  );
}
