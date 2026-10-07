"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Comportamiento común de TODOS los modales (ERP y CRM):
 *
 *  - **Esc** cierra solo el modal de ARRIBA (pila de modales abiertos: el
 *    compositor con «Programar envío», la preparación SAT con «Reportar
 *    problema», el detalle FACTUSOL con sus confirmaciones…). Escucha en
 *    `window`, así que cualquier control de dentro que ya haya gestionado su
 *    Esc con `preventDefault()` (un desplegable, un buscador) tiene prioridad.
 *    No actúa con el modal ocupado (`disabled`) ni dentro de los menús y
 *    diálogos de TinyMCE (`.tox`).
 *  - **Clic fuera**: cierra con el `mousedown` sobre la capa misma (no con el
 *    `click`, para no cerrar al soltar fuera una selección de texto que
 *    empezó dentro).
 *  - **Foco**: al abrir, entra en el diálogo (al ✕) salvo que ya esté dentro
 *    (un campo con `autoFocus`); al cerrar vuelve al elemento que lo tenía
 *    antes de abrir, si sigue en la página.
 *  - `confirmClose`: si devuelve `false`, no se cierra (cambios sin guardar).
 *
 *  `onClose` se lee de una ref: pasar una flecha en línea no reengancha nada
 *  ni roba el foco en cada render del padre. */

type StackEntry = {
  requestClose: () => void;
  isDisabled: () => boolean;
};

const openModals: StackEntry[] = [];

function onWindowKeyDown(event: KeyboardEvent) {
  if (event.key !== "Escape" && event.key !== "Esc") return;
  if (event.defaultPrevented || event.isComposing) return;
  const target = event.target;
  // Menús y diálogos de TinyMCE: su Esc es suyo.
  if (target instanceof Element && target.closest(".tox")) return;
  const top = openModals[openModals.length - 1];
  if (!top) return;
  // El Esc es del modal de arriba aunque esté ocupado: nunca cae al de debajo.
  event.preventDefault();
  if (top.isDisabled()) return;
  top.requestClose();
}

function pushModal(entry: StackEntry) {
  if (openModals.length === 0) window.addEventListener("keydown", onWindowKeyDown);
  openModals.push(entry);
}

function removeModal(entry: StackEntry) {
  const index = openModals.lastIndexOf(entry);
  if (index !== -1) openModals.splice(index, 1);
  if (openModals.length === 0) window.removeEventListener("keydown", onWindowKeyDown);
}

function currentFocus(): Element | null {
  return typeof document === "undefined" ? null : document.activeElement;
}

export const UNSAVED_CHANGES_MESSAGE = "Hay cambios sin guardar. ¿Cerrar sin guardar?";

export type ModalBehaviourOptions = {
  /** Por defecto `true` (modales que solo se montan abiertos). */
  open?: boolean;
  /** Lo mismo que hace el botón Cancelar / Cerrar del modal. */
  onClose: () => void;
  /** Ocupado (guardando, enviando…): Esc y clic fuera no hacen nada. */
  disabled?: boolean;
  /** Devuelve `false` para no cerrar (p. ej. cambios sin guardar). */
  confirmClose?: () => boolean;
};

export function useModalBehaviour<T extends HTMLElement = HTMLDivElement>({
  open = true,
  onClose,
  disabled = false,
  confirmClose,
}: ModalBehaviourOptions) {
  const overlayRef = useRef<T>(null);
  const onCloseRef = useRef(onClose);
  const disabledRef = useRef(disabled);
  const confirmCloseRef = useRef(confirmClose);
  useEffect(() => {
    onCloseRef.current = onClose;
    disabledRef.current = disabled;
    confirmCloseRef.current = confirmClose;
  });

  // Quién tenía el foco ANTES de abrir: se lee al renderizar la apertura,
  // antes de que un `autoFocus` de dentro lo mueva.
  const [wasOpen, setWasOpen] = useState(open);
  const [returnFocus, setReturnFocus] = useState<Element | null>(() =>
    open ? currentFocus() : null,
  );
  if (open !== wasOpen) {
    setWasOpen(open);
    setReturnFocus(open ? currentFocus() : null);
  }

  const requestClose = useCallback(() => {
    if (disabledRef.current) return;
    const confirm = confirmCloseRef.current;
    if (confirm && !confirm()) return;
    onCloseRef.current();
  }, []);

  // Pila de modales abiertos (Esc al de arriba).
  useEffect(() => {
    if (!open) return;
    const entry: StackEntry = {
      requestClose,
      isDisabled: () => disabledRef.current,
    };
    pushModal(entry);
    return () => removeModal(entry);
  }, [open, requestClose]);

  // Foco: entra al abrir y vuelve al cerrar.
  useEffect(() => {
    if (!open) return;
    const overlay = overlayRef.current;
    if (overlay && !overlay.contains(document.activeElement)) {
      const target = overlay.querySelector<HTMLElement>(".modal-close:not(:disabled)");
      target?.focus({ preventScroll: true });
    }
    return () => {
      const active = document.activeElement;
      const focusLost = !active || active === document.body || (overlay?.contains(active) ?? false);
      if (focusLost && returnFocus instanceof HTMLElement && returnFocus.isConnected) {
        returnFocus.focus();
      }
    };
  }, [open, returnFocus]);

  const onOverlayMouseDown = useCallback(
    (event: React.MouseEvent<HTMLElement>) => {
      // Solo la capa misma: no un clic dentro del diálogo ni en un modal anidado.
      if (event.target !== event.currentTarget || event.button !== 0) return;
      requestClose();
    },
    [requestClose],
  );

  return {
    /** Para la capa (`.modal-overlay`): ref + cierre al pulsar fuera. */
    overlayProps: { ref: overlayRef, onMouseDown: onOverlayMouseDown },
    overlayRef,
    /** Cierre «educado»: respeta `disabled` y `confirmClose` (para el ✕). */
    requestClose,
  };
}
