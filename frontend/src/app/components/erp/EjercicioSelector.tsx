"use client";

import { useEffect, useState } from "react";
import { getEjercicios, type Ejercicios } from "../../lib/erpApi";

/** El ejercicio de la pantalla: arranca en el en curso (el de FACTUSOL,
 *  Configuración ERP) y se puede cambiar a uno anterior. `ejercicio` es el
 *  elegido; `null` mientras no se sabe cuál es el en curso. Si no se puede
 *  preguntar, el año natural (y se dice en el selector). */
export function useEjercicio() {
  const [info, setInfo] = useState<Ejercicios | null>(null);
  const [elegido, setElegido] = useState<string | null>(null);
  useEffect(() => {
    let vivo = true;
    getEjercicios()
      .then((r) => { if (vivo) setInfo(r); })
      .catch(() => {
        const anio = new Date().getFullYear();
        if (vivo) setInfo({ en_curso: String(anio), ejercicios: [String(anio), String(anio - 1)] });
      });
    return () => { vivo = false; };
  }, []);
  const ejercicio = elegido ?? info?.en_curso ?? null;
  return {
    info,
    ejercicio,
    enCurso: info?.en_curso ?? null,
    anterior: !!info && !!ejercicio && ejercicio !== info.en_curso,
    setEjercicio: setElegido,
  };
}

/** El selector y, si no es el en curso, el aviso: lo que se ve es de otro
 *  ejercicio y aquí no se registra nada. */
export function EjercicioSelector({
  info, ejercicio, onChange, que = "Lo que ves",
}: {
  info: Ejercicios | null;
  ejercicio: string | null;
  onChange: (ejercicio: string) => void;
  /** Cómo empieza el aviso («Los documentos», «Los movimientos»…). */
  que?: string;
}) {
  if (!info || !ejercicio) return null;
  const anterior = ejercicio !== info.en_curso;
  return (
    <>
      <label className={`field erp-ejercicio ${anterior ? "is-anterior" : ""}`}>
        <span>Ejercicio</span>
        <select
          aria-label="Ejercicio"
          value={ejercicio}
          onChange={(e) => onChange(e.target.value)}
        >
          {info.ejercicios.map((e) => (
            <option key={e} value={e}>{e === info.en_curso ? `${e} (en curso)` : e}</option>
          ))}
        </select>
      </label>
      {anterior ? (
        <p className="form-warning erp-ejercicio-aviso" role="alert">
          <strong>Ejercicio {ejercicio}, no el en curso ({info.en_curso}).</strong>{" "}
          {que} son de {ejercicio}. Es solo consulta: los cobros se registran en el ejercicio
          en curso.{" "}
          <button type="button" className="button small secondary"
            onClick={() => onChange(info.en_curso)}>
            Volver a {info.en_curso}
          </button>
        </p>
      ) : null}
    </>
  );
}
