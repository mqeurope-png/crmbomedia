"use client";

import type { BankEjercicios } from "../../lib/erpApi";

/** El valor de «Todos los años». */
export const TODOS_LOS_ANIOS = "todos";

/** «2.583» (en castellano el navegador no agrupa los de cuatro cifras). */
const n = (x: number) => String(x).replace(/\B(?=(\d{3})+(?!\d))/g, ".");

/** El año de los movimientos en Conciliación. A diferencia de Documentos (que
 *  se ciñe a los ejercicios de FACTUSOL), aquí mandan los movimientos que hay:
 *  salen TODOS los años con movimientos, cada uno con su recuento, el ejercicio
 *  en curso aunque no tenga, y «Todos los años». Ninguno queda fuera. Un año
 *  que no es el en curso se nota y es solo consulta. */
export function AnioBancoSelector({
  banco, ejercicio, onChange,
}: {
  banco: BankEjercicios | null;
  ejercicio: string | null;
  onChange: (anio: string) => void;
}) {
  if (!banco || !ejercicio) return null;
  const anios = banco.anios.some((a) => a.anio === banco.en_curso)
    ? banco.anios
    : [{ anio: banco.en_curso, movimientos: 0, pending: 0, reconciled: 0, discarded: 0,
         pending_amount: 0 }, ...banco.anios].sort((a, b) => b.anio.localeCompare(a.anio));
  const todos = ejercicio === TODOS_LOS_ANIOS;
  const anterior = !todos && ejercicio !== banco.en_curso;
  return (
    <>
      <label className={`field erp-ejercicio ${anterior ? "is-anterior" : ""}`}>
        <span>Ejercicio</span>
        <select aria-label="Ejercicio" value={ejercicio} onChange={(e) => onChange(e.target.value)}>
          <option value={TODOS_LOS_ANIOS}>
            Todos los años · {n(banco.total.movimientos)} movimientos · {n(banco.total.pending)} por conciliar
          </option>
          {anios.map((a) => (
            <option key={a.anio} value={a.anio}>
              {a.anio}{a.anio === banco.en_curso ? " (en curso)" : ""} · {n(a.movimientos)} movimientos
              {" "}· {n(a.pending)} por conciliar
            </option>
          ))}
        </select>
      </label>
      {anterior ? (
        <p className="form-warning erp-ejercicio-aviso" role="alert">
          <strong>Ejercicio {ejercicio}, no el en curso ({banco.en_curso}).</strong>{" "}
          Los movimientos son de {ejercicio}. Es solo consulta: los cobros se registran en el
          ejercicio en curso.{" "}
          <button type="button" className="button small secondary"
            onClick={() => onChange(banco.en_curso)}>
            Volver a {banco.en_curso}
          </button>
        </p>
      ) : null}
    </>
  );
}
