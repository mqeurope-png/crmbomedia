"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
/** Cada cuánto se mira si ha terminado la comprobación de FACTUSOL. */
const POLL_MS = 5000;
const MOTIVO_MAX = 255;

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

  // Recargas explícitas (tras «Comprobar ahora», revisar…): desde los eventos.
  const recargarItems = useCallback(async () => {
    try {
      setItems((await listCuadreHallazgos(filtros)).items);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar los descuadres."));
    }
  }, [filtros]);
  const recargarResumen = useCallback(async () => {
    try {
      setResumen(await getCuadreResumen());
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cargar el Cuadre."));
    }
  }, []);

  // Carga inicial y al cambiar los filtros.
  useEffect(() => {
    let vivo = true;
    listCuadreHallazgos(filtros)
      .then((r) => { if (vivo) setItems(r.items); })
      .catch((e) => {
        if (vivo) setError(extractErrorMessage(e, "No se pudieron cargar los descuadres."));
      });
    return () => { vivo = false; };
  }, [filtros]);

  // Resumen: al entrar y, mientras FACTUSOL se comprueba en segundo plano
  // («comprobando…»), cada pocos segundos; al terminar se recargan las filas.
  const [tick, setTick] = useState(0);
  const comprobandoAntes = useRef(false);
  useEffect(() => {
    let vivo = true;
    getCuadreResumen()
      .then((r) => {
        if (!vivo) return;
        setResumen(r);
        const ahora = r.en_curso.length > 0;
        if (comprobandoAntes.current && !ahora) recargarItems();
        comprobandoAntes.current = ahora;
      })
      .catch((e) => { if (vivo) setError(extractErrorMessage(e, "No se pudo cargar el Cuadre.")); });
    return () => { vivo = false; };
  }, [tick, recargarItems]);
  const comprobando = (resumen?.en_curso.length ?? 0) > 0;
  useEffect(() => {
    if (!comprobando) return;
    const t = window.setTimeout(() => setTick((n) => n + 1), POLL_MS);
    return () => window.clearTimeout(t);
  }, [comprobando, resumen]);

  async function comprobar() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await comprobarCuadre();
      setResumen(r.resumen);
      comprobandoAntes.current = r.resumen.en_curso.length > 0;
      await recargarItems();
      const fs = r.lanzadas.factusol?.estado;
      setNotice(
        fs === "en_cola" || fs === "corriendo"
          ? "Comprobadas las de BoHub. Las de FACTUSOL se comprueban en segundo plano: "
            + "la lista se pone al día sola al terminar."
          : fs === "error"
            ? "Comprobadas las de BoHub. Las de FACTUSOL no se han podido lanzar (worker parado)."
            : "Comprobado.",
      );
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

  async function guardarRevision(h: CuadreHallazgo) {
    const texto = motivo.trim();
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
      await Promise.all([recargarItems(), recargarResumen()]);
    } catch (e) {
      setRowError({ id: h.id, msg: extractErrorMessage(e, "No se pudo marcar como revisado.") });
    } finally {
      setRowBusy(null);
    }
  }

  async function reincluir(h: CuadreHallazgo) {
    setRowBusy(h.id);
    setRowError(null);
    try {
      await reincluirCuadreHallazgo(h.id);
      await Promise.all([recargarItems(), recargarResumen()]);
    } catch (e) {
      setRowError({ id: h.id, msg: extractErrorMessage(e, "No se pudo volver a incluir.") });
    } finally {
      setRowBusy(null);
    }
  }

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
            <button type="button" className="button small" disabled={busy || comprobando}
                    onClick={comprobar}>
              {busy ? "Comprobando…" : "Comprobar ahora"}
            </button>
          </>
        )}
      />

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}

      <section className="erp-cuadre-estado" aria-label="Estado del Cuadre">
        <p className="muted">{ultimaPasadaTexto(resumen?.ultima_pasada ?? null)}</p>
        {comprobando ? (
          <p className="erp-cuadre-comprobando" role="status">
            Comprobando {resumen?.en_curso.map((p) => FUENTE_LABEL[p.fuente] ?? p.fuente).join(" y ")}…
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
        {resumen === null && !error ? <p className="muted">Cargando…</p> : null}
        {resumen !== null && tarjetas.length === 0 ? (
          <p className="muted">No hay comprobaciones con estos filtros.</p>
        ) : null}
        {tarjetas.map((c) => {
          const filas = porCheck.get(c.id) ?? [];
          const abierta = expanded.has(c.id);
          return (
            <article key={c.id} role="listitem"
                     className={`erp-cuadre-tarjeta is-${c.severidad}${c.abiertos === 0 ? " is-ok" : ""}`}>
              <header className="erp-cuadre-tarjeta-cab">
                <button
                  type="button"
                  className="erp-cuadre-tarjeta-toggle"
                  aria-expanded={abierta}
                  aria-controls={`cuadre-${c.id}`}
                  onClick={() => toggle(c.id)}
                >
                  <span className="erp-cuadre-tarjeta-titulo">{c.titulo}</span>
                  <span className="erp-cuadre-tarjeta-num" aria-label={`${c.abiertos} abiertos`}>
                    {c.abiertos}
                  </span>
                </button>
                <span className={`badge ${SEV_TONE[c.severidad]}`}>{SEV_LABEL[c.severidad]}</span>
                {c.nuevos > 0 ? <span className="badge active">{c.nuevos} nuevos</span> : null}
                {c.revisados > 0 ? (
                  <span className="badge muted">{c.revisados} revisados</span>
                ) : null}
              </header>
              <p className="muted small">
                {c.descripcion}
                {c.dias !== null && c.dias_texto ? ` · ${c.dias_texto.replace("N", String(c.dias))}` : ""}
              </p>
              {abierta ? (
                <div id={`cuadre-${c.id}`} className="erp-cuadre-filas">
                  {filas.length === 0 ? (
                    <p className="muted small">
                      {c.abiertos === 0 ? "Todo cuadra." : "Ninguno con estos filtros."}
                    </p>
                  ) : (
                    <ul>
                      {filas.map((h) => (
                        <li key={h.id} className={`erp-cuadre-fila${h.estado === "revisado" ? " is-revisado" : ""}`}>
                          <div className="erp-cuadre-fila-main">
                            <div className="erp-cuadre-fila-r1">
                              {h.enlace ? (
                                <Link href={h.enlace} className="mono">{h.etiqueta}</Link>
                              ) : <span className="mono">{h.etiqueta}</span>}
                              {h.nuevo ? <span className="badge active">Nuevo</span> : null}
                              {h.estado === "revisado" ? <span className="badge muted">Revisado</span> : null}
                            </div>
                            <p>{h.detalle}</p>
                            <p className="muted small">{h.pista_de_arreglo}</p>
                            {h.estado === "revisado" && h.motivo ? (
                              <p className="small">Motivo: {h.motivo}</p>
                            ) : null}
                            {rowError?.id === h.id ? (
                              <p className="form-error" role="alert">{rowError.msg}</p>
                            ) : null}
                            {revisando === h.id ? (
                              <form
                                className="erp-cuadre-motivo"
                                onSubmit={(e) => { e.preventDefault(); guardarRevision(h); }}
                              >
                                <label className="field">
                                  <span>Motivo (obligatorio)</span>
                                  <input
                                    type="text"
                                    aria-label={`Motivo para ${h.etiqueta}`}
                                    maxLength={MOTIVO_MAX}
                                    value={motivo}
                                    autoFocus
                                    onChange={(e) => setMotivo(e.target.value)}
                                  />
                                </label>
                                <button type="submit" className="button small"
                                        disabled={rowBusy === h.id || motivo.trim().length < 3}>
                                  Guardar
                                </button>
                                <button type="button" className="button small secondary"
                                        onClick={() => { setRevisando(null); setMotivo(""); setRowError(null); }}>
                                  Cancelar
                                </button>
                              </form>
                            ) : null}
                          </div>
                          <div className="erp-cuadre-fila-acciones">
                            {h.arreglo_enlace ? (
                              <Link href={h.arreglo_enlace} className="button small">
                                {h.arreglo_boton ?? "Ir a arreglarlo"}
                              </Link>
                            ) : null}
                            {h.estado === "abierto" && revisando !== h.id ? (
                              <button type="button" className="button small secondary"
                                      disabled={rowBusy === h.id}
                                      onClick={() => { setRevisando(h.id); setMotivo(""); setRowError(null); }}>
                                Revisado / no es un descuadre
                              </button>
                            ) : null}
                            {h.estado === "revisado" ? (
                              <button type="button" className="button small secondary"
                                      disabled={rowBusy === h.id}
                                      onClick={() => reincluir(h)}>
                                Volver a incluir
                              </button>
                            ) : null}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </main>
  );
}
