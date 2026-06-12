/**
 * Composer Fase 2 — shared TypeScript types for the editor surface.
 *
 * Mirrors the runtime shapes that `app-main.jsx`, `app-compositor.jsx`
 * and `app-inspector.jsx` push around: `Block` is the canvas-level
 * entity (with `_sourceType` / `_sourceId` tracking provenance from
 * the seed catalog), and the `Composer*` catalog types match the
 * backend's `ComposerCatalog` payload from `/api/composer/catalog`.
 *
 * Kept snake_case on backend-mirrored types so the API client doesn't
 * have to translate names twice.
 */

export type Lang = "es" | "fr" | "de" | "en" | "nl";

export const SUPPORTED_LANGS: readonly Lang[] = ["es", "fr", "de", "en", "nl"];

export type BlockType =
  | "text"
  | "text_from_library"
  | "product_single"
  | "product_pair"
  | "product_trio"
  | "brand_strip"
  | "cta"
  | "saved_cta"
  | "image"
  | "video"
  | "freebird"
  | "pimpam_hero"
  | "pimpam_steps"
  | "composed"
  | "section_2col"
  | "section_3col"
  | "divider_line"
  | "divider_short"
  | "divider_dots";

export type BlockSourceType =
  | "prewritten"
  | "composed"
  | "composed_inner"
  | "standalone"
  | "manual";

export type BlockWidth = "full" | "wide" | "narrow";

export interface HeroCtaButton {
  text: string;
  url: string;
}

export interface PimpamStep {
  n: string;
  t: string;
  s: string;
}

/** Canvas-level block. `Partial`-ish on purpose — only the fields
 * relevant to the current `type` are populated, the rest are
 * undefined. Editors gate their inputs by `type`. */
export interface Block {
  id: string;
  type: BlockType;
  _sourceType?: BlockSourceType;
  _sourceId?: string;
  _composedSourceId?: string;
  _innerIdx?: number;
  _overrides?: Partial<Record<Lang, string | Record<string, unknown>>>;
  _richHtml?: string;
  text?: string;
  product1?: string;
  product2?: string;
  product3?: string;
  brand?: string;
  youtubeUrl?: string;
  thumbnailOverride?: string;
  heroImage?: string;
  heroImageLink?: string;
  heroTitle?: string;
  heroSubtitle?: string;
  heroBullets?: string[];
  heroCtaText?: string;
  heroCtaUrl?: string;
  heroCtaButtons?: HeroCtaButton[];
  heroBgColor?: string;
  steps?: PimpamStep[];
  stepsBgColor?: string;
  stepsBorderColor?: string;
  innerBlocks?: Block[];
  width?: BlockWidth;
  i18n?: Partial<Record<Lang, Record<string, unknown>>>;
  // CTA / image / divider extras
  url?: string;
  bgColor?: string;
  textColor?: string;
  imageUrl?: string;
  alt?: string;
  align?: "left" | "center" | "right";
  fontSize?: string;
}

// ───────────────────────────────────────────────────────────────────
// Catalog shapes — mirror /api/composer/catalog
// ───────────────────────────────────────────────────────────────────

