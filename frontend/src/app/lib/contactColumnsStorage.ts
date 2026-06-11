/** Persistence layer for the contacts table column configuration.
 *
 * Two-tier strategy that mirrors how the operator thinks about views:
 *
 * - When there is an ACTIVE saved view, the column config lives ON
 *   THE VIEW (`columns_json` PATCHed through the existing
 *   `/api/contact-views/{id}` endpoint). Every member of the team
 *   sharing the view sees the same columns.
 * - When there is NO active view (the "Todos los contactos" tab),
 *   the column config falls back to LOCALSTORAGE so a reload still
 *   honours the operator's pick. Keyed per-browser, no sync needed.
 *
 * The page wires `applyColumns` to both, so a user that tweaks columns
 * while on the default tab keeps them; switching to a saved view loads
 * that view's columns and stops the localStorage sync until the user
 * deactivates the view again.
 */
import type { ContactColumnKey } from "./contactColumns";

const LS_KEY = "contacts:default-columns";

export type StoredColumns = {
  order: ContactColumnKey[];
  visible: ContactColumnKey[];
};

export function loadLocalColumns(): StoredColumns | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const order = Array.isArray(parsed.order) ? parsed.order : null;
    const visible = Array.isArray(parsed.visible) ? parsed.visible : null;
    if (!order || !visible) return null;
    return {
      order: order.map(String) as ContactColumnKey[],
      visible: visible.map(String) as ContactColumnKey[],
    };
  } catch {
    return null;
  }
}

export function saveLocalColumns(value: StoredColumns): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      LS_KEY,
      JSON.stringify({ order: value.order, visible: value.visible }),
    );
  } catch {
    // Quota / privacy mode — ignore; the operator still has the
    // in-memory state for the rest of the session.
  }
}

export function clearLocalColumns(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(LS_KEY);
  } catch {
    /* ignore */
  }
}
