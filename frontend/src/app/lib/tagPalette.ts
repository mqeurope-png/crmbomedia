/**
 * Fixed colour palette for tags. Mirrors the Tailwind v3 500 shades.
 * Keep in sync with `backend/app/schemas/crm.TAG_COLOR_PALETTE` — the
 * backend validates every POST/PATCH against the same array, so a
 * value drift here surfaces as a 422 in the admin form.
 */
export type TagPaletteSwatch = {
  hex: string;
  label: string;
};

export const TAG_PALETTE: readonly TagPaletteSwatch[] = [
  { hex: "#64748b", label: "Pizarra" },
  { hex: "#6b7280", label: "Gris" },
  { hex: "#71717a", label: "Zinc" },
  { hex: "#737373", label: "Neutro" },
  { hex: "#78716c", label: "Piedra" },
  { hex: "#ef4444", label: "Rojo" },
  { hex: "#f97316", label: "Naranja" },
  { hex: "#f59e0b", label: "Ámbar" },
  { hex: "#eab308", label: "Amarillo" },
  { hex: "#84cc16", label: "Lima" },
  { hex: "#22c55e", label: "Verde" },
  { hex: "#10b981", label: "Esmeralda" },
  { hex: "#14b8a6", label: "Verde azulado" },
  { hex: "#06b6d4", label: "Cian" },
  { hex: "#0ea5e9", label: "Cielo" },
  { hex: "#3b82f6", label: "Azul" },
  { hex: "#6366f1", label: "Índigo" },
  { hex: "#8b5cf6", label: "Violeta" },
  { hex: "#a855f7", label: "Púrpura" },
  { hex: "#d946ef", label: "Fucsia" },
  { hex: "#ec4899", label: "Rosa" },
  { hex: "#f43f5e", label: "Frambuesa" },
] as const;

const PALETTE_SET = new Set(TAG_PALETTE.map((s) => s.hex.toLowerCase()));

/** True when the hex value is one of the palette swatches. Used to
 *  decide whether to show "Personalizado" for tags created before the
 *  palette landed. Comparison is case-insensitive so `#3B82F6` and
 *  `#3b82f6` both match. */
export function isPaletteColor(value: string | null | undefined): boolean {
  if (!value) return false;
  return PALETTE_SET.has(value.toLowerCase());
}