export interface ComposerBrand {
  id: string;
  type: string;
  label: string;
  logo: string | null;
  logo_text: string | null;
  color: string;
  divider: string | null;
  logo_height: string | null;
  logo_max_width: string | null;
  visible: boolean;
  sort_order: number;
  i18n: Record<string, Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface ComposerProduct {
  id: string;
  brand_id: string;
  name: string;
  badge: string | null;
  badge_bg: string | null;
  badge_color: string | null;
  img: string;
  description: string | null;
  area: string | null;
  alt: string | null;
  feat1: string | null;
  feat2: string | null;
  price: string | null;
  link: string | null;
  accent: string | null;
  gradient: string | null;
  visible: boolean;
  sort_order: number;
  tags: string[];
  i18n: Record<string, Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface ComposerPrewrittenText {
  id: string;
  name: string;
  icon: string | null;
  brand_id: string | null;
  text: string;
  visible: boolean;
  sort_order: number;
  i18n: Record<string, Record<string, unknown>>;
}

export interface ComposerComposedBlock {
  id: string;
  title: string;
  description: string | null;
  price_range: string | null;
  color_tag: string | null;
  intro_text: string | null;
  brand_strip: string | null;
  block_type: string;
  products: string[];
  include_hero: boolean;
  include_steps: boolean;
  visible: boolean;
  sort_order: number;
  i18n: Record<string, Record<string, unknown>>;
  config: Record<string, unknown>;
  // The original Composer's composedBlocks carry inner Block[] arrays
  // for the "composed_inner" resolution path. The CRM backend stores
  // them on `config.innerBlocks` so we expose them as an optional
  // helper field for the inspector / i18n resolver.
  innerBlocks?: Block[];
  introText?: string;
}

export interface ComposerStandaloneBlock {
  id: string;
  title: string;
  description: string | null;
  icon: string | null;
  icon_bg: string | null;
  brand_id: string | null;
  section: string | null;
  block_type: string;
  config: Record<string, unknown>;
  visible: boolean;
  sort_order: number;
  i18n: Record<string, Record<string, unknown>>;
}

export interface ComposerCatalog {
  brands: ComposerBrand[];
  products: ComposerProduct[];
  prewritten_texts: ComposerPrewrittenText[];
  composed_blocks: ComposerComposedBlock[];
  standalone_blocks: ComposerStandaloneBlock[];
}

/** Adapter shape — what `lib/composer/i18n.ts` and `emailGen.ts`
 * expect (camelCase, mirrors the original Composer's `appState`). */
export interface ComposerAppState {
  brands: ComposerBrand[];
  products: ComposerProduct[];
  prewrittenTexts: ComposerPrewrittenText[];
  composedBlocks: ComposerComposedBlock[];
  standaloneBlocks: ComposerStandaloneBlock[];
}

export function toAppState(catalog: ComposerCatalog): ComposerAppState {
  return {
    brands: catalog.brands,
    products: catalog.products,
    prewrittenTexts: catalog.prewritten_texts,
    composedBlocks: catalog.composed_blocks,
    standaloneBlocks: catalog.standalone_blocks,
  };
}

// ───────────────────────────────────────────────────────────────────
// Editor store types
// ───────────────────────────────────────────────────────────────────

export type SaveStatus = "idle" | "saving" | "saved" | "error";

export interface ComposerEditorState {
  blocks: Block[];
  selectedId: string | null;
  activeLang: Lang;
  emailTitle: string;
  editingTemplateId: string | null;
  history: Block[][];
  historyIdx: number;
  saveStatus: SaveStatus;
  lastSavedAt: number | null;
  lastError: string | null;
}

export interface AddBlockSpec {
  type: BlockType;
  params?: Partial<Block>;
}

export interface SaveAsTemplatePayload {
  name: string;
  description?: string | null;
  color_class?: string | null;
  brand_id?: string | null;
  is_global?: boolean;
}

export interface ComposerActions {
  setBlocks: (blocks: Block[], opts?: { skipHistory?: boolean }) => void;
  addBlock: (spec: AddBlockSpec) => string;
  addBlockToColumn: (
    sectionId: string,
    columnIndex: number,
    spec: AddBlockSpec,
  ) => string;
  updateBlock: (id: string, patch: Partial<Block>) => void;
  deleteBlock: (id: string) => void;
  reorderBlocks: (fromIdx: number, toIdx: number) => void;
  duplicateBlock: (id: string) => void;
  ungroupBlock: (id: string) => void;
  clearCanvas: () => void;
  setSelected: (id: string | null) => void;
  setLang: (lang: Lang) => void;
  setEmailTitle: (title: string) => void;
  setEditingTemplateId: (id: string | null) => void;
  undo: () => void;
  redo: () => void;
  setSaveStatus: (status: SaveStatus, error?: string | null) => void;
  setLastSavedAt: (ts: number | null) => void;
}
