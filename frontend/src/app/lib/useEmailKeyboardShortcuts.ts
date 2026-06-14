"use client";

import { useEffect, useRef } from "react";

/** Gmail-style keyboard handler. Single-key actions fire on the next
 *  keydown; chord prefixes (`g` then `i`, `g` then `s`, …) wait
 *  GMAIL_CHORD_TIMEOUT_MS for the second key before resetting. */
const GMAIL_CHORD_TIMEOUT_MS = 1000;

export type EmailShortcutHandlers = {
  /** Move the cursor in the list. */
  onNext?: () => void;
  onPrev?: () => void;
  /** Open the focused row / current thread. */
  onOpen?: () => void;
  /** Archive the focused/open thread (or selected ones if any). */
  onArchive?: () => void;
  /** Move to papelera. */
  onTrash?: () => void;
  /** Star toggle. */
  onStar?: () => void;
  /** Open the reply composer for the current thread. */
  onReply?: () => void;
  /** Open the labels picker. */
  onLabel?: () => void;
  /** Toggle the snooze picker. */
  onSnooze?: () => void;
  /** Mark unread (`u` mirrors Gmail's mark-as-unread). */
  onMarkUnread?: () => void;
  /** Show the help overlay. */
  onHelp?: () => void;
  /** Chord navigation. */
  onGoInbox?: () => void;
  onGoStarred?: () => void;
  onGoArchived?: () => void;
  onGoTrash?: () => void;
};

/** Returns true if the event target is an editable surface (input,
 *  textarea, contenteditable). The hook skips every key when this
 *  is true so typing in the search box doesn't trigger archive. */
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return false;
}

export function useEmailKeyboardShortcuts(
  handlers: EmailShortcutHandlers,
  enabled: boolean = true,
): void {
  // Refs keep us re-running just the listener attach, not the
  // handler binding — a parent that re-renders every list refetch
  // would otherwise tear down + rebind on every cycle.
  const ref = useRef(handlers);
  ref.current = handlers;

  useEffect(() => {
    if (!enabled) return;

    let gChordTimer: number | null = null;
    let waitingForGChord = false;

    function fireOnce(fn: undefined | (() => void)): void {
      if (fn) fn();
    }

    function clearChord() {
      waitingForGChord = false;
      if (gChordTimer !== null) {
        window.clearTimeout(gChordTimer);
        gChordTimer = null;
      }
    }

    function onKey(e: KeyboardEvent) {
      if (isEditableTarget(e.target)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const h = ref.current;
      const k = e.key;

      // Chord: "g" then a destination letter.
      if (waitingForGChord) {
        if (k === "i") fireOnce(h.onGoInbox);
        else if (k === "s") fireOnce(h.onGoStarred);
        else if (k === "a") fireOnce(h.onGoArchived);
        else if (k === "t") fireOnce(h.onGoTrash);
        clearChord();
        e.preventDefault();
        return;
      }

      if (k === "g") {
        waitingForGChord = true;
        gChordTimer = window.setTimeout(clearChord, GMAIL_CHORD_TIMEOUT_MS);
        e.preventDefault();
        return;
      }

      // Single-key actions. The mapping mirrors Gmail's defaults so
      // muscle memory transfers; `?` is the canonical help shortcut.
      switch (k) {
        case "j":
          fireOnce(h.onNext);
          break;
        case "k":
          fireOnce(h.onPrev);
          break;
        case "Enter":
        case "o":
          fireOnce(h.onOpen);
          break;
        case "e":
          fireOnce(h.onArchive);
          break;
        case "#":
        case "Delete":
          fireOnce(h.onTrash);
          break;
        case "s":
          fireOnce(h.onStar);
          break;
        case "r":
          fireOnce(h.onReply);
          break;
        case "l":
          fireOnce(h.onLabel);
          break;
        case "b":
          fireOnce(h.onSnooze);
          break;
        case "u":
          fireOnce(h.onMarkUnread);
          break;
        case "?":
          fireOnce(h.onHelp);
          break;
        default:
          return;
      }
      e.preventDefault();
    }

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      clearChord();
    };
  }, [enabled]);
}

/** Static reference for the help overlay so the same list is the
 *  source of truth in code + UI. */
export const EMAIL_SHORTCUTS: { keys: string; label: string }[] = [
  { keys: "j / k", label: "Siguiente / anterior" },
  { keys: "Enter · o", label: "Abrir hilo" },
  { keys: "e", label: "Archivar" },
  { keys: "# · Supr", label: "Mover a papelera" },
  { keys: "s", label: "Marcar / quitar estrella" },
  { keys: "r", label: "Responder" },
  { keys: "l", label: "Etiquetar" },
  { keys: "b", label: "Posponer (snooze)" },
  { keys: "u", label: "Marcar como no leído" },
  { keys: "g i", label: "Ir a Bandeja" },
  { keys: "g s", label: "Ir a Estrellados" },
  { keys: "g a", label: "Ir a Archivados" },
  { keys: "g t", label: "Ir a Papelera" },
  { keys: "?", label: "Mostrar esta ayuda" },
];
