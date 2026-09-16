"use client";

import { useCallback, useEffect, useState } from "react";
import { getCompany, type Company } from "../../lib/companiesApi";
import { extractErrorMessage } from "../../lib/errors";
import { CompanyFactusolPanel } from "./CompanyFactusolPanel";

/** Lote 3 · ficha — el cliente FACTUSOL del pedido, también en los pedidos WEB.
 *
 *  Antes la vista/edición del cliente FACTUSOL (ver el nº F_CLI, «Traer datos»,
 *  completar el NIF que falta…) solo vivía en la ficha de empresa; en la ficha
 *  del pedido web no había forma de completarlo. Este envoltorio REUTILIZA el
 *  mismo `CompanyFactusolPanel` de la ficha de empresa: resuelve la empresa por
 *  `company_id` y le pasa el flujo confirmado de escritura en F_CLI tal cual.
 *  Nunca inventa datos del cliente. Sin empresa vinculada (raro) avisa discreto. */
export function OrderFactusolClientPanel({
  companyId,
  onChanged,
}: {
  companyId: string | null;
  /** Tras vincular / traer datos: la ficha recarga el pedido (el nº F_CLI y el
   *  régimen que se muestran arriba pueden haber cambiado). */
  onChanged?: () => void;
}) {
  const [company, setCompany] = useState<Company | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!companyId) return;
    getCompany(companyId)
      .then((c) => { setCompany(c); setError(null); })
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la empresa del pedido.")));
  }, [companyId]);

  useEffect(() => { reload(); }, [reload]);

  if (!companyId) {
    return (
      <section className="erp-flow-panel" aria-label="Cliente FACTUSOL">
        <h3>Cliente FACTUSOL</h3>
        <p className="muted small">
          Este pedido no tiene empresa vinculada en el CRM: vincula una empresa
          para ver y completar su cliente FACTUSOL.
        </p>
      </section>
    );
  }

  if (!company) {
    return (
      <section className="erp-flow-panel" aria-label="Cliente FACTUSOL">
        <h3>Cliente FACTUSOL</h3>
        <p className="muted small">{error ?? "Cargando el cliente FACTUSOL…"}</p>
      </section>
    );
  }

  return (
    <CompanyFactusolPanel
      company={company}
      onLinked={() => { reload(); onChanged?.(); }}
      onPulled={() => { reload(); onChanged?.(); }}
    />
  );
}
