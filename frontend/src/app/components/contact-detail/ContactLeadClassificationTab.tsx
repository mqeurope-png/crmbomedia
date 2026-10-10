"use client";

import { Brain } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { formatBackendDateTimeCompact } from "../../lib/dates";
import {
  corregirLeadClasificacion,
  listContactLeadClasificaciones,
  type LeadClasificacion,
  type LeadClasificacionesContacto,
  type LeadCorreccion,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { estadoLabel, estadoTone, fuenteTexto, porcentaje } from "../../lib/leadsTextos";
import { LeadClasificacionResumen } from "./LeadClasificacionResumen";

type Props = {
  contactId: string;
  /** `erp.config`: la misma capacidad que exige el servidor para corregir. */
  canCorrect: boolean;
};

/** Pestaña «Análisis IA» de la ficha: cada lead del contacto con la
 *  consulta entera, el contexto que entró, lo que dijo la IA y la
 *  corrección a mano (la misma de ERP · Leads: `POST
 *  /api/erp/leads/clasificaciones/{id}/corregir`). La última arriba. */
export function ContactLeadClassificationTab({ contactId, canCorrect }: Props) {
  const [datos, setDatos] = useState<LeadClasificacionesContacto | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Cargando mientras no hay ni datos ni error (la página monta la pestaña
  // con `key={contact.id}`, así que un cambio de contacto arranca de cero).
  const cargando = datos === null && error === null;

  useEffect(() => {
    let vivo = true;
    listContactLeadClasificaciones(contactId)
      .then((r) => {
        if (!vivo) return;
        setDatos(r);
        setError(null);
      })
      .catch((e) => {
        if (vivo) setError(extractErrorMessage(e, "No se pudo cargar el análisis de la IA."));
      });
    return () => {
      vivo = false;
    };
  }, [contactId]);

  // La corrección: el servidor devuelve la fila ya corregida y se sustituye.
  const corregir = useCallback(async (id: string, payload: LeadCorreccion) => {
    const fila = await corregirLeadClasificacion(id, payload);
    setDatos((d) => (d ? { ...d, items: d.items.map((i) => (i.id === fila.id ? fila : i)) } : d));
  }, []);

  return (
    <article className="card card-wide contact-lead-ia-tab">
      <div className="section-title">
        <h2>
          <Brain size={16} aria-hidden /> Análisis de la IA
        </h2>
        {datos && datos.total > 0 ? (
          <span className="muted small">
            {datos.total === 1 ? "1 análisis" : `${datos.total} análisis`} · umbral de confianza{" "}
            {porcentaje(datos.umbral_confianza)}
          </span>
        ) : null}
      </div>
      {cargando ? (
        <p className="muted">Cargando…</p>
      ) : error ? (
        <p className="form-error" role="alert">{error}</p>
      ) : !datos || datos.items.length === 0 ? (
        <p className="muted">
          Este contacto no tiene ningún análisis de la IA: no ha entrado como lead
          (formulario web o AgileCRM) con la respuesta a leads activa.
        </p>
      ) : (
        <ol className="contact-lead-ia-list">
          {datos.items.map((lead, idx) => (
            <li key={lead.id}>
              <LeadClasificacionItem
                lead={lead}
                umbral={datos.umbral_confianza}
                opciones={datos.opciones}
                esUltima={idx === 0}
                canCorrect={canCorrect}
                onCorregir={corregir}
              />
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}

/** Etiquetas del contexto de entrada (`lead_classifications.input_json`).
 *  `fuente` ya va en la cabecera y `sitio` se enseña como la web. */
const CONTEXTO_LABEL: Record<string, string> = {
  formulario: "Formulario",
  idioma_formulario: "Idioma del formulario",
  productos: "Productos marcados",
  pais: "País",
  cuenta_agile: "Cuenta de AgileCRM",
  dominio_email: "Dominio del email",
  referencia: "Referencia",
};
const CONTEXTO_OCULTO = new Set(["fuente", "sitio"]);

function valorContexto(valor: unknown): string | null {
  if (valor == null || valor === "") return null;
  if (Array.isArray(valor)) return valor.length ? valor.map(String).join(", ") : null;
  if (typeof valor === "boolean") return valor ? "Sí" : "No";
  if (typeof valor === "object") return JSON.stringify(valor);
  return String(valor);
}

function filasContexto(lead: LeadClasificacion): Array<[string, string]> {
  const filas: Array<[string, string]> = [];
  if (lead.web) filas.push(["Web", lead.web]);
  for (const [clave, valor] of Object.entries(lead.contexto ?? {})) {
    if (CONTEXTO_OCULTO.has(clave)) continue;
    const texto = valorContexto(valor);
    if (texto === null) continue;
    filas.push([CONTEXTO_LABEL[clave] ?? clave.replace(/_/g, " "), texto]);
  }
  return filas;
}

function LeadClasificacionItem({
  lead, umbral, opciones, esUltima, canCorrect, onCorregir,
}: {
  lead: LeadClasificacion;
  umbral: number;
  opciones: LeadClasificacionesContacto["opciones"];
  esUltima: boolean;
  canCorrect: boolean;
  onCorregir: (id: string, payload: LeadCorreccion) => Promise<void>;
}) {
  const consulta = (lead.texto_completo ?? lead.texto ?? "").trim();
  const contexto = filasContexto(lead);
  return (
    <article className="contact-lead-ia-item" aria-label={`Análisis del lead del ${formatBackendDateTimeCompact(lead.lead_at ?? lead.creado)}`}>
      <header className="contact-lead-ia-item-header">
        <h3>Lead del {formatBackendDateTimeCompact(lead.lead_at ?? lead.creado)}</h3>
        <span className="muted small">{fuenteTexto(lead.fuente, lead.web, lead.cuenta_agile)}</span>
        {esUltima ? <span className="badge active">Último</span> : null}
        <span className={`badge ${estadoTone(lead.estado)}`}>{estadoLabel(lead.estado)}</span>
      </header>

      <section>
        <h4>Consulta del cliente</h4>
        {consulta ? (
          <blockquote className="contact-lead-ia-consulta">{consulta}</blockquote>
        ) : (
          <p className="muted small">(sin consulta: el lead llegó sin texto)</p>
        )}
      </section>

      {contexto.length > 0 ? (
        <section>
          <h4>Lo que entró con la consulta</h4>
          <dl className="contact-lead-ia-dl">
            {contexto.map(([etiqueta, valor]) => (
              <div key={etiqueta} className="contact-lead-ia-dl-row">
                <dt>{etiqueta}</dt>
                <dd>{valor}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      <section>
        <h4>Lo que dijo la IA</h4>
        <LeadClasificacionResumen lead={lead} umbral={umbral} />
      </section>

      <section>
        <h4>Corrección a mano</h4>
        {lead.correccion.corregida ? (
          <p className="small">
            Corregido por {lead.correccion.por ?? "?"} el{" "}
            {formatBackendDateTimeCompact(lead.correccion.cuando)}
            {lead.correccion.nota ? ` · ${lead.correccion.nota}` : ""}
          </p>
        ) : null}
        {canCorrect ? (
          <CorreccionForm lead={lead} opciones={opciones} onCorregir={onCorregir} />
        ) : (
          <p className="muted small">
            Corregir la clasificación requiere el permiso de configuración del ERP
            (es la misma corrección de ERP · Leads).
          </p>
        )}
      </section>
    </article>
  );
}

/** La corrección: solo viaja lo que cambia respecto a lo que manda hoy (lo
 *  corregido, si lo hay; si no, lo clasificado). La original se conserva. */
function CorreccionForm({
  lead, opciones, onCorregir,
}: {
  lead: LeadClasificacion;
  opciones: LeadClasificacionesContacto["opciones"];
  onCorregir: (id: string, payload: LeadCorreccion) => Promise<void>;
}) {
  const [idioma, setIdioma] = useState(lead.efectivo.idioma ?? "");
  const [interes, setInteres] = useState(lead.efectivo.interes ?? "");
  const [esSpam, setEsSpam] = useState(lead.efectivo.es_spam);
  // La nota arranca con la que ya hay: el servidor sustituye la nota entera
  // en cada corrección, así que una segunda corrección sin tocarla la
  // conserva (y borrarla a mano la quita de verdad).
  const [nota, setNota] = useState(lead.correccion.nota ?? "");
  const [ocupada, setOcupada] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [guardado, setGuardado] = useState(false);

  const cambios: LeadCorreccion = {};
  if (idioma && idioma !== (lead.efectivo.idioma ?? "")) cambios.idioma = idioma;
  if (interes && interes !== (lead.efectivo.interes ?? "")) cambios.interes = interes;
  if (esSpam !== lead.efectivo.es_spam) cambios.es_spam = esSpam;
  const hayCambios = Object.keys(cambios).length > 0;
  const sufijo = formatBackendDateTimeCompact(lead.lead_at ?? lead.creado);

  async function guardar() {
    setOcupada(true);
    setError(null);
    setGuardado(false);
    try {
      await onCorregir(lead.id, { ...cambios, ...(nota.trim() ? { nota: nota.trim() } : {}) });
      setGuardado(true);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la corrección."));
    } finally {
      setOcupada(false);
    }
  }

  return (
    <div className="contact-lead-ia-correccion">
      <label>
        Idioma
        <select
          aria-label={`Idioma del lead del ${sufijo}`}
          value={idioma}
          onChange={(e) => setIdioma(e.target.value)}
        >
          {!idioma ? <option value="">—</option> : null}
          {opciones.idiomas.map((i) => <option key={i} value={i}>{i.toUpperCase()}</option>)}
        </select>
      </label>
      <label>
        Interés
        <select
          aria-label={`Interés del lead del ${sufijo}`}
          value={interes}
          onChange={(e) => setInteres(e.target.value)}
        >
          {!interes ? <option value="">—</option> : null}
          {opciones.intereses.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}
        </select>
      </label>
      <label className="is-check">
        <input
          type="checkbox"
          aria-label={`Spam del lead del ${sufijo}`}
          checked={esSpam}
          onChange={(e) => setEsSpam(e.target.checked)}
        />
        Spam
      </label>
      {hayCambios ? (
        <>
          <label>
            Nota
            <input
              type="text"
              aria-label={`Nota de la corrección del lead del ${sufijo}`}
              placeholder="Nota (opcional)"
              maxLength={500}
              value={nota}
              onChange={(e) => setNota(e.target.value)}
            />
          </label>
          <button type="button" className="button small" disabled={ocupada} onClick={guardar}>
            {ocupada ? "Guardando…" : "Guardar corrección"}
          </button>
        </>
      ) : (
        <span className="muted small">
          {guardado ? "Corrección guardada." : "Cambia idioma, interés o spam para corregir."}
        </span>
      )}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
    </div>
  );
}
