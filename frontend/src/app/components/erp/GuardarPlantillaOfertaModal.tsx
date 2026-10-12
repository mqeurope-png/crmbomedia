"use client";

import { useState } from "react";
import { createOfertaPlantilla, type OfertaPlantilla } from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ErpModalShell } from "./ErpModalShell";

/** «Guardar como plantilla de oferta» desde un presupuesto de FACTUSOL: el
 *  servidor copia sus líneas (F_LPS) a una plantilla de BoHub con el nombre
 *  (obligatorio) y la descripción (opcional) que se dan aquí. Solo LEE de
 *  FACTUSOL; la plantilla vive en BoHub. */
export function GuardarPlantillaOfertaModal({
  serie,
  codpre,
  numero,
  referencia,
  onClose,
  onSaved,
}: {
  serie: number;
  codpre: number;
  /** Nº visible del presupuesto («5-000039») para el título. */
  numero?: string | null;
  /** Referencia del presupuesto: se propone como nombre de la plantilla. */
  referencia?: string | null;
  onClose: () => void;
  onSaved: (plantilla: OfertaPlantilla) => void;
}) {
  const [nombre, setNombre] = useState(referencia?.trim() ?? "");
  const [descripcion, setDescripcion] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = nombre.trim().length > 0;
  const etiqueta = numero || `${serie}-${codpre}`;

  async function save() {
    if (!valid || saving) return;
    setSaving(true);
    setError(null);
    try {
      const plantilla = await createOfertaPlantilla({
        nombre: nombre.trim(),
        descripcion: descripcion.trim() || null,
        serie,
        codpre,
      });
      onSaved(plantilla);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la plantilla."));
      setSaving(false);
    }
  }

  return (
    <ErpModalShell
      label={`Guardar el presupuesto ${etiqueta} como plantilla de oferta`}
      title="Guardar como plantilla de oferta"
      onClose={onClose}
      disabled={saving}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <p className="muted small">
          Se copian las líneas del presupuesto{" "}
          <span className="mono">{etiqueta}</span> a una plantilla de BoHub, para
          crear ofertas a partir de ella. No se toca nada en FACTUSOL.
        </p>
        <label className="field">
          <span>Nombre</span>
          <input
            type="text"
            value={nombre}
            maxLength={160}
            aria-label="Nombre de la plantilla"
            placeholder="Ej: Kit arranque impresión UV"
            disabled={saving}
            autoFocus
            onChange={(e) => setNombre(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Descripción (opcional)</span>
          <textarea
            value={descripcion}
            rows={2}
            maxLength={2000}
            aria-label="Descripción de la plantilla"
            placeholder="Para qué sirve, a quién se le ofrece…"
            disabled={saving}
            onChange={(e) => setDescripcion(e.target.value)}
          />
        </label>

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
