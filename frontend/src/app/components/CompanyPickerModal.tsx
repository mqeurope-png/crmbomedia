"use client";

import { useEffect, useState } from "react";
import { CompanyCreateForm, type CompanyCreated } from "./CompanyCreateForm";
import { CompanySearch } from "./CompanySearch";

type Props = {
  open: boolean;
  onClose: () => void;
  /** Called with the picked / created company id (or NULL to clear). */
  onPick: (companyId: string | null, label: string) => void;
};

/** Modal «Asignar empresa» de la ficha de contacto (rediseño de flujo, Fase 2).
 *
 *  Antes anidaba dos overlays fijos (`email-compose-overlay` + `modal-backdrop`,
 *  que también es un overlay a pantalla completa) y usaba clases `.btn` que no
 *  existen en el CSS: salía descuadrado y con los botones sin estilo. Ahora
 *  usa el modal estándar (`modal-overlay` + `modal-dialog`) y dentro el MISMO
 *  buscador unificado que el alta de contacto; «Crear empresa nueva» abre el
 *  formulario completo de «Crear empresa» sin salir del modal y la elige al
 *  guardar. Mantiene el contrato `onPick(id, label)` de siempre. */
export function CompanyPickerModal({ open, onClose, onPick }: Props) {
  const [createName, setCreateName] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Al cerrar se vuelve al buscador (el modal se reabre limpio).
  useEffect(() => {
    if (!open) { setCreateName(null); setNotice(null); }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  function onCreated({ company, factusolError }: CompanyCreated) {
    if (factusolError) {
      // La empresa existe y se asigna igual; lo de FACTUSOL se resuelve
      // desde su ficha.
      setNotice(`Empresa creada, pero no se pudo dar de alta en FACTUSOL: ${factusolError}`);
    }
    onPick(company.id, company.name);
    onClose();
  }

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        // Lote 2 · E6: molde plano del ERP; 520 px buscando y 720 (`wide`)
        // con el formulario de «Crear empresa» abierto.
        className={`modal-dialog erp-modal company-picker-dialog${createName !== null ? " wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="company-picker-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="company-picker-head">
          <h2 id="company-picker-title">
            {createName !== null ? "Crear empresa" : "Asignar empresa"}
          </h2>
          <button type="button" className="button small secondary" onClick={onClose}
                  aria-label="Cerrar">
            ✕
          </button>
        </div>
        {notice ? <p className="form-info" role="status">{notice}</p> : null}
        {createName !== null ? (
          <CompanyCreateForm
            compact
            initialName={createName}
            onCreated={onCreated}
            onCancel={() => setCreateName(null)}
            onUseExisting={(c) => { onPick(c.id, c.name); onClose(); }}
          />
        ) : (
          <CompanySearch
            autoFocus
            label="Buscar empresa"
            onPick={(c) => { onPick(c.id, c.name); onClose(); }}
            onCreate={(name) => setCreateName(name)}
          />
        )}
      </div>
    </div>
  );
}
