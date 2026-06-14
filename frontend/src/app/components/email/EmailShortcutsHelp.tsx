"use client";

import { Modal } from "../Modal";
import { EMAIL_SHORTCUTS } from "../../lib/useEmailKeyboardShortcuts";

type Props = {
  open: boolean;
  onClose: () => void;
};

export function EmailShortcutsHelp({ open, onClose }: Props) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Atajos de teclado"
      size="small"
    >
      <p className="muted small">
        Funcionan en /emails cuando el foco no está en un campo de texto.
      </p>
      <ul className="email-shortcuts-list">
        {EMAIL_SHORTCUTS.map((s) => (
          <li key={s.keys}>
            <kbd>{s.keys}</kbd>
            <span>{s.label}</span>
          </li>
        ))}
      </ul>
    </Modal>
  );
}
