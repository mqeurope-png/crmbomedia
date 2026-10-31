"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

/** El estado del resumen ERP por empresa (1/11/2026): de cuándo son los datos
 *  de «Empresa, parque y compras», si el último recálculo falló (se dice, no
 *  se enseñan números viejos con cara de frescos), y los recuentos de las
 *  etiquetas automáticas, con las quitadas a mano. */

export type ResumenErp = {
  calculado: boolean;
  ok_at: string | null;
  fallo_at: string | null;
  fallo: string | null;
  duracion_ms?: number | null;
  empresas?: number | null;
  ya_cliente_empresas?: number | null;
  ya_cliente_contactos?: number | null;
  origen_woo_contactos?: number | null;
  quitadas_a_mano?: number | null;
  programado?: string;
};

export async function getResumenErp(): Promise<ResumenErp> {
  return apiFetch("/api/crm/resumen-erp");
}

export async function recalcularResumenErp(): Promise<ResumenErp> {
  return apiFetch("/api/crm/resumen-erp/recalcular", { method: "POST" });
}

const NF = new Intl.NumberFormat("es-ES");

/** «el 1/11 a las 05:20». */
export function cuando(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const dia = d.toLocaleDateString("es-ES", { day: "numeric", month: "numeric" });
  const hm = d.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
  return `el ${dia} a las ${hm}`;
}

export function ResumenErpEstado() {
  const [datos, setDatos] = useState<ResumenErp | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getResumenErp().then(setDatos).catch(() => setDatos(null));
  }, []);

  async function recalcular() {
    setError(null);
    try {
      const r = await recalcularResumenErp();
      setDatos(r);
      setAviso(r.programado === "sin_cola"
        ? "No se ha podido programar: la cola no responde."
        : "Recálculo en marcha: tarda unos segundos. Vuelve a cargar la página para verlo.");
    } catch (e) {
      setError(extractErrorMessage(e, "No se ha podido recalcular."));
    }
  }

  if (datos === null) return null;
  // Fechas, no cadenas: `isoformat()` quita los microsegundos cuando son cero.
  const fallo = !!datos.fallo_at
    && (!datos.ok_at || new Date(datos.fallo_at) > new Date(datos.ok_at));
  return (
    <section className="resumen-erp-estado" aria-label="Datos de empresa, parque y compras">
      {fallo ? (
        <p className="form-error" role="alert">
          El último recálculo de empresa, parque y compras falló {cuando(datos.fallo_at)}
          {datos.fallo ? ` (${datos.fallo})` : ""}.{" "}
          {datos.ok_at ? `Las reglas de ese grupo usan los datos del recálculo anterior, ${cuando(datos.ok_at)}.`
            : "Aún no hay datos: esas reglas no devuelven nada."}
        </p>
      ) : null}
      <p className="muted small">
        {datos.calculado ? (
          <>
            Empresa, parque y compras: datos calculados {cuando(datos.ok_at)}
            {" · "}<strong>YA CLIENTE</strong> {NF.format(datos.ya_cliente_contactos ?? 0)} contactos
            {" "}({NF.format(datos.ya_cliente_empresas ?? 0)} empresas)
            {" · "}<strong>ORIGEN WOO</strong> {NF.format(datos.origen_woo_contactos ?? 0)}
            {" · "}
            <span title="Contactos a los que alguien quitó a mano una etiqueta automática: el recálculo no se la vuelve a poner. Si son muchos, la definición no es la que la gente espera.">
              quitadas a mano {NF.format(datos.quitadas_a_mano ?? 0)}
            </span>
          </>
        ) : "Empresa, parque y compras: aún no se ha calculado."}
        {" "}
        <button type="button" className="button small secondary" onClick={() => void recalcular()}>
          Recalcular ahora
        </button>
      </p>
      {aviso ? <p className="muted small" role="status">{aviso}</p> : null}
      {error ? <p className="form-error">{error}</p> : null}
    </section>
  );
}
