"use client";

import { useEffect } from "react";

import { ModalCloseButton } from "./ModalCloseButton";
import { useModalBehaviour } from "./useModalBehaviour";

type ModalProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  /** Width preset; the modal defaults to ~600px which fits a tall form. */
  size?: "small" | "default";
};

/**
 * Centred dialog with darkened overlay. Closes on ESC (only the topmost
 * modal), click outside the panel, or the × button, and gives focus back to
 * whatever had it before opening (see `useModalBehaviour`). Locks body
 * scroll while open so the underlying page can't be scrolled behind it.
 */
export function Modal({ open, onClose, title, children, size = "default" }: ModalProps) {
  const { overlayProps, requestClose } = useModalBehaviour({ open, onClose });

  // Body scroll lock while open.
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="modal-overlay" role="presentation" {...overlayProps}>
      <div
        className={`modal-dialog ${size === "small" ? "small" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
      >
        <header className="modal-header">
          <h2 id="modal-title">{title}</h2>
          <ModalCloseButton onClose={requestClose} placement="header" />
        </header>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
