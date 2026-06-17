"use client";

/**
 * Placeholder — el dashboard BoHub (PR-C) pide un widget "🔥
 * Oportunidades calientes" que ranquee leads por engagement reciente.
 * Sin endpoint dedicado todavía: pintamos el chrome del widget y un
 * mensaje "Próximamente" para no dejar un hueco vacío en el grid.
 *
 * TODO Sprint Backend: endpoint `GET /api/dashboard/hot-opportunities`
 * que devuelva top-5 contactos abiertos con scoring = email opens
 * recientes + tareas activas + pipeline stage probability.
 */
import { Flame } from "lucide-react";

export function HotOpportunitiesWidget() {
  return (
    <article className="card widget widget-placeholder">
      <header className="section-title">
        <h2>
          <Flame size={16} aria-hidden /> Oportunidades calientes
        </h2>
      </header>
      <div className="widget-empty">
        <p className="muted small">
          Pronto: ranking automático de leads por engagement
          (emails abiertos + tareas activas + stage del pipeline).
        </p>
      </div>
    </article>
  );
}
