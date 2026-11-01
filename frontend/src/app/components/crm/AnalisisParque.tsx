"use client";

import Link from "next/link";
import { Fragment, useEffect, useRef, useState } from "react";
import {
  generarAnalisisParque,
  getAnalisisParque,
  getPermisoAnalisis,
  type AnalisisParque,
  type BloqueAnalisis,
  type FraseCitada,
  type HechoAnalisis,
} from "../../lib/analisisParqueApi";
import { fichaDocumentoHref } from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** El análisis del parque (1/11/2026). En la ficha de empresa, entero; en la
 *  de contacto, un resumen que dice que es de su empresa. Los hechos los
 *  calcula el servidor y cada uno lleva su documento o su fecha; la IA solo
 *  escribe la lectura y «por dónde entrar», citando los hechos (D1, D2…). */

const TITULO: Record<BloqueAnalisis, string> = {
  tiene: "Qué tiene",
  consume: "Qué consume",
  no_compra: "Qué no compra (o ha dejado de comprar)",
  cambio: "Qué ha cambiado",
  no_se_sabe: "Lo que no se sabe",
};
const ORDEN: BloqueAnalisis[] = ["tiene", "consume", "no_compra", "cambio"];

function fecha(iso: string | null): string {
  if (!iso) return "fecha desconocida";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("es-ES");
}

function cuando(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso
    : `${d.toLocaleDateString("es-ES")} a las ${d.toLocaleTimeString("es-ES",
      { hour: "2-digit", minute: "2-digit" })}`;
}

function anclaDe(companyId: string, id: string): string {
  return `analisis-${companyId}-${id}`;
}

function Fuente({ h }: { h: HechoAnalisis }) {
  const f = h.fuente;
  if (!f) return null;
  if (f.tipo === "documento") {
    return (
      <Link href={fichaDocumentoHref(f.doc.tipo, f.doc.ejercicio, f.doc.serie, f.doc.codigo)}
            prefetch={false} className="analisis-fuente">
        {f.doc.tipo === "albaran" ? "Albarán" : "Factura"} {f.doc.numero}
      </Link>
    );
  }
  if (f.tipo === "manual") {
    return <span className="analisis-fuente muted">añadida a mano{f.fecha ? ` · ${fecha(f.fecha)}` : ""}</span>;
  }
  // La fecha ya escrita en el propio dato no se repite al lado.
  const dma = f.fecha ? `${f.fecha.slice(8, 10)}/${f.fecha.slice(5, 7)}/${f.fecha.slice(0, 4)}` : "";
  if (dma && h.texto.includes(dma)) return null;
  return <span className="analisis-fuente muted">{fecha(f.fecha)}</span>;
}

/** Las citas, separadas (`D1 · D2 · D3`): pegadas se leían «D21D1D16». */
function Citas({ citas, companyId }: { citas: string[]; companyId: string }) {
  return (
    <span className="analisis-citas">
      {citas.map((c, i) => (
        <Fragment key={c}>
          {i > 0 ? <span className="analisis-citas__sep" aria-hidden="true"> · </span> : null}
          <a href={`#${anclaDe(companyId, c)}`} className="badge muted"
             title="El dato en el que se apoya">{c}</a>
        </Fragment>
      ))}
    </span>
  );
}

function Frase({ f, companyId }: { f: FraseCitada; companyId: string }) {
  return <li>{f.texto} <Citas citas={f.citas} companyId={companyId} /></li>;
}

function useAnalisis(companyId: string | null, generarSiHaceFalta: boolean) {
  const [puede, setPuede] = useState<{ puede: boolean; solo_admin: boolean } | null>(null);
  const [a, setA] = useState<AnalisisParque | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generando, setGenerando] = useState(false);
  const pedido = useRef<string | null>(null);

  useEffect(() => {
    if (!companyId) return;
    let vivo = true;
    getPermisoAnalisis()
      .then((p) => {
        if (!vivo) return;
        setPuede(p);
        if (!p.puede) return;
        return getAnalisisParque(companyId).then((r) => { if (vivo) setA(r); });
      })
      .catch((err) => { if (vivo) setError(extractErrorMessage(err, "No se ha podido leer el análisis.")); });
    return () => { vivo = false; };
  }, [companyId]);

  async function generar(forzar: boolean) {
    if (!companyId) return;
    setGenerando(true);
    setError(null);
    try {
      setA(await generarAnalisisParque(companyId, forzar));
    } catch (err) {
      setError(extractErrorMessage(err, "No se ha podido generar el análisis."));
    } finally {
      setGenerando(false);
    }
  }

  // Al abrir la ficha: si no hay, o los datos han cambiado, o ha caducado, se genera.
  useEffect(() => {
    if (!generarSiHaceFalta || !companyId || !a || a.datos !== "con_datos") return;
    if (a.estado === "al_dia" || pedido.current === companyId) return;
    pedido.current = companyId;
    void generar(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a, companyId, generarSiHaceFalta]);

  return { puede, a, error, generando, generar };
}

const MOTIVO: Record<AnalisisParque["estado"], string> = {
  sin_generar: "Aún no se ha generado.",
  al_dia: "",
  desactualizado: "Sus datos han cambiado desde entonces (una compra, una baja o una reclasificación).",
  caducado: "Tiene más meses de los configurados.",
};

