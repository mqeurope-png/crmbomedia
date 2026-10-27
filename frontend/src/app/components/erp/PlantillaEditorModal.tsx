"use client";

import { useEffect, useState } from "react";
import {
  createPlantilla,
  getPlantilla,
  getPlantillasMeta,
  listPlantillaCarpetas,
  listPlantillaEtiquetas,
  updatePlantilla,
  type OfertaIdioma,
  type PlantillaDocumento,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ErpModalShell } from "./ErpModalShell";
import { EtiquetasInput } from "./EtiquetasInput";
import {
  isRowBlank,
  OfertaLineasEditor,
  rowsFromOfertaLineas,
  rowsToOfertaLineasIn,
  type OfertaLineaRow,
} from "./OfertaLineasEditor";
import { flattenCarpetas, IDIOMAS_PLANTILLA, origenTexto, type CarpetaPlana } from "./plantillasUi";

/** Alta y edición de una plantilla de documento: nombre, descripción, idioma,
 *  carpeta (moverla), etiquetas, activa y el mismo editor de líneas que la
 *  oferta (artículos de FACTUSOL del catálogo). Las guardadas desde una
 *  proforma, un albarán o un pedido se editan igual. No escribe en FACTUSOL. */
export function PlantillaEditorModal({
  plantilla,
  carpetaInicial,
  onClose,
  onSaved,
}: {
  /** null = nueva. */
  plantilla?: PlantillaDocumento | null;
  /** Para una nueva: la carpeta abierta en la pantalla. */
  carpetaInicial?: string | null;
  onClose: () => void;
  onSaved: (plantilla: PlantillaDocumento) => void;
}) {
  const editing = Boolean(plantilla);
  const plantillaId = plantilla?.id ?? null;
  // La lista no trae líneas: al editar desde ella se piden.
  const needsLines = Boolean(plantilla && !plantilla.lineas);

  const [nombre, setNombre] = useState(plantilla?.nombre ?? "");
  const [descripcion, setDescripcion] = useState(plantilla?.descripcion ?? "");
  const [idioma, setIdioma] = useState<OfertaIdioma>(plantilla?.idioma ?? "es");
  const [carpetaId, setCarpetaId] = useState(plantilla ? (plantilla.carpeta_id ?? "") : (carpetaInicial ?? ""));
  const [carpetas, setCarpetas] = useState<CarpetaPlana[]>([]);
  const [restringido, setRestringido] = useState(false);
  const [etiquetas, setEtiquetas] = useState<string[]>(plantilla?.etiquetas ?? []);
  const [sugerencias, setSugerencias] = useState<string[]>([]);
  const [activa, setActiva] = useState(plantilla?.activa ?? true);
  const [rows, setRows] = useState<OfertaLineaRow[]>(() => rowsFromOfertaLineas(plantilla?.lineas));
  const [loading, setLoading] = useState(needsLines);
  const [lineasFallidas, setLineasFallidas] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      getPlantillasMeta().catch(() => null),
      listPlantillaCarpetas().catch(() => ({ items: [], carpeta_defecto: null })),
      listPlantillaEtiquetas().catch(() => []),
    ]).then(([meta, arbol, etqs]) => {
      if (!alive) return;
      const planas = flattenCarpetas(arbol.items);
      setCarpetas(planas);
      setSugerencias(etqs.map((e) => e.etiqueta));
      const acotado = Boolean(meta?.acceso_restringido);
      setRestringido(acotado);
      if (!editing) {
        // Nueva: la carpeta abierta; si no, la del usuario; acotado, la primera suya.
        setCarpetaId((cur) => cur || arbol.carpeta_defecto || (acotado ? (planas[0]?.id ?? "") : ""));
      }
    });
    return () => { alive = false; };
  }, [editing]);

  useEffect(() => {
    if (!plantillaId || !needsLines) return;
    let alive = true;
    getPlantilla(plantillaId)
      .then((full) => { if (alive) setRows(rowsFromOfertaLineas(full.lineas)); })
      .catch((e) => {
        if (!alive) return;
        setLineasFallidas(true);
        setError(extractErrorMessage(e, "No se pudieron cargar las líneas de la plantilla."));
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [plantillaId, needsLines]);

  const vivas = rows.filter((r) => !isRowBlank(r));
  const lineasOk = vivas.length > 0 && vivas.every((r) => r.descripcion.trim() || r.codart);
  const carpetaOk = !restringido || Boolean(carpetaId);
  const valid = nombre.trim().length > 0 && lineasOk && carpetaOk && !loading && !lineasFallidas;
  const origen = plantilla ? origenTexto(plantilla) : null;

  async function save() {
    if (!valid) return;
    setSaving(true);
    setError(null);
    const payload = {
      nombre: nombre.trim(),
      descripcion: descripcion.trim() || null,
      idioma,
      carpeta_id: carpetaId || null,
      etiquetas,
      activa,
      lineas: rowsToOfertaLineasIn(rows),
    };
    try {
      const saved = plantilla
        ? await updatePlantilla(plantilla.id, payload)
        : await createPlantilla(payload);
      onSaved(saved);
    } catch (e) {
      setError(extractErrorMessage(
        e, plantilla ? "No se pudo guardar la plantilla." : "No se pudo crear la plantilla.",
      ));
      setSaving(false);
    }
  }

  return (
    <ErpModalShell
      label={editing ? `Editar plantilla ${plantilla?.nombre ?? ""}` : "Nueva plantilla"}
      title={editing ? "Editar plantilla" : "Nueva plantilla"}
      onClose={onClose}
      disabled={saving}
      dialogClassName="erp-modal modal-wide"
    >
      {origen ? (
        <p className="muted small">
          {origen}. Las líneas se copiaron con sus artículos de FACTUSOL.
        </p>
      ) : null}
      <label className="field">
        <span>Nombre</span>
        <input type="text" value={nombre} maxLength={160} aria-label="Nombre de la plantilla"
               placeholder="Ej: Kit arranque DTF 60 cm"
               disabled={saving}
               onChange={(e) => setNombre(e.target.value)} />
      </label>
      <label className="field">
        <span>Descripción (opcional)</span>
        <textarea value={descripcion} rows={2} maxLength={2000}
                  aria-label="Descripción de la plantilla"
                  placeholder="Para qué sirve, a quién se le ofrece…"
                  disabled={saving}
                  onChange={(e) => setDescripcion(e.target.value)} />
      </label>
      <div className="form-row">
        <label className="field">
          <span>Idioma</span>
          <select value={idioma} aria-label="Idioma de la plantilla" disabled={saving}
                  onChange={(e) => setIdioma(e.target.value as OfertaIdioma)}>
            {IDIOMAS_PLANTILLA.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Carpeta</span>
          <select value={carpetaId} aria-label="Carpeta de la plantilla" disabled={saving}
                  onChange={(e) => setCarpetaId(e.target.value)}>
            {!restringido ? <option value="">— Sin carpeta —</option> : null}
            {carpetaId && !carpetas.some((c) => c.id === carpetaId) ? (
              <option value={carpetaId}>{plantilla?.carpeta ?? "Carpeta actual"}</option>
            ) : null}
            {carpetas.map((c) => <option key={c.id} value={c.id}>{c.ruta}</option>)}
          </select>
        </label>
        <label className="field-toggle">
          <input type="checkbox" checked={activa} aria-label="Plantilla activa"
                 disabled={saving}
                 onChange={(e) => setActiva(e.target.checked)} />
          <span>Activa (se ofrece al crear)</span>
        </label>
      </div>
      <div className="field">
        <span>Etiquetas</span>
        <EtiquetasInput value={etiquetas} onChange={setEtiquetas} sugerencias={sugerencias}
                        disabled={saving} />
      </div>

      {error ? <p className="form-error" role="alert">{error}</p> : null}

      {loading ? (
        <p className="muted">Cargando líneas…</p>
      ) : (
        <OfertaLineasEditor
          rows={rows}
          onChange={setRows}
          disabled={saving || lineasFallidas}
          ariaLabel="Líneas de la plantilla"
        />
      )}

      <div className="modal-actions">
        <button type="button" className="button secondary" onClick={onClose} disabled={saving}>
          Cancelar
        </button>
        <button type="button" className="button" onClick={() => void save()}
                disabled={!valid || saving}>
          {saving ? "Guardando…" : editing ? "Guardar cambios" : "Crear plantilla"}
        </button>
      </div>
    </ErpModalShell>
  );
}
