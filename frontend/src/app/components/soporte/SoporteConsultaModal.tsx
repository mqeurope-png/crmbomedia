"use client";

import { ErpModalShell } from "../erp/ErpModalShell";
import { SoporteBusqueda } from "./SoporteBusqueda";

/** «Consulta de soporte» de la Cola SAT: el buscador del soporte de los
 *  fabricantes en modo CONSULTA (sin `onUsar`). El taller busca y lee el
 *  procedimiento; desde aquí no sale nada (el rol `sat` no envía correos,
 *  #562) ni se mete en ningún borrador. */
export function SoporteConsultaModal({ onClose }: { onClose: () => void }) {
  return (
    <ErpModalShell label="Consulta de soporte" title="Consulta de soporte" onClose={onClose}
      dialogClassName="erp-modal wide soporte-consulta-modal">
      <p className="muted small">
        Busca y lee los artículos del soporte de los fabricantes (FLUX…). Es solo consulta: desde
        aquí no se envía nada.
      </p>
      <SoporteBusqueda />
      <div className="modal-actions">
        <button type="button" className="button secondary" onClick={onClose}>Cerrar</button>
      </div>
    </ErpModalShell>
  );
}
