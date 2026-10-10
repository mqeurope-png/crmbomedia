"use client";

import { useState } from "react";
import type { LeadClasificacion } from "../../lib/erpApi";
import { listaIntereses, listasIguales } from "../../lib/leadsMapa";
import { estadoLabel, estadoTone, origenDato, porcentaje } from "../../lib/leadsTextos";

/** Semáforo de la confianza frente al umbral configurado (Configuración
 *  ERP → Respuesta a leads; 0,7 hoy): rojo por debajo, aviso hasta una
 *  décima por encima, verde después. */
export function nivelConfianza(
  confianza: number, umbral: number,
): "baja" | "justa" | "alta" {
  if (confianza < umbral) return "baja";
  if (confianza < umbral + 0.1) return "justa";
  return "alta";
}

const TONO_CONFIANZA: Record<ReturnType<typeof nivelConfianza>, string> = {
  baja: "bad", justa: "warn", alta: "active",
};

/** Más o menos dos líneas a ancho de tarjeta (unos 60 caracteres por
 *  línea): por encima, el Resumen recorta el motivo y ofrece «más». */
export const MOTIVO_RESUMEN_MAX = 140;

type Props = {
  lead: LeadClasificacion;
  umbral: number;
  /** `resumen` (recuadro del Resumen): motivo en dos líneas con «más».
   *  `completo` (pestaña «Análisis IA»): el motivo entero. */
  modo?: "resumen" | "completo";
};

/** Lo que la IA dijo de un lead, tal cual manda hoy (la corrección a mano
 *  pisa a la clasificación), con jerarquía: el interés manda; la confianza
 *  con semáforo; el motivo legible; spam solo cuando lo es; el modelo al
 *  pie. Lo usan el recuadro del Resumen y cada análisis de la pestaña,
 *  para que digan lo mismo. */
