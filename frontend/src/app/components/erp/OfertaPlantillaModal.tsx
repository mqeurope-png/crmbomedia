"use client";

import { useEffect, useState } from "react";
import {
  createOfertaPlantilla,
  getOfertaPlantilla,
  updateOfertaPlantilla,
  type OfertaPlantilla,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ErpModalShell } from "./ErpModalShell";
import {
  isRowBlank,
  OfertaLineasEditor,
  rowsFromOfertaLineas,
  rowsToOfertaLineasIn,
  type OfertaLineaRow,
} from "./OfertaLineasEditor";

/** Alta y edición de una plantilla de oferta con las líneas a mano: nombre,
 *  descripción, activa y el mismo editor de líneas que la oferta. (Las
 *  plantillas copiadas de un presupuesto de FACTUSOL se crean desde Proformas;
 *  aquí se editan igual.) No escribe en FACTUSOL. */
export function OfertaPlantillaModal({
  plantilla,
  onClose,
  onSaved,
}: {
  /** null = nueva. */
  plantilla?: OfertaPlantilla | null;
  onClose: () => void;
  onSaved: (plantilla: OfertaPlantilla) => void;
}) {
  const editing = Boolean(plantilla);
  const plantillaId = plantilla?.id ?? null;
  // La lista no trae líneas: al editar desde ella se piden.
  const needsLines = Boolean(plantilla && !plantilla.lineas);

  const [nombre, setNombre] = useState(plantilla?.nombre ?? "");
  const [descripcion, setDescripcion] = useState(plantilla?.descripcion ?? "");
  const [activa, setActiva] = useState(plantilla?.activa ?? true);
  const [rows, setRows] = useState<OfertaLineaRow[]>(() => rowsFromOfertaLineas(plantilla?.lineas));
  const [loading, setLoading] = useState(needsLines);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!plantillaId || !needsLines) return;
    let alive = true;
    getOfertaPlantilla(plantillaId)
      .then((full) => { if (alive) setRows(rowsFromOfertaLineas(full.lineas)); })
      .catch((e) => {
        if (alive) setError(extractErrorMessage(e, "No se pudieron cargar las líneas de la plantilla."));
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [plantillaId, needsLines]);

  const vivas = rows.filter((r) => !isRowBlank(r));
  const lineasOk = vivas.length > 0 && vivas.every((r) => r.descripcion.trim() || r.codart);
  const valid = nombre.trim().length > 0 && lineasOk && !loading;

  async function save() {
    if (!valid) return;
    setSaving(true);
    setError(null);
    const payload = {
      nombre: nombre.trim(),
      descripcion: descripcion.trim() || null,
      activa,
      lineas: rowsToOfertaLineasIn(rows),
    };
    try {
      const saved = plantilla
        ? await updateOfertaPlantilla(plantilla.id, payload)
        : await createOfertaPlantilla(payload);
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
      label={editing ? `Editar plantilla ${plantilla?.nombre ?? ""}` : "Nueva plantilla de oferta"}
      title={editing ? "Editar plantilla" : "Nueva plantilla de oferta"}
      onClose={onClose}
      disabled={saving}
      dialogClassName="erp-modal modal-wide"
    >
      {plantilla?.origen_numero ? (
        <p className="muted small">
          Copiada del presupuesto <span className="mono">{plantilla.origen_numero}</span> de FACTUSOL.
        </p>
      ) : null}
      <label className="field">
        <span>Nombre</span>
        <input type="text" value={nombre} maxLength={160} aria-label="Nombre de la plantilla"
               placeholder="Ej: Kit arranque impresión UV"
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
      <label className="field-toggle">
        <input type="checkbox" checked={activa} aria-label="Plantilla activa"
               disabled={saving}
               onChange={(e) => setActiva(e.target.checked)} />
        <span>Activa (se ofrece al crear ofertas)</span>
      </label>

      {error ? <p className="form-error" role="alert">{error}</p> : null}

      {loading ? (
        <p className="muted">Cargando líneas…</p>
      ) : (
        <OfertaLineasEditor
          rows={rows}
          onChange={setRows}
          disabled={saving}
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
