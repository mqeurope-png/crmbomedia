"use client";

/** ✕ de cierre de un modal. Hace lo mismo que su botón Cancelar / Cerrar y
 *  se desactiva con él. `placement="corner"` (por defecto) es para los modales
 *  sin `.modal-header` (molde plano del ERP, tarjetas del CRM): se pone justo
 *  DETRÁS del título y el CSS lo dibuja en la esquina superior derecha (el
 *  título sigue siendo el primer hijo, del que cuelga la cabecera pegajosa).
 *  `placement="header"`, dentro de un `.modal-header`. */
export function ModalCloseButton({
  onClose,
  disabled = false,
  placement = "corner",
}: {
  onClose: () => void;
  disabled?: boolean;
  placement?: "corner" | "header";
}) {
  return (
    <button
      type="button"
      className={placement === "corner" ? "modal-close modal-close--corner" : "modal-close"}
      aria-label="Cerrar"
      onClick={onClose}
      disabled={disabled}
    >
      ×
    </button>
  );
}
