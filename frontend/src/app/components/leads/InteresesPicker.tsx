"use client";

import type { LeadInteresOpcion } from "../../lib/erpApi";

type Props = {
  /** Los códigos, en orden: el primero es el principal. */
  value: string[];
  onChange: (next: string[]) => void;
  /** Todo el catálogo (activos e inactivos): un inactivo solo se ofrece si ya
   *  está en la lista. */
  opciones: LeadInteresOpcion[];
  /** La etiqueta accesible del desplegable del principal («Interés de Ana»). */
  labelPrincipal: string;
  /** De quién son los intereses, para las etiquetas de añadir, subir y
   *  quitar («Ana», «el lead del 9/10 08:12»). */
  sujeto: string;
  disabled?: boolean;
  /** Sin principal permitido (una combinación nueva del mapa, que empieza
   *  vacía). */
  permitirVacio?: boolean;
};

/** Varios intereses, ordenados: el principal en un desplegable (el caso de
 *  uno solo es el de siempre, un desplegable) y los demás como chips con
 *  «subir» y «quitar», más un desplegable para añadir otro. Lo usan la
 *  corrección a mano (ERP · Leads y la pestaña «Análisis IA» de la ficha) y
 *  las combinaciones del mapa de plantillas. */
export function InteresesPicker({
  value, onChange, opciones, labelPrincipal, sujeto, disabled = false, permitirVacio = false,
}: Props) {
  const etiqueta = (codigo: string) => opciones.find((o) => o.id === codigo)?.label ?? codigo;
  const principal = value[0] ?? "";
  const secundarios = value.slice(1);
  const ofrecibles = opciones.filter((o) => o.activo !== false || value.includes(o.id));
  const disponibles = ofrecibles.filter((o) => !value.includes(o.id));

  const cambiarPrincipal = (codigo: string) => {
    if (!codigo) {
      if (permitirVacio) onChange(secundarios);
      return;
    }
    onChange([codigo, ...secundarios.filter((c) => c !== codigo)]);
  };
  const anadir = (codigo: string) => {
    if (codigo && !value.includes(codigo)) onChange([...value, codigo]);
  };
  const quitar = (indice: number) => onChange(value.filter((_, i) => i !== indice));
  const subir = (indice: number) => {
    if (indice <= 0) return;
    const next = [...value];
    [next[indice - 1], next[indice]] = [next[indice], next[indice - 1]];
    onChange(next);
  };

  return (
    <div className="lead-intereses">
      <select
        aria-label={labelPrincipal}
        value={principal}
        disabled={disabled}
        onChange={(e) => cambiarPrincipal(e.target.value)}
      >
        {!principal || permitirVacio ? <option value="">—</option> : null}
        {ofrecibles
          .filter((o) => o.id === principal || !secundarios.includes(o.id))
          .map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}{o.activo === false ? " (desactivado)" : ""}
            </option>
          ))}
      </select>
      {secundarios.length > 0 ? (
        <span className="lead-intereses-principal" aria-hidden>principal</span>
      ) : null}
      {secundarios.map((codigo, i) => {
        const indice = i + 1;
        return (
          <span key={codigo} className="lead-intereses-chip">
            <span>{etiqueta(codigo)}</span>
            <button
              type="button"
              aria-label={`Subir ${etiqueta(codigo)} en los intereses de ${sujeto}`}
              title="Subir (el primero es el principal)"
              disabled={disabled}
              onClick={() => subir(indice)}
            >
              ↑
            </button>
            <button
              type="button"
              aria-label={`Quitar ${etiqueta(codigo)} de los intereses de ${sujeto}`}
              title="Quitar"
              disabled={disabled}
              onClick={() => quitar(indice)}
            >
              ×
            </button>
          </span>
        );
      })}
      {disponibles.length > 0 && principal ? (
        <select
          className="lead-intereses-anadir"
          aria-label={`Añadir interés a ${sujeto}`}
          value=""
          disabled={disabled}
          onChange={(e) => anadir(e.target.value)}
        >
          <option value="">+ otro interés…</option>
          {disponibles.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
      ) : null}
    </div>
  );
}
