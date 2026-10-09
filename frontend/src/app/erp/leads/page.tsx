"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import {
  corregirLeadClasificacion,
  crearLeadWorkflow,
  getLeadWorkflow,
  listLeadClasificaciones,
  simularLeadsEnSeco,
  type LeadClasificacion,
  type LeadClasificaciones,
  type LeadCorreccion,
  type LeadEnSecoInforme,
  type LeadWorkflowEstado,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** ERP · Leads — Respuesta a leads (Fase 1). Lo que la Fase 1 hace con cada
 *  lead: clasificar, dejar un borrador preparado, colocar en Ventas B2B y
 *  crear una tarea. NADA sale al cliente. Aquí viven el workflow (crearlo en
 *  borrador), el modo en seco (qué haría con los leads de los últimos N
 *  días, sin escribir nada) y la lista de leads procesados con la
 *  clasificación corregible a mano. La configuración está en Configuración
 *  ERP → Respuesta a leads. */

const FUENTE_LABEL: Record<string, string> = { web_form: "Formulario web", agilecrm: "AgileCRM" };
const ESTADO_LABEL: Record<string, string> = {
  clasificado: "Clasificado", spam: "Spam", preparado: "Borrador preparado",
  sin_plantilla: "Sin plantilla", omitido: "Omitido",
};
const ESTADO_TONE: Record<string, string> = {
  clasificado: "muted", spam: "bad", preparado: "active", sin_plantilla: "warn", omitido: "muted",
};
const ORIGEN_DATO: Record<string, string> = {
  etiquetas: "por los productos marcados", formulario: "por el formulario",
  texto: "por el texto", ia: "por la IA", palabras_clave: "por palabras clave",
  pais: "por el país", contacto: "por el contacto",
};
const WORKFLOW_STATUS: Record<string, string> = {
  draft: "en borrador", active: "activo", paused: "pausado", archived: "archivado",
};
const DIAS_OPCIONES = [7, 15, 30, 60, 90];
const TEXTO_MAX = 160;

function fechaHora(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" });
}

function porcentaje(c: number): string {
  return `${Math.round(c * 100)}%`;
}

function recortar(texto: string): string {
  return texto.length > TEXTO_MAX ? `${texto.slice(0, TEXTO_MAX - 1)}…` : texto;
}

function fuenteTexto(fuente: string, web: string | null, cuenta: string | null): string {
  const base = FUENTE_LABEL[fuente] ?? fuente;
  if (web) return `${base} · ${web}`;
  if (cuenta) return `${base} · ${cuenta}`;
  return base;
}

function origenDato(valor: string | null | undefined): string {
  return valor ? (ORIGEN_DATO[valor] ?? valor) : "";
}

