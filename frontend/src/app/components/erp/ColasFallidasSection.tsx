"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getColasResumen,
  listColaFallidos,
  reencolarCola,
  vaciarCola,
  type ColaOperacion,
  type ColaTrabajoFallido,
  type ColasResumen,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** ERP · Cuadre → «Colas». Los registros de fallidos de RQ: cuántos hay, qué
 *  memoria de Redis ocupan y las dos operaciones que antes solo se podían
 *  hacer entrando al contenedor.
 *
 *  Las dos van con **vista previa obligatoria**: primero se cuenta y se
 *  enseña un ejemplo, y solo entonces aparece el botón que lo hace. Vaciar es
 *  irreversible, así que además pide confirmación y queda en auditoría. */

function mb(bytes: number): string {
  if (!bytes) return "—";
  const megas = bytes / (1024 * 1024);
  const [valor, unidad] = megas >= 1024 ? [megas / 1024, "GB"] : [megas, "MB"];
  return `${valor.toLocaleString("es-ES", { maximumFractionDigits: 1 })} ${unidad}`;
}

function fecha(iso: string | null): string {
  if (!iso) return "fecha desconocida";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("es-ES");
}

export function ColasFallidasSection({ puedeOperar }: { puedeOperar: boolean }) {
  const [resumen, setResumen] = useState<ColasResumen | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [previa, setPrevia] = useState<{ op: "reencolar" | "vaciar"; datos: ColaOperacion } | null>(null);
  const [detalle, setDetalle] = useState<{ cola: string; filas: ColaTrabajoFallido[] } | null>(null);

  // Una sola vía de carga, con guarda: una respuesta vieja nunca pisa a la
  // nueva (mismo patrón que la pantalla del Cuadre).
  const [recarga, setRecarga] = useState(0);
  useEffect(() => {
    let vivo = true;
    getColasResumen()
      .then((r) => {
        if (!vivo) return;
        setResumen(r);
        setError(null);
      })
      .catch((err) => {
        if (!vivo) return;
        setError(extractErrorMessage(err, "No se pudo leer el estado de las colas."));
      });
    return () => {
      vivo = false;
    };
  }, [recarga]);
  const cargar = useCallback(() => setRecarga((n) => n + 1), []);

  async function conErrores(fn: () => Promise<void>) {
    setOcupado(true);
    setError(null);
    setAviso(null);
    try {
      await fn();
    } catch (err) {
      setError(extractErrorMessage(err, "La operación falló."));
    } finally {
      setOcupado(false);
    }
  }

  const verPrevia = (cola: string, op: "reencolar" | "vaciar") =>
    conErrores(async () => {
      const datos = op === "reencolar"
        ? await reencolarCola(cola, { probar: true })
        : await vaciarCola(cola, { probar: true });
      setPrevia({ op, datos });
    });

  const confirmar = () =>
    conErrores(async () => {
      if (!previa) return;
      const { op, datos } = previa;
      if (op === "vaciar" && !window.confirm(
        `Vas a descartar los trabajos fallidos de ${datos.cola}. No se puede ` +
        "deshacer: se borran los trabajos y su traza. ¿Seguir?")) {
        return;
      }
      const hecho = op === "reencolar"
        ? await reencolarCola(datos.cola, { probar: false })
        : await vaciarCola(datos.cola, { probar: false });
      setAviso(op === "reencolar"
        ? `Reencolados ${hecho.reencolados ?? 0} trabajos de ${datos.cola}.`
        : `Descartados ${hecho.descartados ?? 0} trabajos de ${datos.cola}` +
          (hecho.quedan ? `; quedan ${hecho.quedan}, vuelve a pulsar.` : "."));
      setPrevia(null);
      cargar();
    });

  const verFallidos = (cola: string) =>
    conErrores(async () => {
      const out = await listColaFallidos(cola, { limite: 50 });
      setDetalle({ cola, filas: out.trabajos });
    });

  const colas = Object.entries(resumen?.fallidos_por_cola ?? {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);

  return (
    <section className="card erp-colas" aria-label="Colas con trabajos fallidos">
      <h3 style={{ marginTop: 0 }}>Colas · trabajos fallidos</h3>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {aviso ? <p className="form-success" role="status">{aviso}</p> : null}

      {resumen ? (
        <p className="muted small">
          <strong>{resumen.fallidos_total.toLocaleString("es-ES")}</strong> trabajos
          fallidos ocupan unos <strong>{mb(resumen.bytes_estimados)}</strong> de
          Redis (estimado sobre {resumen.muestra} muestras de{" "}
          {resumen.bytes_por_trabajo_medio.toLocaleString("es-ES")} bytes de
          media; Redis usa {mb(resumen.redis_usada_bytes)} en total).
        </p>
      ) : null}

      {colas.length === 0 ? (
        <p className="muted small">Ninguna cola tiene trabajos fallidos.</p>
      ) : (
        <table className="table-compact">
          <thead>
            <tr><th>Cola</th><th>Fallidos</th><th aria-label="Acciones" /></tr>
          </thead>
          <tbody>
            {colas.map(([cola, n]) => (
              <tr key={cola}>
                <td>{cola}</td>
                <td>{n.toLocaleString("es-ES")}</td>
                <td>
                  <button type="button" className="button small secondary"
                          disabled={ocupado} onClick={() => verFallidos(cola)}>
                    Ver
                  </button>{" "}
                  {puedeOperar ? (
                    <>
                      <button type="button" className="button small secondary"
                              disabled={ocupado}
                              onClick={() => verPrevia(cola, "reencolar")}>
                        Reencolar…
                      </button>{" "}
                      <button type="button" className="button small secondary"
                              disabled={ocupado}
                              onClick={() => verPrevia(cola, "vaciar")}>
                        Vaciar…
                      </button>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {previa ? (
        <div className="erp-colas-previa">
          <h4 style={{ marginBottom: ".25rem" }}>
            Vista previa · {previa.op === "reencolar" ? "reencolar" : "vaciar"}{" "}
            {previa.datos.cola}
          </h4>
          <ul className="small" style={{ lineHeight: 1.7 }}>
            <li>
              En el registro:{" "}
              <strong>{(previa.datos.en_registro ?? 0).toLocaleString("es-ES")}</strong>
            </li>
            {previa.op === "reencolar" ? (
              <>
                <li>
                  Se reencolarían <strong>{previa.datos.elegidos ?? 0}</strong>{" "}
                  (tope {previa.datos.tope ?? 0} por pulsación).
                </li>
                {previa.datos.ejemplo ? (
                  <li>
                    Ejemplo: {previa.datos.ejemplo.funcion} ·{" "}
                    {fecha(previa.datos.ejemplo.fecha)}
                  </li>
                ) : null}
              </>
            ) : (
              <li>
                Se descartarían hasta <strong>{previa.datos.tope ?? 0}</strong> por
                pulsación, con sus datos y su traza. <strong>No se puede deshacer.</strong>
              </li>
            )}
          </ul>
          <button type="button" className="button small" disabled={ocupado}
                  onClick={confirmar}>
            {previa.op === "reencolar" ? "Reencolar ahora" : "Vaciar ahora"}
          </button>{" "}
          <button type="button" className="button small secondary" disabled={ocupado}
                  onClick={() => setPrevia(null)}>
            Cancelar
          </button>
        </div>
      ) : null}

      {detalle ? (
        <div className="erp-colas-detalle">
          <h4 style={{ marginBottom: ".25rem" }}>
            Fallidos de {detalle.cola} (los {detalle.filas.length} más recientes)
          </h4>
          <p className="muted small">
            Los argumentos son lo que hace falta para repetir la operación desde
            su pantalla.
          </p>
          <table className="table-compact">
            <thead>
              <tr><th>Función</th><th>Fecha</th><th>Argumentos</th><th>Error</th></tr>
            </thead>
            <tbody>
              {detalle.filas.map((t) => (
                <tr key={t.id}>
                  <td>{t.funcion.split(".").pop()}</td>
                  <td>{fecha(t.fecha)}</td>
                  <td><code>{t.argumentos}</code></td>
                  <td>{t.error}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" className="button small secondary"
                  onClick={() => setDetalle(null)}>
            Cerrar
          </button>
        </div>
      ) : null}
    </section>
  );
}
