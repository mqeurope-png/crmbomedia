"use client";

import { ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { BoHubLogo } from "../components/branding/BoHubLogo";
import { getCurrentUser } from "../lib/api";

/**
 * Pre-login splash. Rebranding Sprint PR-B.
 *
 * El root layout marca esta ruta como anónima (`AppShell.ANONYMOUS_
 * ROUTES`) para que NO renderice ni topbar ni sidebar. Si el operador
 * llega aquí YA autenticado lo mandamos al dashboard — la URL es
 * pública sin sesión, pero no tiene sentido mostrar el splash a quien
 * ya está dentro.
 */
export default function WelcomePage() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser()
      .then((user) => {
        if (cancelled) return;
        if (user) router.replace("/");
      })
      .catch(() => {
        // 401 sin sesión es el camino esperado — quédate en el splash.
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  // Mientras chequeamos sesión renderizamos el splash igual: si la
  // request termina con éxito (sesión válida) el `router.replace`
  // sustituye la página. Mostrar un loader vacío sería peor visualmente.

  return (
    <main className="welcome-page" aria-busy={checking}>
      <section className="welcome-pane welcome-pane-copy">
        <div className="welcome-logo">
          <BoHubLogo variant="horizontal" size={64} />
        </div>
        <h1 className="welcome-title">
          Bienvenido a <span className="welcome-title-brand">BoHub</span>{" "}
          <span className="welcome-title-suffix">CRM</span>
        </h1>
        <p className="welcome-lead">
          Leads, campañas, tareas y comunicación con clientes en un solo lugar.
        </p>
        <div className="welcome-cta">
          <Link href="/login" className="button welcome-cta-primary">
            🔒 Acceder a tu cuenta
          </Link>
          <a
            href="mailto:eduard@bomedia.net?subject=Demo%20BoHub%20CRM"
            className="button secondary welcome-cta-secondary"
          >
            Solicitar una demo
          </a>
        </div>
        <p className="welcome-trust">
          <ShieldCheck size={18} aria-hidden />
          Seguro. Fiable. Preparado para crecer.
        </p>
      </section>
      <section className="welcome-pane welcome-pane-preview" aria-hidden>
        {/* Mock visual reducido del dashboard. Datos hardcoded del
            mockup — NO live. El objetivo es transmitir el look-and-feel
            sin tener que servir una screenshot. */}
        <div className="welcome-preview-frame">
          <div className="welcome-preview-sidebar">
            <div className="welcome-preview-brand">
              <BoHubLogo variant="horizontal" size={22} />
            </div>
            <ul className="welcome-preview-nav">
              <li className="is-active">Panel</li>
              <li>Leads</li>
              <li>Embudo</li>
              <li>Campañas</li>
              <li>Tareas</li>
              <li>Contactos</li>
              <li>Correo</li>
              <li>Informes</li>
            </ul>
          </div>
          <div className="welcome-preview-main">
            <div className="welcome-preview-topbar">
              <span className="welcome-preview-topbar-title">Panel</span>
              <span className="welcome-preview-topbar-meta">
                12 may. 2024 – 18 may. 2024
              </span>
            </div>
            <div className="welcome-preview-kpis">
              {[
                { label: "Nuevos leads", value: "248", trend: "↑ 18%" },
                {
                  label: "Valor del embudo",
                  value: "€124,580",
                  trend: "↑ 20%",
                },
                { label: "Ventas ganadas", value: "36", trend: "↑ 12%" },
                { label: "Tareas abiertas", value: "18", trend: "↓ 8%" },
                {
                  label: "Correos enviados",
                  value: "1,256",
                  trend: "↑ 15%",
                },
              ].map((kpi) => (
                <div key={kpi.label} className="welcome-preview-kpi">
                  <span className="welcome-preview-kpi-label">{kpi.label}</span>
                  <span className="welcome-preview-kpi-value">{kpi.value}</span>
                  <span className="welcome-preview-kpi-trend">{kpi.trend}</span>
                </div>
              ))}
            </div>
            <div className="welcome-preview-grid">
              <div className="welcome-preview-card">
                <h4>Resumen de actividad</h4>
                <ul>
                  <li>
                    <span>Nuevos leads este mes</span>
                    <strong>248</strong>
                  </li>
                  <li>
                    <span>Leads convertidos</span>
                    <strong>64</strong>
                  </li>
                  <li>
                    <span>Oportunidades ganadas</span>
                    <strong>36</strong>
                  </li>
                  <li>
                    <span>Ingresos generados</span>
                    <strong>€124,580</strong>
                  </li>
                </ul>
              </div>
              <div className="welcome-preview-card">
                <h4>Tareas</h4>
                <ul>
                  <li>Seguimiento con Acme Corp</li>
                  <li>Propuesta para Servicios Avanzados</li>
                  <li>Llamada con Laura García</li>
                  <li>Enviar informe de campaña</li>
                </ul>
              </div>
              <div className="welcome-preview-card welcome-preview-card-wide">
                <h4>Mejores campañas</h4>
                <ul className="welcome-preview-bars">
                  {[
                    { name: "Promoción de Primavera", pct: 90, value: "124" },
                    { name: "Lanzamiento de Producto", pct: 72, value: "98" },
                    { name: "Webinar de Mayo", pct: 55, value: "76" },
                    { name: "Boletín 05/24", pct: 45, value: "64" },
                  ].map((b) => (
                    <li key={b.name}>
                      <span>{b.name}</span>
                      <span
                        className="welcome-preview-bar"
                        style={{ ["--pct" as string]: `${b.pct}%` }}
                      />
                      <strong>{b.value}</strong>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
