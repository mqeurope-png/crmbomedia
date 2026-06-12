/**
 * Composer Fase 2 — typed Block model rebased to the literal field
 * names from `bomedia-v4/app-data.jsx` + `app-main.jsx`.
 *
 * Step 0 of the post-mortem plan: stop carrying my own renamed shape
 * (imageUrl/bgColor/textColor/innerBlocks/divider_line/section_2col)
 * and align to the original (`src`/`bg`/`color`/`columns[].blocks`/
 * `divider+style`/`section+layout`). Every consumer downstream reads
 * the same names the v5o code reads, so previewing / renderering /
 * email-gen stop needing per-field translation.
 *
 * Backend untouched: SQL columns store opaque JSON. The shape lives
 * client-side.
 */

export type Lang = "es" | "fr" | "de" | "en" | "nl";

export const SUPPORTED_LANGS: readonly Lang[] = ["es", "fr", "de", "en", "nl"];

/** The canonical block types the v5o factory and renderer use. Note:
 * `section_2col` / `section_3col` from the palette materialise as
 * `{type:'section', layout:'2col'|'3col'}`; `divider_line/short/dots`
 * materialise as `{type:'divider', style:...}`. Those palette names
 * are not block types — they're factory inputs. */
export type BlockType =
  | "text"
  | "text_from_library"
  | "product"
  | "product_single"
  | "product_pair"
  | "product_trio"
  | "brand_strip"
  | "brand_artisjet"
  | "brand_mbo"
  | "brand_pimpam"
  | "brand_smartjet"
  | "brand_flux"
  | "cta"
  | "saved_cta"
  | "image"
  | "video"
  | "freebird"
  | "pimpam_hero"
  | "product_hero"
  | "hero"
  | "pimpam_steps"
  | "composed"
  | "section"
  | "divider";

export type BlockSourceType =
  | "prewritten"
  | "composed"
  | "composed_inner"
  | "standalone"
  | "manual";

export type DividerStyle = "line" | "short" | "dots";
export type SectionLayout = "2col" | "3col";

export interface HeroCtaButton {
  text: string;
  url: string;
  bg?: string;
  color?: string;
}

export interface PimpamStep {
  n: string;
  t: string;
  s: string;
}

export interface SectionColumn {
  blocks: Block[];
}

// ───────────────────────────────────────────────────────────────────
// Block — every field optional except id+type. Each type populates
// its own subset. Names + shapes match the v5o source.
// ───────────────────────────────────────────────────────────────────

export interface Block {
  id: string;
  type: BlockType;

  // Source attribution (set when the block is materialised from a
  // catalog row — prewritten text, composed block, standalone hero,
  // etc.).
  _sourceType?: BlockSourceType;
  _sourceId?: string;
  _composedSourceId?: string;
  _innerIdx?: number;

  // Text-specific.
  /** Per-language plain-text overrides — the v5o canonical key. */
  overridesByLang?: Partial<Record<Lang, string>>;
  /** Legacy single-language override (Spanish only). The factory
   * still emits it for `text-blank`; the renderer prefers
   * `overridesByLang[es]` when both exist. */
  overrideText?: string;
  /** Per-language rich HTML edited via the inline RichTextEditor. */
  _richHtmlByLang?: Partial<Record<Lang, string>>;
  /** Legacy single-string rich HTML (Spanish only). */
  _richHtml?: string;
  /** Library reference for `type === "text"` blocks sourced from a
   * prewritten catalog row. */
  textId?: string;
  fontSize?: number | string;
  align?: "left" | "center" | "right";

  // Product blocks.
  productId?: string;
  product1?: string;
  product2?: string;
  product3?: string;
  /** Per-language inline overrides on product fields (name / desc /
   * price / feat1 / feat2 …). v5o stores them under `overrides`,
   * keyed by lang then by field. */
  overrides?: Partial<Record<Lang, Record<string, string>>>;
  showPrice?: boolean;
  showSpecs?: boolean;
  showCta?: boolean;
  ctaText?: string;

  // Brand strip.
  brand?: string;

  // Composed block (catalog reference).
  composedId?: string;

  // Standalone block (catalog reference).
  standaloneId?: string;

  // Hero (pimpam_hero / product_hero / hero — all unified at render).
  heroImage?: string;
  heroImageLink?: string;
  heroTitle?: string;
  heroSubtitle?: string;
  heroBullets?: string[];
  heroCtaText?: string;
  heroCtaUrl?: string;
  heroCtaButtons?: HeroCtaButton[];
  heroCtaColor?: string;
  heroBgColor?: string;

  // Pimpam steps.
  steps?: PimpamStep[];
  stepsBgColor?: string;
  stepsBorderColor?: string;

  // Image block.
  src?: string;
  alt?: string;
  link?: string;
  widthPct?: number;

  // CTA block.
  title?: string;
  subtitle?: string;
  bullets?: string[];
  text?: string;
  url?: string;
  bg?: string;
  color?: string;
  panelBg?: string;
  panelBorder?: string;
  /** Optional saved-CTA reference (matches the spec arg the original
   * carries through `_ctaSourceId`). */
  _ctaSourceId?: string;

  // Divider.
  style?: DividerStyle;
  paddingV?: number;

  // Section (multi-column container).
  layout?: SectionLayout;
  columns?: SectionColumn[];

  // Per-block layout shaping (v5o canvas adds these inline overrides).
  blockAlign?: "left" | "center" | "right";

  // Per-lang i18n stash (heroes + composed blocks merge from here).
  i18n?: Partial<Record<Lang, Record<string, unknown>>>;
}

// ───────────────────────────────────────────────────────────────────
// Catalog shapes — mirror /api/composer/catalog payload.
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

/** camelCase adapter the v5o code reads as `appState`. */
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

/** Palette factory spec — what the sidebar / CMDK / column picker
 * emit. Mirrors the args `app-main.jsx`'s `addBlock(spec)` reads
 * (productId / textId / composedId / standaloneId / templateId). */
export interface AddBlockSpec {
  /** Palette type — can be a real BlockType OR a palette-only key
   * like `section_2col` / `divider_line` / `text-blank` that
   * createBlock() translates. */
  type: string;
  productId?: string;
  textId?: string;
  composedId?: string;
  standaloneId?: string;
  templateId?: string;
  _imgUrl?: string;
  _ctaSourceId?: string;
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
  addBlock: (
    spec: AddBlockSpec,
    opts?: { into?: { sectionId: string; columnIdx: number } },
  ) => string | null;
  addBlockToColumn: (
    sectionId: string,
    columnIndex: number,
    spec: AddBlockSpec,
  ) => string | null;
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
  /** Bind the live catalog to the store so addBlock can materialise
   * hero / product / brand fields from standalone configs. */
  setCatalog: (catalog: ComposerCatalog | null) => void;
}
