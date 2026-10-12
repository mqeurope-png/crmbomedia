"use client";

import { useEffect, useMemo, useState } from "react";
import {
  createOferta,
  FACTUSOL_SERIES,
  getOferta,
  getOfertaPlantilla,
  getOfertasMeta,
  listOfertaPlantillas,
  updateOferta,
  type Oferta,
  type OfertaIdioma,
  type OfertaPlantilla,
  type OfertasMeta,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { CrmContactSearch } from "./CrmContactSearch";
import { ErpModalShell } from "./ErpModalShell";
import {
  isRowBlank,
  ofertaTotales,
  OfertaLineasEditor,
  rowsFromOfertaLineas,
  rowsToOfertaLineasIn,
  type OfertaLineaRow,
} from "./OfertaLineasEditor";

const IDIOMAS: { value: OfertaIdioma; label: string }[] = [
  { value: "es", label: "ES · Español" },
  { value: "en", label: "EN · English" },
  { value: "de", label: "DE · Deutsch" },
  { value: "fr", label: "FR · Français" },
  { value: "nl", label: "NL · Nederlands" },
];
const VALIDEZ_MIN = 1;
const VALIDEZ_MAX = 365;
/** Mientras no llega `meta` (el valor de Configuración ERP · Ofertas). */
const DEFAULT_VALIDEZ = 30;

type Contacto = { id: string; nombre: string };

function contactoInicial(
  oferta: Oferta | null | undefined, contactId: string | null | undefined,
  contactName: string | null | undefined,
): Contacto | null {
  if (oferta?.contacto) return { id: oferta.contacto.id, nombre: oferta.contacto.nombre };
  if (oferta?.contact_id) return { id: oferta.contact_id, nombre: contactName ?? "" };
  if (contactId) return { id: contactId, nombre: contactName ?? "" };
  return null;
}

/** Alta y edición de una oferta (propuesta comercial en BoHub, antes del
 *  presupuesto de FACTUSOL). Crea si `oferta` es null; si no, edita. Con
 *  `contactId` el contacto va fijo (ficha del contacto); sin él se elige con
 *  el buscador del CRM. Plantilla (vuelca sus líneas), empresa emisora,
 *  idioma, con/sin IVA, validez, referencia, notas y el editor de líneas con
 *  totales en vivo. Guardar → `createOferta` / `updateOferta` → `onSaved`.
 *  No escribe en FACTUSOL. */
export function OfertaEditorModal({
  contactId,
  contactName,
  oferta,
  onClose,
  onSaved,
  marcarPosiciones,
  readOnly = false,
}: {
  contactId?: string | null;
  contactName?: string | null;
  oferta?: Oferta | null;
  onClose: () => void;
  onSaved: (oferta: Oferta) => void;
  /** Posiciones (1-based) de las líneas que «Convertir en presupuesto»
   *  rechazó por no tener artículo: se abren resaltadas. */
  marcarPosiciones?: number[];
  /** Solo ver (el usuario no tiene `erp.proformas`): campos deshabilitados y
   *  sin botón de guardar, como una convertida. */
  readOnly?: boolean;
}) {
  const editing = Boolean(oferta);
  const ofertaId = oferta?.id ?? null;
  // La lista no trae líneas: al editar desde ella se piden con `getOferta`.
  const needsLines = Boolean(oferta && !oferta.lineas);
  const fixedContact = editing || Boolean(contactId);
  const convertida = oferta?.estado === "convertida";
  const soloLectura = convertida || readOnly;

  const [contact, setContact] = useState<Contacto | null>(
    () => contactoInicial(oferta, contactId, contactName),
  );
  const [meta, setMeta] = useState<OfertasMeta | null>(null);
  const [plantillas, setPlantillas] = useState<OfertaPlantilla[]>([]);
  const [plantillaId, setPlantillaId] = useState(oferta?.plantilla_id ?? "");
  const [loadingPlantilla, setLoadingPlantilla] = useState(false);
  const [serie, setSerie] = useState<number>(oferta?.serie ?? FACTUSOL_SERIES[0].value);
  // "" = automático (el del contacto → empresa → país → emisora), solo en el alta.
  const [idioma, setIdioma] = useState<OfertaIdioma | "">(oferta?.idioma ?? "");
  const [conIva, setConIva] = useState(oferta?.con_iva ?? true);
  const [validez, setValidez] = useState(String(oferta?.validez_dias ?? DEFAULT_VALIDEZ));
  // Solo al editar: vacía = al guardar la fecha pasa a hoy (regla del backend).
  const [fecha, setFecha] = useState("");
  const [referencia, setReferencia] = useState(oferta?.referencia ?? "");
  const [notas, setNotas] = useState(oferta?.notas ?? "");
  const [rows, setRows] = useState<OfertaLineaRow[]>(() => rowsFromOfertaLineas(oferta?.lineas));
  const [loadingOferta, setLoadingOferta] = useState(needsLines);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Meta: series emisoras con nombre, validez por defecto, idiomas.
  useEffect(() => {
    let alive = true;
    getOfertasMeta()
      .then((m) => {
        if (!alive) return;
        setMeta(m);
        if (!editing) {
          setValidez(String(m.validez_dias));
          setSerie((cur) => (m.series.some((s) => s.serie === cur) ? cur : (m.series[0]?.serie ?? cur)));
        }
      })
      .catch(() => { /* sin meta: series de siempre y validez por defecto */ });
    return () => { alive = false; };
  }, [editing]);

  // Plantillas activas para el selector.
  useEffect(() => {
    let alive = true;
    listOfertaPlantillas(true)
      .then((items) => { if (alive) setPlantillas(items); })
      .catch(() => { if (alive) setPlantillas([]); });
    return () => { alive = false; };
  }, []);

  // Edición desde la lista (sin líneas): se piden.
  useEffect(() => {
    if (!ofertaId || !needsLines) return;
    let alive = true;
    getOferta(ofertaId)
      .then((full) => {
        if (!alive) return;
        setRows(rowsFromOfertaLineas(full.lineas));
      })
      .catch((e) => {
        if (alive) setError(extractErrorMessage(e, "No se pudieron cargar las líneas de la oferta."));
      })
      .finally(() => { if (alive) setLoadingOferta(false); });
    return () => { alive = false; };
  }, [ofertaId, needsLines]);

  /** Elegir plantilla: carga sus líneas y las vuelca (pidiendo confirmación
   *  si ya hay líneas escritas). */
  async function choosePlantilla(id: string) {
    if (!id) {
      setPlantillaId("");
      return;
    }
    if (rows.some((r) => !isRowBlank(r))
        && !window.confirm("Se sustituirán las líneas actuales por las de la plantilla. ¿Seguir?")) {
      return;
    }
    setLoadingPlantilla(true);
    setError(null);
    try {
      const p = await getOfertaPlantilla(id);
      setRows(rowsFromOfertaLineas(p.lineas));
      setPlantillaId(id);
      setNotice(`Líneas cargadas de la plantilla «${p.nombre}» (${p.lineas?.length ?? 0}).`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cargar la plantilla."));
    } finally {
      setLoadingPlantilla(false);
    }
  }

  const totales = useMemo(() => ofertaTotales(rows, conIva), [rows, conIva]);
  const validezNum = Number(validez);
  const validezOk = Number.isInteger(validezNum) && validezNum >= VALIDEZ_MIN && validezNum <= VALIDEZ_MAX;
  const vivas = rows.filter((r) => !isRowBlank(r));
  const lineasOk = vivas.length > 0 && vivas.every((r) => r.descripcion.trim() || r.codart);
  const valid = Boolean(contact) && lineasOk && validezOk && !soloLectura && !loadingOferta;
  const series = meta?.series ?? FACTUSOL_SERIES.map((s) => ({ serie: s.value, nombre: s.label }));

  async function save() {
    if (!valid || !contact) return;
    setSaving(true);
    setError(null);
    const comunes = {
      serie,
      lineas: rowsToOfertaLineasIn(rows),
      con_iva: conIva,
      validez_dias: validezNum,
      referencia: referencia.trim() || null,
      notas: notas.trim() || null,
    };
    try {
      const saved = oferta
        ? await updateOferta(oferta.id, {
          ...comunes,
          idioma: idioma || oferta.idioma,
          // Solo si se cambió a mano: vacía, el backend la pone a hoy.
          ...(fecha ? { fecha } : {}),
        })
        : await createOferta({
          contact_id: contact.id,
          ...comunes,
          ...(plantillaId ? { plantilla_id: plantillaId } : {}),
          ...(idioma ? { idioma } : {}),
        });
      onSaved(saved);
    } catch (e) {
      setError(extractErrorMessage(
        e, oferta ? "No se pudo guardar la oferta." : "No se pudo crear la oferta.",
      ));
      setSaving(false);
    }
  }

  return (
    <ErpModalShell
      label={editing ? `Editar oferta ${oferta?.numero ?? ""}` : "Nueva oferta"}
      title={editing ? <>Oferta <span className="mono">{oferta?.numero}</span></> : "Nueva oferta"}
      onClose={onClose}
      disabled={saving}
      dialogClassName="erp-modal modal-wide"
    >
      <div className="erp-quote-target">
        <span>
          Contacto: <strong>{contact?.nombre || "— sin contacto —"}</strong>
          {oferta?.empresa ? <span className="muted small"> · {oferta.empresa.nombre}</span> : null}
        </span>
        {!fixedContact && contact ? (
          <button type="button" className="button small secondary" disabled={saving}
                  onClick={() => setContact(null)}>
            Cambiar
          </button>
        ) : null}
      </div>
      {!fixedContact && !contact ? (
        <>
          <p className="muted small">
            La oferta va a nombre de un contacto del CRM (no hace falta cliente
            de FACTUSOL ni datos fiscales).
          </p>
          <CrmContactSearch
            pickLabel="Elegir"
            // También los contactos sin email: una oferta no lo necesita
            // (solo para enviarla).
            conEmail={false}
            disabled={saving}
            onAdd={(c) => setContact({ id: c.id, nombre: c.name })}
          />
        </>
      ) : null}

      {convertida ? (
        <p className="form-info" role="status">
          Esta oferta ya se convirtió en el presupuesto{" "}
          <strong>{oferta?.factusol?.numero ?? ""}</strong>: se puede ver, pero no editar.
        </p>
      ) : readOnly ? (
        <p className="form-info" role="status">
          Solo lectura: tu perfil no edita ofertas.
        </p>
      ) : null}

      <div className="form-row">
        <label className="field">
          <span>Plantilla</span>
          <select value={plantillaId} aria-label="Plantilla"
                  disabled={saving || loadingPlantilla || soloLectura}
                  onChange={(e) => void choosePlantilla(e.target.value)}>
            <option value="">— Sin plantilla —</option>
            {plantillas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.nombre} ({p.num_lineas} {p.num_lineas === 1 ? "línea" : "líneas"})
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Empresa emisora (serie)</span>
          <select value={serie} aria-label="Empresa emisora (serie)"
                  title="Desde qué empresa de la casa sale la oferta (y, al convertir, el presupuesto)."
                  disabled={saving || soloLectura}
                  onChange={(e) => setSerie(Number(e.target.value))}>
            {series.map((s) => (
              <option key={s.serie} value={s.serie}>{s.serie} · {s.nombre}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="form-row">
        <label className="field">
          <span>Idioma</span>
          <select value={idioma} aria-label="Idioma" disabled={saving || soloLectura}
                  onChange={(e) => setIdioma(e.target.value as OfertaIdioma | "")}>
            {!editing ? <option value="">Automático (según el contacto)</option> : null}
            {IDIOMAS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Validez (días)</span>
          <input type="number" min={VALIDEZ_MIN} max={VALIDEZ_MAX} step="1" value={validez}
                 aria-label="Validez en días" aria-invalid={!validezOk}
                 disabled={saving || soloLectura}
                 onChange={(e) => setValidez(e.target.value)} />
          {!validezOk ? (
            <span className="form-error small">Entre {VALIDEZ_MIN} y {VALIDEZ_MAX} días.</span>
          ) : null}
        </label>
        <label className="field-toggle">
          <input type="checkbox" checked={conIva} aria-label="Con IVA"
                 disabled={saving || soloLectura}
                 onChange={(e) => setConIva(e.target.checked)} />
          <span>Con IVA</span>
        </label>
      </div>

      {editing ? (
        <label className="field">
          <span>Fecha (opcional)</span>
          <input type="date" value={fecha} aria-label="Fecha de la oferta"
                 disabled={saving || soloLectura}
                 onChange={(e) => setFecha(e.target.value)} />
          <span className="muted small">
            Si la cambias a mano, manda; si no, al guardar pasa a hoy y la fecha
            de validez se recalcula.
          </span>
        </label>
      ) : null}

      <label className="field">
        <span>Referencia (opcional)</span>
        <input type="text" value={referencia} maxLength={250} aria-label="Referencia"
               placeholder="Ej: nº de consulta, proyecto, obra…"
               disabled={saving || soloLectura}
               onChange={(e) => setReferencia(e.target.value)} />
      </label>
      <label className="field">
        <span>Notas (opcional)</span>
        <textarea value={notas} rows={3} aria-label="Notas" maxLength={2000}
                  placeholder="Condiciones, plazos, lo que el cliente debe saber…"
                  disabled={saving || soloLectura}
                  onChange={(e) => setNotas(e.target.value)} />
      </label>

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {notice ? <p className="form-info" role="status">{notice}</p> : null}

      {loadingOferta ? (
        <p className="muted">Cargando líneas…</p>
      ) : (
        <OfertaLineasEditor
          rows={rows}
          onChange={setRows}
          disabled={saving || soloLectura}
          marcadas={marcarPosiciones}
        />
      )}

      <div className="erp-manual-total" role="group" aria-label="Totales">
        <span>Base: <strong>{totales.base.toFixed(2)} €</strong></span>
        {" · "}
        <span>IVA {totales.iva_pct} %: <strong>{totales.iva.toFixed(2)} €</strong></span>
        {" · "}
        <span>Total: <strong>{totales.total.toFixed(2)} €</strong></span>
      </div>
      <p className="muted small">
        La oferta vive en BoHub y no toca FACTUSOL: el presupuesto se crea solo
        con «Convertir en presupuesto», cuando el cliente acepte.
      </p>

      <div className="modal-actions">
        <button type="button" className="button secondary" onClick={onClose} disabled={saving}>
          {soloLectura ? "Cerrar" : "Cancelar"}
        </button>
        {!soloLectura ? (
          <button type="button" className="button" onClick={() => void save()}
                  disabled={!valid || saving}>
            {saving ? "Guardando…" : editing ? "Guardar cambios" : "Crear oferta"}
          </button>
        ) : null}
      </div>
    </ErpModalShell>
  );
}
