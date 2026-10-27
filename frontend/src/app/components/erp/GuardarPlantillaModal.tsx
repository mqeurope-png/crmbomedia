"use client";

import { useEffect, useState } from "react";
import {
  createPlantilla,
  getPlantillasMeta,
  listPlantillaCarpetas,
  listPlantillaEtiquetas,
  type OfertaIdioma,
  type PlantillaDocumento,
  type PlantillaOrigenIn,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ErpModalShell } from "./ErpModalShell";
import { EtiquetasInput } from "./EtiquetasInput";
import { flattenCarpetas, IDIOMAS_PLANTILLA, type CarpetaPlana } from "./plantillasUi";

const DOC_LABEL: Record<PlantillaOrigenIn["tipo"], string> = {
  presupuesto: "la proforma",
  albaran: "el albarán",
  pedido: "el pedido",
};

/** «Guardar como plantilla» desde una proforma o un albarán de FACTUSOL o un
 *  pedido de BoHub: el servidor copia sus líneas (con sus artículos de
 *  FACTUSOL) a una plantilla de documento, que luego sirve para crear una
 *  oferta, una proforma o un pedido. Nombre, descripción, idioma, carpeta
 *  (por defecto, la del usuario) y etiquetas. Solo LEE el documento; no se
 *  escribe nada en FACTUSOL. */
export function GuardarPlantillaModal({
  origen,
  numero,
  nombreSugerido,
  idiomaSugerido,
  onClose,
  onSaved,
}: {
  origen: PlantillaOrigenIn;
  /** Nº visible del documento («5-000039», «PED-2026-0012») para el título. */
  numero?: string | null;
  /** Se propone como nombre (la referencia del documento, p. ej.). */
  nombreSugerido?: string | null;
  idiomaSugerido?: OfertaIdioma | null;
  onClose: () => void;
  onSaved: (plantilla: PlantillaDocumento) => void;
}) {
  const [nombre, setNombre] = useState(nombreSugerido?.trim() ?? "");
  const [descripcion, setDescripcion] = useState("");
  const [idioma, setIdioma] = useState<OfertaIdioma>(idiomaSugerido ?? "es");
  const [carpetas, setCarpetas] = useState<CarpetaPlana[]>([]);
  const [carpetaId, setCarpetaId] = useState("");
  const [restringido, setRestringido] = useState(false);
  const [etiquetas, setEtiquetas] = useState<string[]>([]);
  const [sugerencias, setSugerencias] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = nombre.trim().length > 0 && (!restringido || Boolean(carpetaId));
  const doc = `${DOC_LABEL[origen.tipo]}${numero ? ` ${numero}` : ""}`;

  useEffect(() => {
    let alive = true;
    Promise.all([
      getPlantillasMeta(),
      listPlantillaCarpetas(),
      listPlantillaEtiquetas().catch(() => []),
    ])
      .then(([meta, arbol, etqs]) => {
        if (!alive) return;
        const planas = flattenCarpetas(arbol.items);
        setCarpetas(planas);
        setRestringido(meta.acceso_restringido);
        const defecto = meta.carpeta_defecto && planas.some((c) => c.id === meta.carpeta_defecto)
          ? meta.carpeta_defecto : "";
        setCarpetaId(defecto || (meta.acceso_restringido ? (planas[0]?.id ?? "") : ""));
        setSugerencias(etqs.map((e) => e.etiqueta));
      })
      .catch(() => { /* sin carpetas: se guarda en la raíz */ });
    return () => { alive = false; };
  }, []);

  async function save() {
    if (!valid || saving) return;
    setSaving(true);
    setError(null);
    try {
      const plantilla = await createPlantilla({
        nombre: nombre.trim(),
        descripcion: descripcion.trim() || null,
        idioma,
        carpeta_id: carpetaId || null,
        etiquetas,
        origen,
      });
      onSaved(plantilla);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la plantilla."));
      setSaving(false);
    }
  }

  return (
    <ErpModalShell
      label={`Guardar ${doc} como plantilla`}
      title="Guardar como plantilla"
      onClose={onClose}
      disabled={saving}
    >
      <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <p className="muted small">
          Se copian las líneas de {DOC_LABEL[origen.tipo]}{" "}
          {numero ? <span className="mono">{numero}</span> : null} (con sus
          artículos) a una plantilla de BoHub. Sirve para crear una oferta, una
          proforma o un pedido. No se toca nada en FACTUSOL.
        </p>
        <label className="field">
          <span>Nombre</span>
          <input type="text" value={nombre} maxLength={160} aria-label="Nombre de la plantilla"
                 placeholder="Ej: Kit arranque DTF 60 cm" disabled={saving} autoFocus
                 onChange={(e) => setNombre(e.target.value)} />
        </label>
        <label className="field">
          <span>Descripción (opcional)</span>
          <textarea value={descripcion} rows={2} maxLength={2000}
                    aria-label="Descripción de la plantilla"
                    placeholder="Para qué sirve, a quién se le ofrece…" disabled={saving}
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
              {carpetas.map((c) => (
                <option key={c.id} value={c.id}>{c.ruta}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="field">
          <span>Etiquetas</span>
          <EtiquetasInput value={etiquetas} onChange={setEtiquetas} sugerencias={sugerencias}
                          disabled={saving} />
        </div>

        {error ? <p className="form-error" role="alert">{error}</p> : null}

        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={saving}>
            Cancelar
          </button>
          <button type="submit" className="button" disabled={!valid || saving}>
            {saving ? "Guardando…" : "Guardar plantilla"}
          </button>
        </div>
      </form>
    </ErpModalShell>
  );
}
