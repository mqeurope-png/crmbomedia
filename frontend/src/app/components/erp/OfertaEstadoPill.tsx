import { OFERTA_ESTADO_LABELS, type OfertaEstado } from "../../lib/erpApi";

/** Tono de cada estado sobre las clases `.badge.{muted,info,warn,ok}` de
 *  styles.css: borrador gris, enviada azul, caducada ámbar, convertida verde. */
const TONE: Record<OfertaEstado, string> = {
  borrador: "muted",
  enviada: "info",
  caducada: "warn",
  convertida: "ok",
};

/** Pastilla del estado EFECTIVO de una oferta (la palabra siempre, no solo el
 *  color). Con `vencida` añade la nota «· vencida» cuando el estado no lo dice
 *  ya por sí mismo (una caducada ya lo es; una convertida ya no importa). */
export function OfertaEstadoPill({ estado, vencida = false }: {
  estado: OfertaEstado;
  vencida?: boolean;
}) {
  const label = OFERTA_ESTADO_LABELS[estado] ?? estado;
  const nota = vencida && estado !== "caducada" && estado !== "convertida";
  return (
    <span
      className={`badge ${TONE[estado] ?? "muted"}`}
      title={vencida ? "La fecha de validez ya pasó." : undefined}
    >
      {label}
      {nota ? <span className="muted small"> · vencida</span> : null}
    </span>
  );
}
