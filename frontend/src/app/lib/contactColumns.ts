/**
 * Metadata for every column the contacts table can render. The
 * configurator picks `visible` and `order` from this list; the table
 * itself uses `key` to look up the render hint when displaying a row.
 *
 * `key="name"` is always-visible — the configurator disables its
 * checkbox so an operator can't end up with a nameless table.
 */
export type ContactColumnKey =
  | "name"
  | "email"
  | "phone"
  | "tags"
  | "origin"
  | "commercial_status"
  | "marketing_consent"
  | "lead_score"
  | "is_active"
  | "created_at"
  | "updated_at"
  | "external_data_freshness"
  | "last_external_refresh_at";

export type ContactColumnDef = {
  key: ContactColumnKey;
  label: string;
  alwaysVisible?: true;
  /** Default column width in pixels, used when no view-level width is set. */
  defaultWidth?: number;
};

export const CONTACT_COLUMNS: readonly ContactColumnDef[] = [
  { key: "name", label: "Nombre", alwaysVisible: true, defaultWidth: 220 },
  { key: "email", label: "Email", defaultWidth: 240 },
  { key: "phone", label: "Teléfono", defaultWidth: 160 },
  { key: "tags", label: "Tags", defaultWidth: 200 },
  { key: "origin", label: "Origen", defaultWidth: 140 },
  { key: "commercial_status", label: "Estado comercial", defaultWidth: 160 },
  { key: "marketing_consent", label: "Consentimiento", defaultWidth: 160 },
  { key: "lead_score", label: "Lead score", defaultWidth: 110 },
  { key: "is_active", label: "Activo", defaultWidth: 100 },
  { key: "created_at", label: "Creado", defaultWidth: 130 },
  { key: "updated_at", label: "Actualizado", defaultWidth: 130 },
  { key: "external_data_freshness", label: "Frescura", defaultWidth: 130 },
  { key: "last_external_refresh_at", label: "Última actualización ext.", defaultWidth: 170 },
];

export const DEFAULT_VISIBLE_COLUMNS: ContactColumnKey[] = [
  "name",
  "email",
  "phone",
  "tags",
  "origin",
  "commercial_status",
  "marketing_consent",
  "external_data_freshness",
  "updated_at",
];

export const ALL_COLUMN_KEYS: ContactColumnKey[] = CONTACT_COLUMNS.map(
  (c) => c.key,
);

export function findColumn(key: string): ContactColumnDef | undefined {
  return CONTACT_COLUMNS.find((column) => column.key === key);
}
