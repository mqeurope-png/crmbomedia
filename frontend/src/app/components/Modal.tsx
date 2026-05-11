"use client";

import { useCallback, useEffect, useRef } from "react";

type ModalProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  /** Width preset; the modal defaults to ~600px which fits a tall form. */
  size?: "small" | "default";
};

/**
 * Centred dialog with darkened overlay. Closes on ESC, click outside the
 * panel, or the × button. Locks body scroll while open so the underlying
 * page can't be scrolled behind the modal.
 */
export function Modal({ open, onClose, title, children, size = "default" }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  // ESC to close + body scroll lock while open.
  useEffect(() => {
    if (!open) return;
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    }
    document.addEventListener("keydown", handleKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Move keyboard focus to the close button so screen readers and
    // keyboard users land inside the dialog immediately.
    closeButtonRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", handleKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  const onOverlayMouseDown = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      // Close only when the press starts on the overlay itself, not when
      // dragging text inside the dialog and releasing outside.
      if (event.target === event.currentTarget) {
        onClose();
      }
    },
    [onClose],
  );

  if (!open) return null;

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onMouseDown={onOverlayMouseDown}
    >
      <div
        className={`modal-dialog ${size === "small" ? "small" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        ref={dialogRef}
      >
        <header className="modal-header">
          <h2 id="modal-title">{title}</h2>
          <button
            type="button"
            className="modal-close"
            aria-label="Cerrar"
            ref={closeButtonRef}
            onClick={onClose}
          >
            ×
          </button>
        </header>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
