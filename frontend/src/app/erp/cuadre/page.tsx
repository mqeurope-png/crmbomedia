"use client";

import Link from "next/link";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import {
  comprobarCuadre,
  exportCuadreXlsx,
  getCuadreResumen,
  listCuadreHallazgos,
  reincluirCuadreHallazgo,
  revisarCuadreHallazgo,
  saveBlob,
  type CuadreHallazgo,
  type CuadrePasada,
  type CuadreResumen,
  type CuadreSeveridad,
  type CuadreTarjeta,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** ERP · Cuadre — descuadres entre BoHub, FACTUSOL, envíos y la hoja de
 *  Drive. SOLO LECTURA: no corrige nada; cada descuadre lleva a la pantalla
 *  donde ya existe la acción para arreglarlo. Lo único que se guarda aquí es
 *  «Revisado / no es un descuadre» (con motivo) y «Volver a incluir». */

const SEVERIDADES: readonly CuadreSeveridad[] = ["alta", "media", "baja"];
const SEV_LABEL: Record<CuadreSeveridad, string> = { alta: "Alta", media: "Media", baja: "Baja" };
const SEV_TONE: Record<CuadreSeveridad, string> = { alta: "bad", media: "warn", baja: "muted" };
const ORIGEN_LABEL: Record<string, string> = { nocturno: "pasada nocturna", manual: "Comprobar ahora" };
const FUENTE_LABEL: Record<string, string> = { mysql: "BoHub", factusol: "FACTUSOL" };
/** Cada cuánto se mira si ha terminado la comprobación de FACTUSOL: deprisa
 *  los primeros minutos y luego más despacio (una pasada atascada en cola no
 *  martillea la API). Con la pestaña oculta no se pregunta. */
const POLL_MS = 5000;
const POLL_LENTO_MS = 30000;
const POLL_LENTO_TRAS_MS = 2 * 60 * 1000;
const MOTIVO_MAX = 255;
/** Filas que se pintan de una vez en una tarjeta (luego, «Ver más»). */
const FILAS_POR_TANDA = 100;

function hora(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("es-ES", { timeStyle: "short" });
}

function fechaHora(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" });
}

function ultimaPasadaTexto(p: CuadrePasada | null): string {
  if (!p) return "Todavía no se ha comprobado nada: pulsa «Comprobar ahora».";
  const errores = p.estado === "con_errores" ? " · alguna comprobación falló" : "";
  return `Última comprobación: ${fechaHora(p.finished_at)} (${ORIGEN_LABEL[p.origen] ?? p.origen}, `
    + `${FUENTE_LABEL[p.fuente] ?? p.fuente})${errores}.`;
}

export default function CuadrePage() {
  const [resumen, setResumen] = useState<CuadreResumen | null>(null);
  const [items, setItems] = useState<CuadreHallazgo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [severidad, setSeveridad] = useState<CuadreSeveridad | "">("");
  const [checkId, setCheckId] = useState("");
  const [soloNuevos, setSoloNuevos] = useState(false);
  const [incluirRevisados, setIncluirRevisados] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [revisando, setRevisando] = useState<string | null>(null);
  const [motivo, setMotivo] = useState("");
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; msg: string } | null>(null);

  const filtros = useMemo(() => ({
    severidad: severidad || undefined,
    solo_nuevos: soloNuevos,
    incluir_revisados: incluirRevisados,
  }), [severidad, soloNuevos, incluirRevisados]);

  // Filas: UNA sola vía de carga (filtros + recargas tras las acciones), con
  // guarda: una respuesta vieja nunca pisa la de los filtros actuales.
  const [recarga, setRecarga] = useState(0);
  useEffect(() => {
    let vivo = true;
    listCuadreHallazgos(filtros)
      .then((r) => {
        if (!vivo) return;
        setItems(r.items);
        setErrorCarga(null);
      })
      .catch((e) => {
        if (vivo) setErrorCarga(extractErrorMessage(e, "No se pudieron cargar los descuadres."));
      });
    return () => { vivo = false; };
  }, [filtros, recarga]);

  // Resumen: al entrar, tras cada acción y, mientras FACTUSOL se comprueba en
  // segundo plano («comprobando…»), cada pocos segundos —aunque una consulta
  // falle—. Al terminar se recargan las filas y se dice si fue bien.
  const [tick, setTick] = useState(0);
  const [pausa, setPausa] = useState(0);
  const comprobandoAntes = useRef(false);
  const comprobandoDesde = useRef<number | null>(null);
  const lanzadaFactusol = useRef<string | null>(null);
  useEffect(() => {
    let vivo = true;
    getCuadreResumen()
      .then((r) => {
        if (!vivo) return;
        // Mientras FACTUSOL se comprueba, el resumen casi nunca cambia: si es el
        // mismo no se toca el estado y la página no se repinta en cada sondeo.
        setResumen((prev) => (prev && mismoJson(prev, r) ? prev : r));
        setErrorCarga(null);
        const ahora = r.en_curso.length > 0;
        if (comprobandoAntes.current && !ahora) {
          setRecarga((n) => n + 1);
          const id = lanzadaFactusol.current;
          if (id) {
            if (r.ultimas_por_fuente?.factusol?.id === id) {
              setNotice("Comprobadas también las de FACTUSOL.");
            } else {
              setNotice(null);
              setError("La comprobación de FACTUSOL no ha terminado bien (¿FACTUSOL o "
                + "worker-sync parados?): sus tarjetas siguen con lo de la última vez.");
            }
          }
          lanzadaFactusol.current = null;
        }
        comprobandoAntes.current = ahora;
      })
      .catch((e) => {
        if (vivo) setErrorCarga(extractErrorMessage(e, "No se pudo cargar el Cuadre."));
      });
    return () => { vivo = false; };
  }, [tick]);
  const comprobando = (resumen?.en_curso.length ?? 0) > 0;
  useEffect(() => {
    if (!comprobando) {
      comprobandoDesde.current = null;
      return;
    }
    if (comprobandoDesde.current === null) comprobandoDesde.current = Date.now();
    const lento = Date.now() - comprobandoDesde.current > POLL_LENTO_TRAS_MS;
    const t = window.setTimeout(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") setPausa((n) => n + 1);
      else setTick((n) => n + 1);
    }, lento ? POLL_LENTO_MS : POLL_MS);
    return () => window.clearTimeout(t);
  }, [comprobando, tick, pausa]);

  async function comprobar() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await comprobarCuadre();
      setResumen(r.resumen);
      comprobandoAntes.current = r.resumen.en_curso.length > 0;
      setRecarga((n) => n + 1);
      const ms = r.lanzadas.mysql?.estado;
      const fs = r.lanzadas.factusol?.estado;
      if (fs === "en_cola" || fs === "corriendo") {
        lanzadaFactusol.current = r.lanzadas.factusol?.id ?? null;
      }
      const partes = [
        ms === "en_curso"
          ? "Ya había una comprobación de BoHub en curso."
          : ms === "con_errores" || ms === "error"
            ? "Comprobadas las de BoHub, pero alguna ha fallado (mira las tarjetas)."
            : "Comprobadas las de BoHub.",
        fs === "en_cola" || fs === "corriendo"
          ? "Las de FACTUSOL se comprueban en segundo plano: la lista se pone al día sola al terminar."
          : fs === "error"
            ? "Las de FACTUSOL no se han podido lanzar (¿worker-sync parado?)."
            : "",
      ];
      setNotice(partes.filter(Boolean).join(" "));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo comprobar."));
    } finally {
      setBusy(false);
    }
  }

  async function descargar() {
    setError(null);
    try {
      const blob = await exportCuadreXlsx();
      saveBlob(blob, `cuadre_descuadres_${new Date().toISOString().slice(0, 10)}.xlsx`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo descargar el Excel."));
    }
  }

  // Acciones de las filas: estables (solo usan setters), para que las filas
  // memorizadas no se repinten con cada cambio de la página.
  const acciones = useMemo<AccionesFila>(() => {
    const recargar = () => {
      setRecarga((n) => n + 1);
      setTick((n) => n + 1);
    };
    return {
      empezar: (id) => { setRevisando(id); setMotivo(""); setRowError(null); },
      cancelar: () => { setRevisando(null); setMotivo(""); setRowError(null); },
      escribir: (texto) => setMotivo(texto),
      guardar: async (h, motivoFila) => {
        const texto = motivoFila.trim();
        if (texto.length < 3) {
          setRowError({ id: h.id, msg: "Escribe un motivo (al menos 3 caracteres)." });
          return;
        }
        setRowBusy(h.id);
        setRowError(null);
        try {
          await revisarCuadreHallazgo(h.id, texto);
          setRevisando(null);
          setMotivo("");
          recargar();
        } catch (e) {
          setRowError({ id: h.id, msg: extractErrorMessage(e, "No se pudo marcar como revisado.") });
          recargar();                     // p. ej. lo resolvió otra pasada mientras tanto
        } finally {
          setRowBusy(null);
        }
      },
      reincluir: async (h) => {
        setRowBusy(h.id);
        setRowError(null);
        try {
          await reincluirCuadreHallazgo(h.id);
          recargar();
        } catch (e) {
          setRowError({ id: h.id, msg: extractErrorMessage(e, "No se pudo volver a incluir.") });
          recargar();
        } finally {
          setRowBusy(null);
        }
      },
    };
  }, []);

  const [visibles, setVisibles] = useState<Record<string, number>>({});

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const porCheck = useMemo(() => {
    const out = new Map<string, CuadreHallazgo[]>();
    for (const h of items) {
      const list = out.get(h.check_id) ?? [];
      list.push(h);
      out.set(h.check_id, list);
    }
    return out;
  }, [items]);

  const tarjetas: CuadreTarjeta[] = (resumen?.checks ?? []).filter(
    (c) => (!severidad || c.severidad === severidad) && (!checkId || c.id === checkId),
  );
  const contadores = resumen?.contadores;

  return (
    <main className="shell shell-wide erp-flow">
      <PageHeader
        title="Cuadre"
        eyebrow="ERP"
        description="Lo que no cuadra entre BoHub, FACTUSOL, los envíos y la hoja de Drive. Solo lectura: cada descuadre lleva a donde se arregla."
        crumbs={[{ label: "ERP", href: "/erp" }, { label: "Cuadre" }]}
        actions={(
          <>
            <button type="button" className="button small secondary" onClick={descargar}>
              Descargar Excel
            </button>
            <button type="button" className="button small" disabled={busy}
                    onClick={comprobar}>
              {busy ? "Comprobando…" : "Comprobar ahora"}
            </button>
          </>
        )}
      />

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {errorCarga ? <p className="form-error" role="alert">{errorCarga}</p> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}

      <section className="erp-cuadre-estado" aria-label="Estado del Cuadre">
        <p className="muted">{ultimaPasadaTexto(resumen?.ultima_pasada ?? null)}</p>
        {comprobando ? (
          <p className="erp-cuadre-comprobando" role="status">
            Comprobando {resumen?.en_curso.map((p) => (
              `${FUENTE_LABEL[p.fuente] ?? p.fuente}${p.estado === "en_cola" && hora(p.created_at)
                ? ` (en cola desde las ${hora(p.created_at)})` : ""}`
            )).join(" y ")}…
          </p>
        ) : null}
        {resumen && !resumen.nocturno.activo ? (
          <p className="muted small">
            La comprobación nocturna está apagada (se enciende en Configuración ERP → Cuadre).
          </p>
        ) : null}
      </section>

      <section className="erp-cuadre-contadores" aria-label="Descuadres por severidad">
        {SEVERIDADES.map((s) => (
          <button
            key={s}
            type="button"
            className={`erp-cuadre-contador is-${s}${severidad === s ? " is-active" : ""}`}
            aria-pressed={severidad === s}
            onClick={() => setSeveridad(severidad === s ? "" : s)}
          >
            <span className="erp-cuadre-contador-valor">{contadores?.[s] ?? "—"}</span>
            <span className="erp-cuadre-contador-label">Severidad {SEV_LABEL[s].toLowerCase()}</span>
          </button>
        ))}
      </section>

      <div className="erp-doc-filters" role="search" aria-label="Filtros del Cuadre">
        <label className="field">
          <span>Severidad</span>
          <select value={severidad} aria-label="Severidad"
                  onChange={(e) => setSeveridad(e.target.value as CuadreSeveridad | "")}>
            <option value="">Todas</option>
            {SEVERIDADES.map((s) => <option key={s} value={s}>{SEV_LABEL[s]}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Comprobación</span>
          <select value={checkId} aria-label="Comprobación"
                  onChange={(e) => setCheckId(e.target.value)}>
            <option value="">Todas</option>
            {(resumen?.checks ?? []).map((c) => (
              <option key={c.id} value={c.id}>{c.titulo}</option>
            ))}
          </select>
        </label>
        <label className="field erp-check-field">
          <input type="checkbox" checked={soloNuevos}
                 onChange={(e) => setSoloNuevos(e.target.checked)} />
          <span>Solo nuevos desde la última vez</span>
        </label>
        <label className="field erp-check-field">
          <input type="checkbox" checked={incluirRevisados}
                 onChange={(e) => setIncluirRevisados(e.target.checked)} />
          <span>Incluir revisados</span>
        </label>
      </div>

      <div className="erp-cuadre-tarjetas" role="list" aria-label="Comprobaciones">
        {resumen === null && !errorCarga ? <p className="muted">Cargando…</p> : null}
        {resumen !== null && tarjetas.length === 0 ? (
          <p className="muted">No hay comprobaciones con estos filtros.</p>
        ) : null}
        {tarjetas.map((c) => {
          const filas = porCheck.get(c.id) ?? [];
          const abierta = expanded.has(c.id);
          // Lo que dijo la última comprobación terminada de su fuente: sin
          // ella, o si la comprobación falló, «0 abiertos» NO es «todo cuadra».
          const ultima = resumen?.ultimas_por_fuente?.[c.fuente] ?? null;
          const sinComprobar = !ultima || !(c.id in (ultima.resumen ?? {}));
          const fallo = ultima?.resumen?.[c.id]?.error ?? null;
          const cuadra = c.abiertos === 0 && !sinComprobar && !fallo;
          return (
            <article key={c.id} role="listitem"
                     className={`erp-cuadre-tarjeta is-${c.severidad}${cuadra ? " is-ok" : ""}`}>
              <header className="erp-cuadre-tarjeta-cab">
                <button
                  type="button"
                  className="erp-cuadre-tarjeta-toggle"
                  aria-expanded={abierta}
                  aria-controls={`cuadre-${c.id}`}
                  onClick={() => toggle(c.id)}
                >
                  <span className="erp-cuadre-tarjeta-titulo">{c.titulo}</span>
                  <span className="erp-cuadre-tarjeta-num">
                    {c.abiertos}<span className="sr-only"> abiertos</span>
                  </span>
                </button>
                <span className={`badge ${SEV_TONE[c.severidad]}`}>{SEV_LABEL[c.severidad]}</span>
                {sinComprobar ? <span className="badge muted">Sin comprobar</span> : null}
                {fallo ? (
                  <span className="badge warn" title={fallo}>Falló en la última comprobación</span>
                ) : null}
                {c.nuevos > 0 ? <span className="badge active">{c.nuevos} nuevos</span> : null}
                {c.revisados > 0 ? (
                  <span className="badge muted">{c.revisados} revisados</span>
                ) : null}
              </header>
              <p className="muted small">
                {c.descripcion}
                {c.dias !== null && c.dias_texto ? ` · ${c.dias_texto.replace("N", String(c.dias))}` : ""}
              </p>
              <div id={`cuadre-${c.id}`} className="erp-cuadre-filas" hidden={!abierta}>
                {abierta ? (filas.length === 0 ? (
                    <p className="muted small">
                      {fallo
                        ? `La última comprobación falló: ${fallo}`
                        : sinComprobar
                          ? "Todavía no se ha comprobado."
                          : c.abiertos === 0 ? "Todo cuadra." : "Ninguno con estos filtros."}
                    </p>
                  ) : (
                    <>
                      <ul>
                        {filas.slice(0, visibles[c.id] ?? FILAS_POR_TANDA).map((h) => (
                          <FilaCuadre
                            key={h.id}
                            h={h}
                            editando={revisando === h.id}
                            motivo={revisando === h.id ? motivo : ""}
                            ocupada={rowBusy === h.id}
                            error={rowError?.id === h.id ? rowError.msg : null}
                            acciones={acciones}
                          />
                        ))}
                      </ul>
                      {filas.length > (visibles[c.id] ?? FILAS_POR_TANDA) ? (
                        <p className="erp-cuadre-mas">
                          <span className="muted small">
                            Mostrando {visibles[c.id] ?? FILAS_POR_TANDA} de {filas.length}.
                          </span>{" "}
                          <button
                            type="button" className="button small secondary"
                            onClick={() => setVisibles((v) => ({
                              ...v, [c.id]: (v[c.id] ?? FILAS_POR_TANDA) + FILAS_POR_TANDA,
                            }))}
                          >
                            Ver {Math.min(FILAS_POR_TANDA,
                              filas.length - (visibles[c.id] ?? FILAS_POR_TANDA))} más
                          </button>
                        </p>
                      ) : null}
                    </>
                  )) : null}
              </div>
            </article>
          );
        })}
      </div>
    </main>
  );
}

function mismoJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

type AccionesFila = {
  empezar: (id: string) => void;
  cancelar: () => void;
  escribir: (texto: string) => void;
  guardar: (h: CuadreHallazgo, motivo: string) => Promise<void>;
  reincluir: (h: CuadreHallazgo) => Promise<void>;
};

/** Una fila de descuadre. Memorizada: solo se repinta si cambian sus datos o
 *  su estado (editando / ocupada / error), no con cada sondeo ni al teclear el
 *  motivo de otra fila. Los enlaces van sin precarga: cientos de filas no
 *  lanzan cientos de precargas de rutas al desplegar una tarjeta. */
const FilaCuadre = memo(function FilaCuadre({
  h, editando, motivo, ocupada, error, acciones,
}: {
  h: CuadreHallazgo;
  editando: boolean;
  motivo: string;
  ocupada: boolean;
  error: string | null;
  acciones: AccionesFila;
}) {
  return (
    <li className={`erp-cuadre-fila${h.estado === "revisado" ? " is-revisado" : ""}`}>
      <div className="erp-cuadre-fila-main">
        <div className="erp-cuadre-fila-r1">
          {h.enlace ? (
            <Link href={h.enlace} prefetch={false} className="mono">{h.etiqueta}</Link>
          ) : <span className="mono">{h.etiqueta}</span>}
          {h.nuevo ? <span className="badge active">Nuevo</span> : null}
          {h.estado === "revisado" ? <span className="badge muted">Revisado</span> : null}
        </div>
        <p>{h.detalle}</p>
        <p className="muted small">{h.pista_de_arreglo}</p>
        {h.estado === "revisado" && h.motivo ? (
          <p className="small">Motivo: {h.motivo}</p>
        ) : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        {editando ? (
          <form
            className="erp-cuadre-motivo"
            onSubmit={(e) => { e.preventDefault(); acciones.guardar(h, motivo); }}
          >
            <label className="field">
              <span>Motivo (obligatorio)</span>
              <input
                type="text"
                aria-label={`Motivo para ${h.etiqueta}`}
                maxLength={MOTIVO_MAX}
                value={motivo}
                autoFocus
                onChange={(e) => acciones.escribir(e.target.value)}
              />
            </label>
            <button type="submit" className="button small"
                    disabled={ocupada || motivo.trim().length < 3}>
              Guardar
            </button>
            <button type="button" className="button small secondary" onClick={acciones.cancelar}>
              Cancelar
            </button>
          </form>
        ) : null}
      </div>
      <div className="erp-cuadre-fila-acciones">
        {h.arreglo_enlace ? (
          <Link href={h.arreglo_enlace} prefetch={false} className="button small">
            {h.arreglo_boton ?? "Ir a arreglarlo"}
          </Link>
        ) : null}
        {h.estado === "abierto" && !editando ? (
          <button type="button" className="button small secondary" disabled={ocupada}
                  onClick={() => acciones.empezar(h.id)}>
            Revisado / no es un descuadre
          </button>
        ) : null}
        {h.estado === "revisado" ? (
          <button type="button" className="button small secondary" disabled={ocupada}
                  onClick={() => acciones.reincluir(h)}>
            Volver a incluir
          </button>
        ) : null}
      </div>
    </li>
  );
});