export default function LeadsPage() {
  const [dias, setDias] = useState(15);
  const [datos, setDatos] = useState<LeadClasificaciones | null>(null);
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [workflow, setWorkflow] = useState<LeadWorkflowEstado | null>(null);
  const [workflowError, setWorkflowError] = useState<string | null>(null);
  const [creando, setCreando] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [diasSeco, setDiasSeco] = useState(15);
  const [simulando, setSimulando] = useState(false);
  const [informe, setInforme] = useState<LeadEnSecoInforme | null>(null);
  const [errorSeco, setErrorSeco] = useState<string | null>(null);

  // La lista: una sola vía de carga; una respuesta vieja nunca pisa la actual.
  useEffect(() => {
    let vivo = true;
    listLeadClasificaciones(dias)
      .then((r) => {
        if (!vivo) return;
        setDatos(r);
        setErrorCarga(null);
      })
      .catch((e) => {
        if (vivo) setErrorCarga(extractErrorMessage(e, "No se pudieron cargar los leads."));
      });
    return () => { vivo = false; };
  }, [dias]);

  const cargarWorkflow = useCallback(() => {
    getLeadWorkflow()
      .then((w) => { setWorkflow(w); setWorkflowError(null); })
      .catch((e) => setWorkflowError(extractErrorMessage(e, "No se pudo consultar el workflow.")));
  }, []);
  useEffect(() => { cargarWorkflow(); }, [cargarWorkflow]);

  async function crear() {
    setCreando(true);
    setWorkflowError(null);
    setNotice(null);
    try {
      const r = await crearLeadWorkflow();
      setNotice(
        `Workflow «${r.name}» creado en borrador. Revísalo en Workflows y actívalo cuando esté bien; `
        + "hasta entonces no procesa ningún lead.",
      );
      cargarWorkflow();
    } catch (e) {
      setWorkflowError(extractErrorMessage(e, "No se pudo crear el workflow."));
    } finally {
      setCreando(false);
    }
  }

  async function simular() {
    setSimulando(true);
    setErrorSeco(null);
    try {
      setInforme(await simularLeadsEnSeco(diasSeco));
    } catch (e) {
      setErrorSeco(extractErrorMessage(e, "No se pudo simular."));
    } finally {
      setSimulando(false);
    }
  }

  // La corrección: el servidor devuelve la fila ya corregida y se sustituye.
  const corregir = useCallback(async (id: string, payload: LeadCorreccion) => {
    const fila = await corregirLeadClasificacion(id, payload);
    setDatos((d) => {
      if (!d) return d;
      const items = d.items.map((i) => (i.id === fila.id ? fila : i));
      return { ...d, items, corregidas: items.filter((i) => i.correccion.corregida).length };
    });
  }, []);

  const etiquetas: Record<string, string> = {};
  for (const i of datos?.opciones.intereses ?? []) etiquetas[i.id] = i.label;

  return (
    <main className="shell shell-wide erp-flow">
      <PageHeader
        title="Respuesta a leads"
        eyebrow="ERP"
        description="Lo que la Fase 1 hace con cada lead: clasificar, dejar un borrador preparado, colocar en Ventas B2B y crear una tarea. Nada sale al cliente."
        crumbs={[{ label: "ERP", href: "/erp" }, { label: "Leads" }]}
        actions={(
          <Link href="/erp/settings#ajuste-respuesta_leads" className="button small secondary">
            Configuración
          </Link>
        )}
      />

      {notice ? <p className="form-success" role="status">{notice}</p> : null}

      <section className="form-card" aria-label="Workflow de la Fase 1">
        <h2>Workflow «Respuesta a leads (Fase 1)»</h2>
        {workflow === null && !workflowError ? <p className="muted">Consultando…</p> : null}
        {workflow?.existe ? (
          <p>
            Existe y está{" "}
            <strong>{WORKFLOW_STATUS[workflow.status ?? ""] ?? workflow.status}</strong>.{" "}
            {workflow.url ? (
              <Link href={workflow.url} prefetch={false}>Abrir en Workflows</Link>
            ) : null}
            {workflow.status !== "active"
              ? " Hasta que esté activo (y el interruptor de Configuración ERP encendido) no se procesa ningún lead."
              : " Procesa leads mientras el interruptor de Configuración ERP esté encendido."}
          </p>
        ) : null}
        {workflow && !workflow.existe ? (
          <>
            <p>
              Todavía no existe. Se crea <strong>en borrador</strong> sobre el pipeline
              «Ventas B2B» (Nuevo lead / Descartado · spam): lead recibido → clasificar →
              esperar (ventana horaria) → preparar borrador → añadir al pipeline → crear
              tarea. Una persona lo revisa y lo activa en Workflows.
            </p>
            {!workflow.pipeline_ok && workflow.pipeline_aviso ? (
              <p className="form-error" role="alert">{workflow.pipeline_aviso}</p>
            ) : null}
            <button
              type="button"
              className="button small"
              disabled={creando || !workflow.pipeline_ok}
              onClick={crear}
            >
              {creando ? "Creando…" : "Crear el workflow"}
            </button>
          </>
        ) : null}
        {workflowError ? <p className="form-error" role="alert">{workflowError}</p> : null}
      </section>

      <section className="form-card" aria-label="Modo en seco">
        <h2>Modo en seco</h2>
        <p className="muted small">
          Clasifica los leads de los últimos N días (envíos de formulario que no son spam y
          notas «form note» de AgileCRM, por su fecha real) con el mismo proveedor y la
          misma configuración que el workflow, y dice qué habría hecho con cada uno.{" "}
          <strong>No escribe nada</strong>: ni clasificación, ni borrador, ni tarea, ni
          pipeline. Es lo que se revisa con Bart antes de encender el interruptor.
        </p>
        <div className="erp-doc-filters">
          <label className="field">
            <span>Días hacia atrás</span>
            <select
              aria-label="Días del modo en seco"
              value={diasSeco}
              onChange={(e) => setDiasSeco(Number(e.target.value))}
            >
              {DIAS_OPCIONES.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
          <button type="button" className="button small" disabled={simulando} onClick={simular}>
            {simulando ? "Simulando…" : "Simular en seco"}
          </button>
        </div>
        {errorSeco ? <p className="form-error" role="alert">{errorSeco}</p> : null}
        {informe ? <InformeEnSeco informe={informe} etiquetas={etiquetas} /> : null}
      </section>

      <section aria-label="Leads procesados">
        <h2>Leads procesados</h2>
        <div className="erp-doc-filters" role="search" aria-label="Filtros de leads">
          <label className="field">
            <span>Últimos días</span>
            <select
              aria-label="Días de la lista"
              value={dias}
              onChange={(e) => setDias(Number(e.target.value))}
            >
              {DIAS_OPCIONES.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
          {datos ? (
            <span className="muted small">
              {datos.total} procesados · {datos.corregidas} corregidos a mano · umbral de
              confianza {porcentaje(datos.umbral_confianza)}
            </span>
          ) : null}
        </div>
        {errorCarga ? <p className="form-error" role="alert">{errorCarga}</p> : null}
        {datos === null && !errorCarga ? <p className="muted">Cargando…</p> : null}
        {datos && datos.items.length === 0 ? (
          <p className="muted">
            Ningún lead procesado en los últimos {dias} días. Los leads se procesan cuando
            el workflow está activo y el interruptor encendido.
          </p>
        ) : null}
        {datos && datos.items.length > 0 ? (
          <table className="data-table data-table--responsive erp-leads-tabla">
            <thead>
              <tr>
                <th>Lead</th><th>Consulta</th><th>Idioma</th><th>Interés</th><th>Spam</th>
                <th>Confianza</th><th>Resultado</th><th>Corrección</th>
              </tr>
            </thead>
            <tbody>
              {datos.items.map((l) => (
                <FilaLead
                  // Tras corregir llega la fila nueva: la clave cambia y el
                  // estado local (los desplegables) arranca de lo corregido.
                  key={`${l.id}:${l.correccion.cuando ?? ""}`}
                  lead={l}
                  opciones={datos.opciones}
                  onCorregir={corregir}
                />
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
    </main>
  );
}

function InformeEnSeco({
  informe, etiquetas,
}: {
  informe: LeadEnSecoInforme;
  etiquetas: Record<string, string>;
}) {
  const r = informe.resumen;
  const lista = (m: Record<string, number>, nombre: (k: string) => string) => Object.entries(m)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${nombre(k)} ${v}`)
    .join(" · ");
  return (
    <div className="erp-leads-informe">
      <p role="status">
        <strong>{r.total}</strong> {r.total === 1 ? "lead" : "leads"} en {informe.dias} días
        (proveedor: {r.proveedor}): {r.spam} spam, {r.sin_plantilla} sin plantilla,{" "}
        {r.con_discrepancia_idioma} con el idioma distinto del formulario,{" "}
        {r.ya_clasificados} ya procesados de verdad. Nada escrito.
      </p>
      <p className="muted small">
        Por interés: {lista(r.por_interes, (k) => etiquetas[k] ?? k) || "—"}. Por idioma:{" "}
        {lista(r.por_idioma, (k) => k.toUpperCase()) || "—"}.
      </p>
      {informe.items.length === 0 ? <p className="muted">Ningún lead en ese plazo.</p> : (
        <table className="data-table data-table--responsive erp-leads-tabla">
          <thead>
            <tr><th>Lead</th><th>Consulta</th><th>Clasificación</th><th>Qué haría</th></tr>
          </thead>
          <tbody>
            {informe.items.map((f) => (
              <tr key={`${f.fuente}:${f.referencia}`}>
                <td data-label="Lead">
                  <Link href={`/contacts/${f.contacto_id}`} prefetch={false}>
                    {f.nombre || f.email}
                  </Link>
                  <br />
                  <span className="muted small">
                    {fechaHora(f.lead_at)} · {fuenteTexto(f.fuente, f.web, null)}
                    {f.ya_clasificado ? " · ya procesado" : ""}
                  </span>
                </td>
                <td data-label="Consulta">
                  <span className="small" title={f.texto}>{recortar(f.texto) || "(sin consulta)"}</span>
                  {f.productos.length > 0 ? (
                    <><br /><span className="muted small">Productos: {f.productos.join(", ")}</span></>
                  ) : null}
                </td>
                <td data-label="Clasificación">
                  {f.clasificacion.es_spam ? (
                    <span className="badge bad">Spam</span>
                  ) : (
                    <span className="badge active">
                      {etiquetas[f.clasificacion.interes] ?? f.clasificacion.interes}
                    </span>
                  )}{" "}
                  <span className="small">
                    {(f.clasificacion.idioma ?? "?").toUpperCase()} · {porcentaje(f.clasificacion.confianza)}
                  </span>
                  {f.clasificacion.discrepancia_idioma ? (
                    <> <span className="badge warn">≠ formulario {(f.idioma_formulario ?? "").toUpperCase()}</span></>
                  ) : null}
                  <br />
                  <span className="muted small">{f.clasificacion.motivo}</span>
                </td>
                <td data-label="Qué haría">
                  <span className="small">Etapa: {f.haria.etapa}</span>
                  {!f.clasificacion.es_spam ? (
                    <>
                      <br />
                      <span className="small">
                        Plantilla: {f.haria.plantilla ?? "ninguna"} · desde {f.haria.remitente ?? "(sin remitente)"}
                      </span>
                      <br />
                      <span className="small">{f.haria.tarea ? "Tarea para una persona" : "Sin tarea"}</span>
                    </>
                  ) : null}
                  {f.haria.aviso ? (
                    <><br /><span className="badge warn">{f.haria.aviso}</span></>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** Una fila de lead procesado. Idioma, interés y spam se corrigen a mano:
 *  solo viaja lo que cambia respecto a lo que manda (lo corregido, si lo
 *  hay; si no, lo clasificado). La original se conserva en el servidor. */
function FilaLead({
  lead, opciones, onCorregir,
}: {
  lead: LeadClasificacion;
  opciones: LeadClasificaciones["opciones"];
  onCorregir: (id: string, payload: LeadCorreccion) => Promise<void>;
}) {
  const nombre = lead.contacto.nombre || lead.contacto.email;
  const [idioma, setIdioma] = useState(lead.efectivo.idioma ?? "");
  const [interes, setInteres] = useState(lead.efectivo.interes ?? "");
  const [esSpam, setEsSpam] = useState(lead.efectivo.es_spam);
  const [nota, setNota] = useState("");
  const [ocupada, setOcupada] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cambios: LeadCorreccion = {};
  if (idioma && idioma !== (lead.efectivo.idioma ?? "")) cambios.idioma = idioma;
  if (interes && interes !== (lead.efectivo.interes ?? "")) cambios.interes = interes;
  if (esSpam !== lead.efectivo.es_spam) cambios.es_spam = esSpam;
  const hayCambios = Object.keys(cambios).length > 0;

  async function guardar() {
    setOcupada(true);
    setError(null);
    try {
      await onCorregir(lead.id, { ...cambios, ...(nota.trim() ? { nota: nota.trim() } : {}) });
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la corrección."));
    } finally {
      setOcupada(false);
    }
  }

  return (
    <tr className={lead.correccion.corregida ? "is-corregida" : undefined}>
      <td data-label="Lead">
        <Link href={`/contacts/${lead.contacto.id}`} prefetch={false}>{nombre}</Link>
        {lead.contacto.nombre ? <><br /><span className="muted small">{lead.contacto.email}</span></> : null}
        <br />
        <span className="muted small">
          {fechaHora(lead.lead_at)} · {fuenteTexto(lead.fuente, lead.web, lead.cuenta_agile)}
        </span>
      </td>
      <td data-label="Consulta">
        <span className="small" title={lead.texto}>{recortar(lead.texto) || "(sin consulta)"}</span>
        {lead.productos.length > 0 ? (
          <><br /><span className="muted small">Productos: {lead.productos.join(", ")}</span></>
        ) : null}
      </td>
      <td data-label="Idioma">
        <select
          aria-label={`Idioma de ${nombre}`}
          value={idioma}
          onChange={(e) => setIdioma(e.target.value)}
        >
          {!idioma ? <option value="">—</option> : null}
          {opciones.idiomas.map((i) => <option key={i} value={i}>{i.toUpperCase()}</option>)}
        </select>
        {lead.discrepancia_idioma && lead.idioma_formulario ? (
          <><br /><span className="badge warn">≠ formulario {lead.idioma_formulario.toUpperCase()}</span></>
        ) : null}
        {lead.idioma_fuente ? <><br /><span className="muted small">{origenDato(lead.idioma_fuente)}</span></> : null}
      </td>
      <td data-label="Interés">
        <select
          aria-label={`Interés de ${nombre}`}
          value={interes}
          onChange={(e) => setInteres(e.target.value)}
        >
          {!interes ? <option value="">—</option> : null}
          {opciones.intereses.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}
        </select>
        {lead.interes_fuente ? <><br /><span className="muted small">{origenDato(lead.interes_fuente)}</span></> : null}
      </td>
      <td data-label="Spam">
        <input
          type="checkbox"
          aria-label={`Spam de ${nombre}`}
          checked={esSpam}
          onChange={(e) => setEsSpam(e.target.checked)}
        />
      </td>
      <td data-label="Confianza">
        <span
          className={`badge ${lead.bajo_umbral ? "bad" : "active"}`}
          title={lead.bajo_umbral ? "Por debajo del umbral de confianza" : undefined}
        >
          {porcentaje(lead.confianza)}
        </span>
        {lead.motivo ? <><br /><span className="muted small">{lead.motivo}</span></> : null}
        {lead.proveedor ? <><br /><span className="muted small">{lead.proveedor}{lead.modelo ? ` · ${lead.modelo}` : ""}</span></> : null}
      </td>
      <td data-label="Resultado">
        <span className={`badge ${ESTADO_TONE[lead.estado] ?? "muted"}`}>
          {ESTADO_LABEL[lead.estado] ?? lead.estado}
        </span>
        {lead.estado_detalle ? <><br /><span className="muted small">{lead.estado_detalle}</span></> : null}
        {lead.plantilla ? <><br /><span className="small">Plantilla: {lead.plantilla}</span></> : null}
        {lead.remitente ? <><br /><span className="small">Desde: {lead.remitente}</span></> : null}
        {lead.borrador_url ? (
          <><br /><a href={lead.borrador_url}>Abrir el borrador</a></>
        ) : null}
      </td>
      <td data-label="Corrección">
        {lead.correccion.corregida ? (
          <p className="small">
            Corregido por {lead.correccion.por ?? "?"} el {fechaHora(lead.correccion.cuando)}
            {lead.correccion.nota ? ` · ${lead.correccion.nota}` : ""}
          </p>
        ) : null}
        {hayCambios ? (
          <>
            <input
              type="text"
              aria-label={`Nota de la corrección de ${nombre}`}
              placeholder="Nota (opcional)"
              maxLength={500}
              value={nota}
              onChange={(e) => setNota(e.target.value)}
            />
            <button type="button" className="button small" disabled={ocupada} onClick={guardar}>
              {ocupada ? "Guardando…" : "Guardar corrección"}
            </button>
          </>
        ) : (
          <span className="muted small">Cambia idioma, interés o spam para corregir.</span>
        )}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
      </td>
    </tr>
  );
}
