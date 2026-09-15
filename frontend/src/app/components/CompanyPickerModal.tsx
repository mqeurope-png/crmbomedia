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

/** Modal «Asignar empresa» de la ficha de contacto (rediseño de flujo, Fase 2
 *  · revisión Lote 2).
 *
 *  Usa el molde de modal del ERP (`modal-dialog erp-modal wide`) con ancho
 *  FIJO de 720 px tanto buscando como creando: el diálogo no cambia de tamaño
 *  al aparecer resultados, porque la lista scrollea dentro de `.modal-body`
 *  (y en móvil es una hoja desde abajo, por CSS del molde). Dentro va el
 *  MISMO buscador unificado que el alta de contacto; «Crear empresa «…»»
 *  abre el formulario completo con el texto tecleado como nombre y la elige
 *  al guardar. Mantiene el contrato `onPick(id, label)` de siempre. */
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

  const creating = createName !== null;

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className={`modal-dialog erp-modal wide company-picker-dialog ${creating ? "is-creating" : "is-searching"}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="company-picker-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="company-picker-head">
          <h2 id="company-picker-title">
            {creating ? "Crear empresa" : "Asignar empresa"}
            <span className="muted">
              {creating
                ? "Los datos fiscales mandan: de aquí salen pedidos, albaranes y facturas."
                : "Busca por nombre, CIF, NIF-IVA o dominio de email."}
            </span>
          </h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Cerrar">
            ✕
          </button>
        </div>
        <div className="modal-body">
          {notice ? <p className="form-info" role="status">{notice}</p> : null}
          {creating ? (
            <CompanyCreateForm
              compact
              initialName={createName ?? ""}
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
    </div>
  );
}
