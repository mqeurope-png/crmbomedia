"use client";

import { Modal } from "./Modal";

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

/**
 * Small modal used for irreversible operations (delete account). Uses the
 * shared `Modal` shell so ESC / overlay click / scroll lock behave the
 * same way as the bigger create/edit dialog.
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Eliminar",
  cancelLabel = "Cancelar",
  destructive = true,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal open={open} onClose={onCancel} title={title} size="small">
      <p>{message}</p>
      <div className="modal-footer">
        <button type="button" className="button secondary" onClick={onCancel}>
          {cancelLabel}
        </button>
        <button
          type="button"
          className={`button ${destructive ? "danger" : ""}`}
          onClick={onConfirm}
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