export function LeadClasificacionResumen({ lead, umbral, modo = "completo" }: Props) {
  const [desplegado, setDesplegado] = useState(false);
  const corregida = lead.correccion.corregida;
  // Un lead puede querer varias cosas: el principal manda (grande) y los
  // demás van en chips pequeños, en su orden.
  const efectivos = listaIntereses(lead.efectivo.intereses, lead.efectivo.interes);
  const originales = listaIntereses(lead.intereses, lead.interes);
  const etiquetasEfectivas = lead.efectivo.intereses_etiquetas?.length === efectivos.length
    ? lead.efectivo.intereses_etiquetas
    : efectivos;
  const secundarios = etiquetasEfectivas.slice(1);
  const interesCorregido = corregida && (
    lead.correccion.intereses?.length
      ? !listasIguales(lead.correccion.intereses, originales)
      : lead.correccion.interes != null && lead.correccion.interes !== lead.interes
  );
  const dijoLaIa = lead.intereses_texto || lead.interes_texto || "—";
  const idiomaCorregido = corregida && lead.correccion.idioma != null
    && lead.correccion.idioma !== lead.idioma;
  const spamCorregido = corregida && lead.correccion.es_spam != null
    && lead.correccion.es_spam !== lead.es_spam;
  // Todavía sin plantilla, remitente ni borrador: el workflow está en la
  // espera previa (o no ha llegado a prepararlo). Solo tiene sentido en
  // «clasificado»: el spam y lo omitido no llevan borrador.
  const borradorPendiente = lead.estado === "clasificado"
    && !lead.plantilla && !lead.remitente && !lead.borrador_id;
  const nivel = nivelConfianza(lead.confianza, umbral);
  const discrepancia = lead.discrepancia_idioma
    ? (lead.idioma_discrepancia_texto
      || (lead.idioma_formulario
        ? `≠ formulario ${lead.idioma_formulario.toUpperCase()}`
        : "idioma distinto del formulario"))
    : null;
  const motivo = (lead.motivo || "").trim();
  const recortable = modo === "resumen" && motivo.length > MOTIVO_RESUMEN_MAX;
  const recortado = recortable && !desplegado;
  const umbralTexto = nivel === "baja"
    ? `por debajo del umbral (${porcentaje(umbral)})`
    : nivel === "justa"
      ? `justo por encima del umbral (${porcentaje(umbral)})`
      : `umbral ${porcentaje(umbral)}`;
  const hayBorrador = borradorPendiente || lead.plantilla || lead.remitente || lead.borrador_url;

  return (
    <div className={`lead-ia lead-ia-${modo}`}>
      <div className="lead-ia-cabecera">
        <p className="lead-ia-interes">
          <span className="lead-ia-interes-valor">
            {lead.efectivo.interes_texto || "Sin interés claro"}
          </span>
          {secundarios.length > 0 ? (
            <span className="lead-ia-secundarios" aria-label="También pide">
              {secundarios.map((e) => (
                <span key={e} className="lead-ia-chip lead-ia-secundario">+ {e}</span>
              ))}
            </span>
          ) : null}
          {interesCorregido ? (
            <span className="lead-ia-origen">
              corregido a mano (la IA dijo {dijoLaIa})
            </span>
          ) : lead.interes_fuente ? (
            <span className="lead-ia-origen">{origenDato(lead.interes_fuente)}</span>
          ) : null}
        </p>
        {lead.efectivo.es_spam ? (
          <span className="lead-ia-spam" title={spamCorregido ? "Marcado como spam a mano" : undefined}>
            SPAM{spamCorregido ? " · a mano" : ""}
          </span>
        ) : spamCorregido ? (
          <span className="lead-ia-origen">la IA lo dio por spam; corregido a mano</span>
        ) : null}
      </div>

      <ul className="lead-ia-datos">
        <li>
          <span className="lead-ia-dato-etiqueta">Idioma</span>
          <strong>{lead.efectivo.idioma ? lead.efectivo.idioma.toUpperCase() : "—"}</strong>
          {idiomaCorregido ? (
            <span className="lead-ia-origen">
              corregido a mano (la IA dijo {lead.idioma ? lead.idioma.toUpperCase() : "—"})
            </span>
          ) : lead.idioma_fuente ? (
            <span className="lead-ia-origen">{origenDato(lead.idioma_fuente)}</span>
          ) : null}
          {discrepancia ? (
            <span className="badge warn" title="El formulario decía un idioma y el texto está en otro">
              {discrepancia}
            </span>
          ) : null}
        </li>
        <li>
          <span className="lead-ia-dato-etiqueta">Confianza</span>
          <span
            className={`badge ${TONO_CONFIANZA[nivel]} lead-ia-confianza is-${nivel}`}
            title={nivel === "baja"
              ? `Por debajo del umbral de confianza (${porcentaje(umbral)}): conviene revisarla`
              : `Umbral de confianza: ${porcentaje(umbral)}`}
          >
            {porcentaje(lead.confianza)}
          </span>
          <span className="lead-ia-origen">{umbralTexto}</span>
        </li>
        <li>
          <span className="lead-ia-dato-etiqueta">Resultado</span>
          <span className={`badge ${estadoTone(lead.estado)}`}>{estadoLabel(lead.estado)}</span>
          {lead.estado_detalle ? <span className="lead-ia-origen">{lead.estado_detalle}</span> : null}
        </li>
      </ul>

      {motivo ? (
        <div className="lead-ia-motivo-bloque">
          <p className={`lead-ia-motivo${recortado ? " is-recortado" : ""}`}>{motivo}</p>
          {recortable ? (
            <button
              type="button"
              className="lead-ia-mas"
              aria-expanded={!recortado}
              onClick={() => setDesplegado((v) => !v)}
            >
              {recortado ? "más" : "menos"}
            </button>
          ) : null}
        </div>
      ) : null}

      {hayBorrador ? (
        <p className="lead-ia-borrador small">
          {borradorPendiente ? <span>Borrador pendiente de prepararse.</span> : null}
          {lead.plantilla ? <span>Plantilla: {lead.plantilla}</span> : null}
          {lead.remitente ? <span>Desde: {lead.remitente}</span> : null}
          {lead.borrador_url ? <a href={lead.borrador_url}>Abrir el borrador</a> : null}
        </p>
      ) : null}

      {lead.proveedor || lead.modelo ? (
        <p className="lead-ia-pie">
          {lead.proveedor
            ? `${lead.proveedor}${lead.modelo ? ` · ${lead.modelo}` : ""}`
            : lead.modelo}
        </p>
      ) : null}
    </div>
  );
}
