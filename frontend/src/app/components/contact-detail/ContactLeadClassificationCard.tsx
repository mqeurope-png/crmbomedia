"use client";

import { ArrowUpRight, Brain } from "lucide-react";
import { useEffect, useState } from "react";
import { formatBackendDateTimeCompact } from "../../lib/dates";
import {
  listContactLeadClasificaciones,
  type LeadClasificacion,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { fuenteTexto } from "../../lib/leadsTextos";
import { LeadClasificacionResumen } from "./LeadClasificacionResumen";

type Props = {
  contactId: string;
  /** Lleva a la pestaña «Análisis IA» (consulta entera, historial, corrección). */
  onSeeAll?: () => void;
};

/** Recuadro del Resumen con la ÚLTIMA clasificación del contacto. Si el
 *  contacto no tiene ninguna (no entró como lead por formulario ni por
 *  AgileCRM con la respuesta a leads activa) no se pinta nada: un recuadro
 *  vacío solo quitaría sitio a los que sí tienen datos. */
export function ContactLeadClassificationCard({ contactId, onSeeAll }: Props) {
  const [ultima, setUltima] = useState<LeadClasificacion | null>(null);
  const [total, setTotal] = useState(0);
  const [umbral, setUmbral] = useState(0.7);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let vivo = true;
    listContactLeadClasificaciones(contactId)
      .then((r) => {
        if (!vivo) return;
        setUltima(r.items[0] ?? null);
        setTotal(r.total);
        setUmbral(r.umbral_confianza);
        setError(null);
      })
      .catch((e) => {
        if (vivo) setError(extractErrorMessage(e, "No se pudo cargar el análisis de la IA."));
      });
    return () => {
      vivo = false;
    };
  }, [contactId]);

  if (!ultima && !error) return null;

  return (
    <article className="card contact-summary-card contact-lead-ia-card">
      <header className="contact-summary-card-header">
        <h3>
          <Brain size={14} aria-hidden /> Análisis de la IA
        </h3>
      </header>
      {error ? (
        <p className="form-error" role="alert">{error}</p>
      ) : ultima ? (
        <>
          <p className="muted small">
            Lead del {formatBackendDateTimeCompact(ultima.lead_at ?? ultima.creado)} ·{" "}
            {fuenteTexto(ultima.fuente, ultima.web, ultima.cuenta_agile)}
            {total > 1 ? ` · ${total} análisis, este es el último` : ""}
          </p>
          <LeadClasificacionResumen lead={ultima} umbral={umbral} modo="resumen" />
        </>
      ) : null}
      {onSeeAll && ultima ? (
        <button type="button" className="contact-summary-link" onClick={onSeeAll}>
          Ver el análisis completo <ArrowUpRight size={12} aria-hidden />
        </button>
      ) : null}
    </article>
  );
}
