"use client";

import type { LeadClasificacion } from "../../lib/erpApi";
import { estadoLabel, estadoTone, origenDato, porcentaje } from "../../lib/leadsTextos";

/** Lo que la IA dijo de un lead, tal cual manda hoy (la corrección a mano
 *  pisa a la clasificación): interés, idioma, confianza, spam, motivo
 *  entero, resultado y modelo. Lo usan el recuadro del Resumen y cada
 *  análisis de la pestaña «Análisis IA», para que digan lo mismo. */
export function LeadClasificacionResumen({
  lead, umbral,
}: {
  lead: LeadClasificacion;
  umbral: number;
}) {
  const corregida = lead.correccion.corregida;
  const interesCorregido = corregida && lead.correccion.interes != null
    && lead.correccion.interes !== lead.interes;
  const idiomaCorregido = corregida && lead.correccion.idioma != null
    && lead.correccion.idioma !== lead.idioma;
  const spamCorregido = corregida && lead.correccion.es_spam != null
    && lead.correccion.es_spam !== lead.es_spam;
  // Todavía sin plantilla, remitente ni borrador: el workflow está en la
  // espera previa (o no ha llegado a prepararlo). Solo tiene sentido en
  // «clasificado»: el spam y lo omitido no llevan borrador.
  const borradorPendiente = lead.estado === "clasificado"
    && !lead.plantilla && !lead.remitente && !lead.borrador_id;
  const bajoUmbral = lead.confianza < umbral;
  const discrepancia = lead.discrepancia_idioma
    ? (lead.idioma_discrepancia_texto
      || (lead.idioma_formulario ? `≠ formulario ${lead.idioma_formulario.toUpperCase()}` : "idioma distinto del formulario"))
    : null;

  return (
    <dl className="contact-lead-ia-dl">
      <dt>Interés</dt>
      <dd>
        <strong>{lead.efectivo.interes_texto || "—"}</strong>
        {interesCorregido ? (
          <span className="muted small"> · corregido a mano (la IA dijo {lead.interes_texto || "—"})</span>
        ) : lead.interes_fuente ? (
          <span className="muted small"> · {origenDato(lead.interes_fuente)}</span>
        ) : null}
      </dd>

      <dt>Idioma</dt>
      <dd>
        <strong>{lead.efectivo.idioma ? lead.efectivo.idioma.toUpperCase() : "—"}</strong>
        {idiomaCorregido ? (
          <span className="muted small"> · corregido a mano (la IA dijo {lead.idioma ? lead.idioma.toUpperCase() : "—"})</span>
        ) : lead.idioma_fuente ? (
          <span className="muted small"> · {origenDato(lead.idioma_fuente)}</span>
        ) : null}
        {discrepancia ? (
          <>
            {" "}
            <span className="badge warn" title="El formulario decía un idioma y el texto está en otro">
              {discrepancia}
            </span>
          </>
        ) : null}
      </dd>

      <dt>Confianza</dt>
      <dd>
        <span
          className={`badge ${bajoUmbral ? "bad" : "active"}`}
          title={bajoUmbral
            ? `Por debajo del umbral de confianza (${porcentaje(umbral)}): conviene revisarla`
            : `Umbral de confianza: ${porcentaje(umbral)}`}
        >
          {porcentaje(lead.confianza)}
        </span>
        {bajoUmbral ? <span className="muted small"> · por debajo del umbral ({porcentaje(umbral)})</span> : null}
      </dd>

      <dt>Spam</dt>
      <dd>
        {lead.efectivo.es_spam ? <span className="badge bad">Spam</span> : <span>No</span>}
        {spamCorregido ? (
          <span className="muted small"> · corregido a mano (la IA dijo {lead.es_spam ? "spam" : "no"})</span>
        ) : null}
      </dd>

      <dt>Motivo</dt>
      <dd>
        <p className="contact-lead-ia-motivo">{lead.motivo || "—"}</p>
      </dd>

      <dt>Resultado</dt>
      <dd>
        <span className={`badge ${estadoTone(lead.estado)}`}>{estadoLabel(lead.estado)}</span>
        {lead.estado_detalle ? <span className="muted small"> · {lead.estado_detalle}</span> : null}
        {borradorPendiente ? (
          <p className="muted small contact-lead-ia-nota">Borrador pendiente de prepararse.</p>
        ) : null}
        {lead.plantilla ? <p className="small contact-lead-ia-nota">Plantilla: {lead.plantilla}</p> : null}
        {lead.remitente ? <p className="small contact-lead-ia-nota">Desde: {lead.remitente}</p> : null}
        {lead.borrador_url ? (
          <p className="small contact-lead-ia-nota"><a href={lead.borrador_url}>Abrir el borrador</a></p>
        ) : null}
      </dd>

      <dt>Modelo</dt>
      <dd>
        {lead.proveedor
          ? `${lead.proveedor}${lead.modelo ? ` · ${lead.modelo}` : ""}`
          : (lead.modelo || "—")}
      </dd>
    </dl>
  );
}