/** Ficha de empresa: el análisis entero. */
export function AnalisisParquePanel({ companyId }: { companyId: string }) {
  const { puede, a, error, generando, generar } = useAnalisis(companyId, true);
  if (!puede?.puede) return null;
  const guardado = a?.analisis ?? null;
  const hechos = guardado?.hechos.hechos ?? a?.hechos_actuales.hechos ?? [];
  const avisos = hechos.filter((h) => h.bloque === "no_se_sabe");
  return (
    <section className="erp-card analisis-parque" aria-label="Análisis del parque" id="analisis-parque">
      <h3>
        Análisis del parque{" "}
        {puede.solo_admin ? <span className="badge warn">En prueba · solo administradores</span> : null}
      </h3>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {!a ? <p className="muted">Cargando…</p> : a.datos === "sin_cliente" ? (
        <p className="muted" role="status">
          Esta empresa no tiene cliente de FACTUSOL vinculado: no hay datos de los que hacer un
          análisis, y no se inventa uno.
        </p>
      ) : a.datos === "no_cliente_final" ? (
        <p className="muted" role="status">
          Marcada «no es cliente final» (financiera, revendedor, nosotros): no se analiza su parque.
        </p>
      ) : a.datos === "sin_datos" ? (
        <p className="muted" role="status">
          Sin máquinas en servicio ni compras de consumible o recambio: no hay de qué hacer un análisis.
        </p>
      ) : (
        <>
          <p className="muted small" role="status" aria-label="Cuándo se calculó">
            {generando ? "Generando el análisis…"
              : a.generado_at ? `Calculado el ${cuando(a.generado_at)} con ${a.modelo ?? "—"}`
                + (a.coste_usd != null ? ` · ${a.coste_usd.toFixed(4)} $` : "")
                + (a.estado !== "al_dia" ? `. ${MOTIVO[a.estado]}` : "")
              : MOTIVO[a.estado]}{" "}
            <button type="button" className="button small secondary" disabled={generando}
                    onClick={() => void generar(true)}>
              Volver a generar
            </button>
          </p>
          {guardado && guardado.lectura.length ? (
            <div aria-label="En resumen">
              <h4>En resumen</h4>
              <ul>{guardado.lectura.map((f) => <Frase key={f.texto} f={f} companyId={companyId} />)}</ul>
            </div>
          ) : null}
          {ORDEN.map((b) => {
            const hs = hechos.filter((h) => h.bloque === b);
            if (!hs.length) return null;
            return (
              <div key={b} aria-label={TITULO[b]}>
                <h4>{TITULO[b]}</h4>
                <ul>
                  {hs.map((h) => (
                    <li key={h.id} id={anclaDe(companyId, h.id)}>
                      <span className="badge muted">{h.id}</span> {h.texto} <Fuente h={h} />
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
          <div aria-label="Por dónde entrar" className="analisis-entrada">
            <h4>Por dónde entrar</h4>
            {guardado?.entrada ? (
              <p>
                {guardado.entrada.texto} <Citas citas={guardado.entrada.citas} companyId={companyId} />
                {guardado.entrada.productos.length ? (
                  <span className="muted small">
                    {" "}· Del catálogo: {guardado.entrada.productos
                      .map((p) => `${p.descripcion} (${p.codart})`).join(", ")}
                  </span>
                ) : null}
              </p>
            ) : guardado ? (
              <p className="muted">Sin propuesta: no hay un dato calculado en el que apoyarla.</p>
            ) : <p className="muted">—</p>}
          </div>
          {avisos.length ? (
            <div aria-label={TITULO.no_se_sabe} className="analisis-avisos">
              <h4>{TITULO.no_se_sabe}</h4>
              <ul>
                {avisos.map((h) => (
                  <li key={h.id} id={anclaDe(companyId, h.id)}>
                    <span className="badge warn">{h.id}</span> {h.texto} <Fuente h={h} />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {guardado?.descartado.length ? (
            <details className="muted small">
              <summary>
                {guardado.descartado.length === 1
                  ? "Se descartó 1 frase de la IA que no se apoyaba en un dato"
                  : `Se descartaron ${guardado.descartado.length} frases de la IA que no se apoyaban en un dato`}
              </summary>
              <ul>{guardado.descartado.map((d) => <li key={d}>{d}</li>)}</ul>
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}

/** Ficha de contacto: un resumen corto del análisis de SU EMPRESA (no suyo),
 *  con enlace al entero. Un contacto sin empresa no lo tiene. */
export function AnalisisResumenContacto({ companyId, companyName }: {
  companyId: string | null; companyName: string | null;
}) {
  const { puede, a, error } = useAnalisis(companyId, false);
  if (!companyId || !puede?.puede) return null;
  const guardado = a?.analisis ?? null;
  const enlace = `/companies/${companyId}#analisis-parque`;
  return (
    <section className="analisis-resumen" aria-label="Análisis de su empresa">
      <h4>Análisis de su empresa</h4>
      <p className="muted small">
        Es de {companyName ? <strong>{companyName}</strong> : "su empresa"}, no de este contacto:
        la máquina y las compras son de la empresa.
      </p>
      {error ? <p className="form-error">{error}</p> : null}
      {!a ? null : a.datos !== "con_datos" ? (
        <p className="muted small">
          {a.datos === "sin_cliente" ? "Su empresa no tiene cliente de FACTUSOL: no hay análisis."
            : a.datos === "no_cliente_final" ? "Su empresa no es cliente final: no hay análisis."
              : "Su empresa no tiene parque ni compras de consumible: no hay análisis."}
        </p>
      ) : guardado ? (
        <>
          {guardado.entrada ? (
            <p><strong>Por dónde entrar:</strong> {guardado.entrada.texto}</p>
          ) : guardado.lectura[0] ? <p>{guardado.lectura[0].texto}</p> : null}
          <p className="muted small">Calculado el {cuando(a.generado_at)}.</p>
        </>
      ) : <p className="muted small">Aún no se ha generado.</p>}
      <Link href={enlace} prefetch={false} className="button small secondary">
        {guardado ? "Ver el análisis completo" : "Generarlo en la ficha de la empresa"}
      </Link>
    </section>
  );
}
