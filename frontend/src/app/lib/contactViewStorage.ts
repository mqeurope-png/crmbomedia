/**
 * localStorage fallback for the contacts list configuration.
 *
 * Used when the operator hasn't saved a `contact_views` row yet: their
 * column/sort/filter choices persist locally so the next visit lands
 * back in the same shape. As soon as they save a real view this
 * storage gets cleared — every subsequent change flows through the
 * backend.
 */
import type {
  SavedViewColumns,
  SavedViewFilters,
  SavedViewSort,
} from "./api";

const STORAGE_KEY = "crmbo.contacts.unsaved-view.v1";

export type LocalViewConfig = {
  filters: SavedViewFilters;
  columns: SavedViewColumns;
  sort: SavedViewSort;
};

export function loadLocalConfig(): LocalViewConfig | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as LocalViewConfig;
  } catch {
    return null;
  }
}

export function saveLocalConfig(config: LocalViewConfig): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
}

export function clearLocalConfig(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
}
