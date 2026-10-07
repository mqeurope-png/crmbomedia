"use client";

import type { ReactNode } from "react";

import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";

/** Molde plano del ERP para los modales que se pintan EN LÍNEA dentro de un
 *  panel o una página (`{x ? <div className="modal-overlay">…</div> : null}`):
 *  capa + `.modal-dialog.erp-modal` + título + ✕, con Esc, clic fuera y foco
 *  de `useModalBehaviour`. El DOM es el mismo que tenían a mano. */
export function ErpModalShell({
  label,
  title,
  onClose,
  disabled = false,
  dialogClassName = "erp-modal",
  children,
}: {
  /** `aria-label` de la capa (`role="dialog"`). */
  label: string;
  title: ReactNode;
  /** Lo mismo que el botón Cancelar / Cerrar del modal. */
  onClose: () => void;
  /** Ocupado: el ✕, Esc y el clic fuera no cierran (como el Cancelar). */
  disabled?: boolean;
  dialogClassName?: string;
  children: ReactNode;
}) {
  const { overlayProps, requestClose } = useModalBehaviour({ onClose, disabled });
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={label} {...overlayProps}>
      <div className={`modal-dialog ${dialogClassName}`}>
        <h2>{title}</h2>
        <ModalCloseButton onClose={requestClose} disabled={disabled} />
        {children}
      </div>
    </div>
  );
}
