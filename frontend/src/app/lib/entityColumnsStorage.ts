/**
 * Sprint Filtros & Listas (PR-C) — localStorage de configuración de
 * columnas por entidad.
 *
 * Cuando hay vista activa, las columnas se persisten en la vista vía
 * `PATCH /api/entity-views/{entity}/{id}` (eso lo gestiona la pantalla
 * que monta `<EntityTable>`). Cuando no hay vista, este helper guarda
 * el "default por usuario" en localStorage para esa entidad. Patrón
 * heredado de `contactColumnsStorage.ts`, generalizado por `entityKey`.
 */

export type ColumnConfig = {
  visible: string[]; // ordered list of field keys
};

function storageKey(entity: string): string {
  return `crmbomedia_entity_columns:${entity}`;
}

export function loadColumnConfig(
  entity: string,
  fallbackVisible: string[],
): ColumnConfig {
  if (typeof window === "undefined") {
    return { visible: fallbackVisible };
  }
  try {
    const raw = window.localStorage.getItem(storageKey(entity));
    if (!raw) return { visible: fallbackVisible };
    const parsed = JSON.parse(raw) as Partial<ColumnConfig>;
    if (Array.isArray(parsed.visible) && parsed.visible.every((v) => typeof v === "string")) {
      return { visible: parsed.visible };
    }
  } catch {
    // ignore corrupted localStorage entries
  }
  return { visible: fallbackVisible };
}

export function saveColumnConfig(entity: string, config: ColumnConfig): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      storageKey(entity),
      JSON.stringify({ visible: config.visible }),
    );
  } catch {
    // localStorage may be full or disabled — fail silently
  }
}
